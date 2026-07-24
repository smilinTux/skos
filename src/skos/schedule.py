"""skos scheduler-as-code.

The skos gtd-ingest / observability pipeline (the ~12 `sk-cron-run.sh`-wrapped jobs
on the primary node) is declared in ``deploy/schedule/jobs.yaml`` and rendered into
the user crontab by ``skos schedule install``. This module owns:

* loading + validating the manifest (unique job names, well-formed 5-field cron
  schedules, referenced runner script present),
* rendering the manifest to crontab lines (portable ``$HOME`` form, or host-expanded
  for install), never emitting secret VALUES (only the ``$NAME`` reference unless a
  resolved env map is passed in),
* diffing the desired schedule against the live crontab (keyed by job name, secret
  value ignored), and
* installing the desired schedule into a marked, idempotently-replaced crontab block.

No secret value is ever read from or written to the repo. Secret values are resolved
at install time from the scheduler env file (``~/.skcapstone/skos-schedule.env``).
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Managed-block markers. `install` replaces everything between them idempotently and
# leaves the rest of the user crontab (personal jobs) untouched.
BLOCK_BEGIN = "# >>> skos schedule (managed by `skos schedule install`) - do not edit by hand >>>"
BLOCK_END = "# <<< skos schedule (managed) <<<"

# Default location of the not-committed env file holding secret VALUES.
DEFAULT_ENV_FILE = "~/.skcapstone/skos-schedule.env"

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FIELD_RANGES = {
    0: (0, 59),   # minute
    1: (0, 23),   # hour
    2: (1, 31),   # day-of-month
    3: (1, 12),   # month
    4: (0, 7),    # day-of-week (0 and 7 = Sunday)
}


class ScheduleError(ValueError):
    """Raised when the schedule manifest is malformed or internally inconsistent."""


@dataclass
class Job:
    name: str
    schedule: str
    command: str
    log: str


@dataclass
class Schedule:
    runner: str
    path: str
    secret_env: list[str]
    jobs: list[Job] = field(default_factory=list)


def repo_root() -> Path:
    """Repo root (three parents up from src/skos/schedule.py)."""
    return Path(__file__).resolve().parents[2]


def default_manifest() -> Path:
    return repo_root() / "deploy" / "schedule" / "jobs.yaml"


# --------------------------------------------------------------------------- load

def _validate_cron_field(field_value: str, idx: int, job: str) -> None:
    lo, hi = _FIELD_RANGES[idx]
    for part in field_value.split(","):
        if not part:
            raise ScheduleError(f"job {job!r}: empty term in cron field {idx} ({field_value!r})")
        step_split = part.split("/")
        if len(step_split) > 2:
            raise ScheduleError(f"job {job!r}: bad step in cron field {idx} ({part!r})")
        base = step_split[0]
        if len(step_split) == 2:
            step = step_split[1]
            if not step.isdigit() or int(step) < 1:
                raise ScheduleError(f"job {job!r}: bad step value in cron field {idx} ({part!r})")
        if base == "*":
            continue
        # range a-b or single value
        for endpoint in base.split("-"):
            if not endpoint.isdigit():
                raise ScheduleError(f"job {job!r}: non-numeric cron field {idx} ({part!r})")
            v = int(endpoint)
            if not (lo <= v <= hi):
                raise ScheduleError(
                    f"job {job!r}: cron field {idx} value {v} out of range {lo}-{hi} ({part!r})"
                )


def validate_schedule(text: str, job: str) -> None:
    fields = text.split()
    if len(fields) != 5:
        raise ScheduleError(
            f"job {job!r}: schedule must have 5 fields, got {len(fields)} ({text!r})"
        )
    for idx, fld in enumerate(fields):
        _validate_cron_field(fld, idx, job)


def load(path: str | os.PathLike | None = None) -> Schedule:
    """Load, parse, and validate the schedule manifest.

    Raises ScheduleError on any malformed / internally-inconsistent manifest:
    missing keys, duplicate job names, invalid cron schedules, or a runner script
    that does not exist in the repo.
    """
    manifest = Path(path) if path else default_manifest()
    if not manifest.exists():
        raise ScheduleError(f"schedule manifest not found: {manifest}")
    data = yaml.safe_load(manifest.read_text()) or {}

    if data.get("version") != 1:
        raise ScheduleError(f"unsupported schedule manifest version: {data.get('version')!r}")

    defaults = data.get("defaults") or {}
    runner = defaults.get("runner")
    path_str = defaults.get("path")
    secret_env = list(defaults.get("secret_env") or [])
    if not runner:
        raise ScheduleError("defaults.runner is required")
    if not path_str:
        raise ScheduleError("defaults.path is required")

    raw_jobs = data.get("jobs") or []
    if not raw_jobs:
        raise ScheduleError("manifest declares no jobs")

    jobs: list[Job] = []
    seen: set[str] = set()
    for i, rj in enumerate(raw_jobs):
        for key in ("name", "schedule", "command", "log"):
            if not rj.get(key):
                raise ScheduleError(f"job #{i}: missing required key {key!r}")
        name = rj["name"]
        if name in seen:
            raise ScheduleError(f"duplicate job name: {name!r}")
        seen.add(name)
        validate_schedule(rj["schedule"], name)
        jobs.append(Job(name=name, schedule=rj["schedule"], command=rj["command"], log=rj["log"]))

    sched = Schedule(runner=runner, path=path_str, secret_env=secret_env, jobs=jobs)
    return sched


def runner_path_in_repo(sched: Schedule) -> Path:
    """Resolve the runner to a concrete path inside THIS repo checkout (for the
    'referenced command exists' consistency check), independent of $SKOS_REPO."""
    rel = sched.runner.replace("$SKOS_REPO/", "").replace("${SKOS_REPO}/", "")
    return repo_root() / rel


def check_runner_exists(sched: Schedule) -> None:
    rp = runner_path_in_repo(sched)
    if not rp.exists():
        raise ScheduleError(f"runner script referenced by manifest not found in repo: {rp}")


# ------------------------------------------------------------------------- render

def _expand(value: str, *, home: str, repo: str) -> str:
    out = value.replace("$SKOS_REPO", repo).replace("${SKOS_REPO}", repo)
    out = out.replace("$HOME", home).replace("${HOME}", home)
    return out


def render_lines(
    sched: Schedule,
    *,
    expand: bool = False,
    home: str | None = None,
    repo: str | None = None,
    secrets: dict[str, str] | None = None,
) -> list[str]:
    """Render the schedule to crontab job lines.

    expand=False (default): portable form with literal ``$HOME`` / ``$SKOS_REPO`` and
    the secret as a ``$NAME`` reference - safe to print / commit / show.
    expand=True: host-concrete form for install. ``secrets`` (name->value) supplies
    the secret VALUES; any name not in ``secrets`` stays as a ``$NAME`` reference so a
    value is never fabricated.
    """
    home = home or os.path.expanduser("~")
    repo = repo or os.environ.get("SKOS_REPO", os.path.join(home, "clawd", "skos"))
    secrets = secrets or {}

    def maybe(v: str) -> str:
        return _expand(v, home=home, repo=repo) if expand else v

    lines: list[str] = []
    for job in sched.jobs:
        env_tokens = []
        for name in sched.secret_env:
            if expand and name in secrets:
                env_tokens.append(f"{name}={secrets[name]}")
            else:
                env_tokens.append(f"{name}=${name}")
        env_prefix = " ".join(env_tokens + [f"PATH={maybe(sched.path)}"])
        runner = maybe(sched.runner)
        command = maybe(job.command)
        log = maybe(job.log)
        lines.append(
            f"{job.schedule} {env_prefix} {runner} {job.name} {command} >> {log} 2>&1"
        )
    return lines


def render_block(sched: Schedule, **kw) -> str:
    """Render the full managed block (markers + lines) as text."""
    body = "\n".join(render_lines(sched, **kw))
    return f"{BLOCK_BEGIN}\n{body}\n{BLOCK_END}"


# --------------------------------------------------------------------------- diff

def _signature(line: str) -> tuple[str, str] | None:
    """Return (job_name, normalized-command) for a sk-cron-run.sh crontab line, or
    None if the line is not one of our managed jobs. Strips the leading env
    assignments (so the secret VALUE is ignored) and collapses whitespace, then keys
    on the job name (the token right after sk-cron-run.sh)."""
    line = line.strip()
    if not line or line.startswith("#") or "sk-cron-run.sh" not in line:
        return None
    fields = line.split()
    if len(fields) < 6:
        return None
    rest = fields[5:]  # drop the 5 cron time fields
    # strip leading VAR=... assignments (PATH, secrets)
    while rest and _ENV_ASSIGN_RE.match(rest[0]):
        rest.pop(0)
    # rest now: <runner> <job-name> <command...>
    if len(rest) < 2:
        return None
    try:
        runner_idx = next(i for i, t in enumerate(rest) if t.endswith("sk-cron-run.sh"))
    except StopIteration:
        return None
    if runner_idx + 1 >= len(rest):
        return None
    name = rest[runner_idx + 1]
    schedule = " ".join(fields[:5])
    command = " ".join(rest[runner_idx + 2:])
    return name, f"{schedule} || {command}"


def parse_crontab(text: str) -> dict[str, str]:
    """Map job-name -> normalized signature for every managed job line in a crontab."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        sig = _signature(raw)
        if sig:
            out[sig[0]] = sig[1]
    return out


@dataclass
class Diff:
    missing: list[str] = field(default_factory=list)   # in manifest, absent live
    extra: list[str] = field(default_factory=list)     # managed live, not in manifest
    changed: list[str] = field(default_factory=list)   # present both, different
    ok: bool = True


def diff(sched: Schedule, live_text: str, *, home: str | None = None, repo: str | None = None) -> Diff:
    """Compare the manifest (host-expanded) against a live crontab. Secret values are
    ignored (stripped from both sides). Clean when every manifest job is present live
    with a matching schedule + command."""
    desired_lines = render_lines(sched, expand=True, home=home, repo=repo)
    desired = {}
    for ln in desired_lines:
        sig = _signature(ln)
        if sig:
            desired[sig[0]] = sig[1]
    live = parse_crontab(live_text)

    d = Diff()
    for name, sig in desired.items():
        if name not in live:
            d.missing.append(name)
        elif live[name] != sig:
            d.changed.append(name)
    managed_names = set(desired)
    for name in live:
        if name not in managed_names:
            d.extra.append(name)
    d.ok = not (d.missing or d.changed)
    return d


# ------------------------------------------------------------------------ install

def read_crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    # `crontab -l` exits non-zero with "no crontab for user"; treat as empty.
    if r.returncode != 0 and "no crontab" not in (r.stderr or "").lower():
        raise RuntimeError(f"crontab -l failed: {r.stderr.strip()}")
    return r.stdout


def splice_block(current: str, block: str) -> str:
    """Return the crontab text with the managed block inserted or replaced in place."""
    lines = current.splitlines()
    out: list[str] = []
    in_block = False
    replaced = False
    for ln in lines:
        if ln.strip() == BLOCK_BEGIN:
            in_block = True
            out.append(block)
            replaced = True
            continue
        if in_block:
            if ln.strip() == BLOCK_END:
                in_block = False
            continue
        out.append(ln)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(block)
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text


def load_env_file(path: str | os.PathLike) -> dict[str, str]:
    """Parse a simple KEY=value env file (no export, comments with #)."""
    p = Path(path).expanduser()
    env: dict[str, str] = {}
    if not p.exists():
        return env
    for raw in p.read_text().splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def resolve_secrets(sched: Schedule, env_file: str | os.PathLike | None) -> dict[str, str]:
    """Resolve secret VALUES from the env file, falling back to the process env.
    Never invents a value; a missing name is reported so install can fail loudly."""
    file_env = load_env_file(env_file or os.path.expanduser(DEFAULT_ENV_FILE))
    resolved: dict[str, str] = {}
    for name in sched.secret_env:
        if name in file_env and file_env[name] and file_env[name] != "replace-me":
            resolved[name] = file_env[name]
        elif os.environ.get(name):
            resolved[name] = os.environ[name]
    return resolved


def install(
    sched: Schedule,
    *,
    env_file: str | os.PathLike | None = None,
    home: str | None = None,
    repo: str | None = None,
    dry_run: bool = True,
) -> str:
    """Render the managed block (host-expanded, secrets injected) and splice it into
    the user crontab. Returns the full new crontab text. When dry_run is False the
    new crontab is written via `crontab -`. Raises if a declared secret is unresolved.
    """
    secrets = resolve_secrets(sched, env_file)
    missing = [n for n in sched.secret_env if n not in secrets]
    if missing:
        raise ScheduleError(
            "unresolved secret(s): " + ", ".join(missing)
            + f" - set them in the env file ({env_file or DEFAULT_ENV_FILE}) or the environment"
        )
    block = render_block(sched, expand=True, home=home, repo=repo, secrets=secrets)
    new_text = splice_block(read_crontab(), block)
    if not dry_run:
        subprocess.run(["crontab", "-"], input=new_text, text=True, check=True)
    return new_text
