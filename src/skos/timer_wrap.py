"""Route systemd user timers through sk-cron-run (card ``95a3b69e``).

The whole crontab runs under ``sk-cron-run``, which is what produces the run
ledger, the failure-to-GTD capture and the sk-alert. Zero of the systemd user
timers did, so half the scheduling surface could fail with nobody told.
OBSERVABILITY_AND_SCHEDULING_STANDARD says nothing scheduled fails silently;
that was simply false for the timer half, and it is how skoperator failed 16
times on 2026-08-13 and alerted nobody.

The wrap is a systemd DROP-IN, never an edit of the packaged unit::

    [Service]
    ExecStart=
    ExecStart=<sk-cron-run> <job> <original ExecStart>

Three details that are easy to get wrong and expensive to get wrong:

* The bare ``ExecStart=`` reset is mandatory. ``ExecStart`` is a list-typed
  directive, so systemd APPENDS to it; without the reset the job runs twice,
  once bare and once wrapped.
* The command to wrap is the unit's **effective** ``ExecStart``, which is the
  base fragment PLUS its drop-ins in lexical order, with a bare ``ExecStart=``
  resetting the list. Reading only the base ``.service`` file silently drops
  every flag that arrives from a drop-in. That is card ``47e32514``: skos wrapped
  ``skoperator.service`` from its report-only base fragment and discarded
  ``--execute`` (from ``execute.conf``) and ``--honor``, so the operator seat
  reverted to report-only while every status surface looked healthy.
* The command is copied VERBATIM from whichever source produced it. In the file
  fallback that means specifiers survive: ``%h`` and friends are systemd's to
  expand at start time, not ours to resolve at write time. Note the asymmetry,
  ``systemctl show`` reports an ALREADY-EXPANDED command, so a drop-in written
  from the effective value carries absolute paths rather than specifiers.

Drop-ins keep the change reversible by deleting a file, which matters when the
blast radius is every scheduled job on the box.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

# Units that belong to someone else. Wrapping them is not ours to do, and a
# package upgrade would drop the drop-in on the floor anyway.
THIRD_PARTY_PREFIXES = frozenset(
    {"snap.", "systemd-", "dbus-", "gpg-agent", "pipewire"}
)

DROPIN_NAME = "sk-cron-run.conf"

_EXECSTART = re.compile(r"^ExecStart\s*=\s*(.+)$", re.MULTILINE)

# systemd's ExecStart special prefixes. They qualify the whole command, so a
# wrap has to carry them AHEAD of the runner, not bury them in its arguments.
EXEC_PREFIX_CHARS = "-@:+!"

# Prefix flags this module knows how to reproduce from a `systemctl show`
# record. Anything else means falling back to the file parse rather than
# guessing, because getting `+` or `!` wrong changes a job's privileges.
_REPRODUCIBLE_FLAGS = {"ignore-failure": "-"}

#: Signature of an effective-ExecStart query: unit name in, raw
#: ``systemctl show`` output (or None when it cannot be answered) out.
ExecStartQuery = Callable[[str], "str | None"]

_AUTO = object()  # "decide for yourself whether systemd can be consulted"


def is_wrappable(unit: str) -> bool:
    """True when a unit is ours to wrap."""
    return not any(unit.startswith(prefix) for prefix in THIRD_PARTY_PREFIXES)


def split_exec_prefix(execstart: str) -> tuple[str, str]:
    """Split an ExecStart into its special-prefix characters and the command."""
    execstart = execstart.strip()
    cut = 0
    while cut < len(execstart) and execstart[cut] in EXEC_PREFIX_CHARS:
        cut += 1
    return execstart[:cut], execstart[cut:].lstrip()


def wrap_command(runner: str, job: str, execstart: str) -> str:
    """The wrapped ExecStart line. Idempotent: never wraps a wrapped command.

    Idempotency is load-bearing now that the command can be read back from
    systemd: the effective ExecStart of an already-wrapped unit IS the wrapped
    command, so a non-idempotent wrap would stack a second runner on every run
    of ``plan_wraps``.
    """
    prefix, command = split_exec_prefix(execstart)
    if command.startswith(runner):
        return f"{prefix}{command}"
    return f"{prefix}{runner} {job} {command}"


def dropin_text(runner: str, job: str, execstart: str) -> str:
    """The full drop-in file contents for one unit."""
    return (
        "# Managed by skos (card 95a3b69e): route this timer through\n"
        "# sk-cron-run so a failure produces a run-ledger record, a GTD item\n"
        "# and an sk-alert instead of silence.\n"
        "#\n"
        "# The bare ExecStart= below is REQUIRED: ExecStart is list-typed, so\n"
        "# systemd appends to it and the job would otherwise run twice.\n"
        "#\n"
        "# To undo: delete this file and run `systemctl --user daemon-reload`.\n"
        "[Service]\n"
        "ExecStart=\n"
        f"ExecStart={wrap_command(runner, job, execstart)}\n"
    )


def _join_continuations(text: str) -> str:
    """Fold systemd's backslash line-continuations into single logical lines.

    A unit may spell one ExecStart across many lines. Reading only the first
    would wrap a command with none of its arguments, which is a silently
    different command, so continuations are joined BEFORE anything is matched.
    """
    return re.sub(r"\\\n\s*", " ", text)


def _execstart_of(service_path: Path) -> str | None:
    """The last ExecStart value in a unit FILE, or None.

    The base fragment only. This is the fallback, kept byte-for-byte in its old
    behaviour so a box without systemd, a template unit, or a failed query lands
    exactly where it always did.
    """
    try:
        text = service_path.read_text(encoding="utf-8")
    except OSError:
        return None
    matches = _EXECSTART.findall(_join_continuations(text))
    return matches[-1].strip() if matches else None


def parse_show_execstart(value: str) -> str | None:
    """The command of the LAST ExecStart record in ``systemctl show`` output.

    ``systemctl --user show <unit> -p ExecStart --value`` prints one record per
    line::

        { path=/x ; argv[]=/x run --flag ; ignore_errors=no ; start_time=... }

    ``argv[]`` is the command, ``path`` is only its binary, so parsing ``path``
    would throw away every argument. The last record wins, matching the file
    parser's "last ExecStart" semantics.

    ``ExecStartEx`` reports ``flags=ignore-failure`` where ``ExecStart`` reports
    ``ignore_errors=yes``; both spellings are read. A prefix this module cannot
    faithfully reproduce (``+``, ``!``, ``@``) returns None so the caller falls
    back to the file rather than silently changing a job's privileges.

    Returns:
        str | None: the command, carrying any reproducible prefix character, or
        None when the output holds no usable record.
    """
    parsed: str | None = None
    for line in value.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        start = line.find("argv[]=")
        if start == -1:
            continue
        body = line[start + len("argv[]=") :]
        # The field that FOLLOWS argv[] terminates it. Scanning for that exact
        # key rather than the next " ; " keeps a command whose own arguments
        # contain a bare ";" (find -exec, say) intact.
        argv, flags = None, ""
        for sep in (" ; flags=", " ; ignore_errors="):
            cut = body.rfind(sep)
            if cut == -1:
                continue
            argv = body[:cut].strip()
            tail = body[cut + len(sep) :]
            end = tail.find(" ; ")
            flags = (tail if end == -1 else tail[:end]).strip()
            if sep.endswith("ignore_errors="):
                flags = "ignore-failure" if flags == "yes" else ""
            break
        if argv is None:
            continue
        prefix = ""
        for flag in (f for f in flags.split() if f):
            if flag not in _REPRODUCIBLE_FLAGS:
                return None
            prefix += _REPRODUCIBLE_FLAGS[flag]
        parsed = f"{prefix}{argv}" if argv else None
    return parsed


def systemd_execstart_query(unit_name: str) -> str | None:
    """Raw ``systemctl --user show`` ExecStart output for a unit, or None.

    None means "unanswerable, use the file": no ``systemctl`` on PATH, a
    template unit with no instance (``foo@.service``, which systemd refuses as
    "neither a valid invocation ID nor unit name"), a non-zero exit, or an
    unknown unit (which exits 0 with EMPTY output).
    """
    if unit_name.endswith("@.service"):
        return None
    exe = shutil.which("systemctl")
    if exe is None:
        return None
    for prop in ("ExecStartEx", "ExecStart"):
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [exe, "--user", "show", unit_name, "-p", prop, "--value"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    return None


def _systemd_manages(unit_dir: Path) -> bool:
    """True when ``unit_dir`` is one of systemd's own user unit directories.

    Guards the effective read. ``plan_wraps`` is routinely pointed at a fixture
    directory, and answering those from the LIVE manager would describe some
    other unit that merely shares a name.
    """
    exe = shutil.which("systemctl")
    if exe is None:
        return False
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [exe, "--user", "show", "-p", "UnitPath", "--value"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    try:
        target = unit_dir.resolve()
    except OSError:
        return False
    for entry in proc.stdout.split():
        try:
            if Path(entry).resolve() == target:
                return True
        except OSError:
            continue
    return False


def effective_execstart(unit_name: str, query: ExecStartQuery | None) -> str | None:
    """The unit's effective (base + drop-ins) ExecStart, or None."""
    if query is None:
        return None
    raw = query(unit_name)
    return parse_show_execstart(raw) if raw else None


def plan_wraps(
    unit_dir: Path,
    runner: str,
    strict: bool = False,
    effective: ExecStartQuery | None | object = _AUTO,
) -> list[dict]:
    """Work out which timer-backed services need wrapping.

    Args:
        unit_dir: A systemd user unit directory.
        runner: Absolute path to ``sk-cron-run.sh``.
        strict: Raise on a timer whose service cannot be resolved, instead of
            reporting it as a problem entry.
        effective: How to read a unit's effective (post-drop-in) ExecStart.
            Left at its default, systemd is consulted only when ``unit_dir`` is
            one of systemd's own user unit directories. Pass a callable to
            inject a query (tests do this, so they never touch the live
            manager), or None to force the base-file-only read.

    Returns:
        list[dict]: one entry per unit needing work. A normal entry carries
        ``unit``, ``service``, ``exec``, ``exec_source``, ``wrapped_exec`` and
        ``dropin``. An unresolvable timer carries ``unit`` and ``problem``.
        ``exec_source`` is ``"effective"`` or ``"file"``, so a dry run shows
        which reading produced the command.

    The command wrapped is the EFFECTIVE ExecStart (base fragment plus its
    drop-ins), falling back to the base file when systemd cannot answer. Card
    ``47e32514``: reading the base alone discarded flags that only a drop-in
    contributed, and nothing errored.

    A unit already wrapped is omitted entirely, so the plan is what CHANGES
    and re-running converges. An unresolvable timer is reported rather than
    skipped: silence is the bug being fixed here, so this must not add more.
    """
    unit_dir = Path(unit_dir)
    if effective is _AUTO:
        query: ExecStartQuery | None = (
            systemd_execstart_query if _systemd_manages(unit_dir) else None
        )
    else:
        query = effective  # type: ignore[assignment]

    plan: list[dict] = []
    for timer in sorted(unit_dir.glob("*.timer")):
        unit = timer.stem
        if not is_wrappable(unit):
            continue
        service = unit_dir / f"{unit}.service"
        # A templated timer (foo@.timer) is backed by its template service.
        if not service.exists() and "@" in unit:
            service = unit_dir / f"{unit.split('@', 1)[0]}@.service"
        if not service.exists():
            if strict:
                raise ValueError(f"timer {unit} has no resolvable .service unit")
            plan.append({"unit": unit, "problem": "no matching .service unit"})
            continue
        # Query the unit the DROP-IN will target, not the fragment that happens
        # to supply it: for foo@bar.timer the drop-in lands in foo@bar.service.d
        # even though the ExecStart comes from the foo@.service template.
        execstart = effective_execstart(f"{unit}.service", query)
        source = "effective"
        if execstart is None:
            execstart = _execstart_of(service)
            source = "file"
        if execstart is None:
            if strict:
                raise ValueError(f"unit {unit} has no ExecStart")
            plan.append({"unit": unit, "problem": "no ExecStart in the service unit"})
            continue
        wrapped = wrap_command(runner, unit, execstart)
        if wrapped == execstart:
            continue  # already wrapped
        plan.append(
            {
                "unit": unit,
                "service": str(service),
                "exec": execstart,
                "exec_source": source,
                "wrapped_exec": wrapped,
                "dropin": str(unit_dir / f"{unit}.service.d" / DROPIN_NAME),
            }
        )
    return plan


def apply_wraps(
    unit_dir: Path,
    runner: str,
    plan: list[dict] | None = None,
    effective: ExecStartQuery | None | object = _AUTO,
) -> list[str]:
    """Write the drop-ins for a plan. Returns the paths written.

    Does NOT reload systemd: the caller decides when to
    ``systemctl --user daemon-reload``, so a batch of writes lands as one
    reload rather than N.

    ``effective`` is forwarded to :func:`plan_wraps` and ignored when an
    already-computed ``plan`` is supplied.
    """
    unit_dir = Path(unit_dir)
    plan = plan_wraps(unit_dir, runner, effective=effective) if plan is None else plan
    written: list[str] = []
    for entry in plan:
        if entry.get("problem"):
            continue
        path = Path(entry["dropin"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            dropin_text(runner, entry["unit"], entry["exec"]), encoding="utf-8"
        )
        written.append(str(path))
    return written


def unwrap(unit_dir: Path) -> list[str]:
    """Remove every drop-in this module wrote. Returns the paths removed."""
    unit_dir = Path(unit_dir)
    removed: list[str] = []
    for path in sorted(unit_dir.glob(f"*.service.d/{DROPIN_NAME}")):
        path.unlink()
        removed.append(str(path))
        try:
            path.parent.rmdir()  # only if now empty
        except OSError:
            pass
    return removed
