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

Two details that are easy to get wrong and expensive to get wrong:

* The bare ``ExecStart=`` reset is mandatory. ``ExecStart`` is a list-typed
  directive, so systemd APPENDS to it; without the reset the job runs twice,
  once bare and once wrapped.
* The original text is copied VERBATIM, specifiers included. ``%h`` and friends
  are systemd's to expand at start time, not ours to resolve at write time.

Drop-ins keep the change reversible by deleting a file, which matters when the
blast radius is every scheduled job on the box.
"""

from __future__ import annotations

import re
from pathlib import Path

# Units that belong to someone else. Wrapping them is not ours to do, and a
# package upgrade would drop the drop-in on the floor anyway.
THIRD_PARTY_PREFIXES = frozenset({"snap.", "systemd-", "dbus-", "gpg-agent", "pipewire"})

DROPIN_NAME = "sk-cron-run.conf"

_EXECSTART = re.compile(r"^ExecStart\s*=\s*(.+)$", re.MULTILINE)


def is_wrappable(unit: str) -> bool:
    """True when a unit is ours to wrap."""
    return not any(unit.startswith(prefix) for prefix in THIRD_PARTY_PREFIXES)


def wrap_command(runner: str, job: str, execstart: str) -> str:
    """The wrapped ExecStart line. Idempotent: never wraps a wrapped command."""
    execstart = execstart.strip()
    if execstart.startswith(runner):
        return execstart
    return f"{runner} {job} {execstart}"


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
    """The last ExecStart value in a unit file, or None."""
    try:
        text = service_path.read_text(encoding="utf-8")
    except OSError:
        return None
    matches = _EXECSTART.findall(_join_continuations(text))
    return matches[-1].strip() if matches else None


def plan_wraps(unit_dir: Path, runner: str, strict: bool = False) -> list[dict]:
    """Work out which timer-backed services need wrapping.

    Args:
        unit_dir: A systemd user unit directory.
        runner: Absolute path to ``sk-cron-run.sh``.
        strict: Raise on a timer whose service cannot be resolved, instead of
            reporting it as a problem entry.

    Returns:
        list[dict]: one entry per unit needing work. A normal entry carries
        ``unit``, ``service``, ``exec``, ``wrapped_exec`` and ``dropin``. An
        unresolvable timer carries ``unit`` and ``problem``.

    A unit already wrapped is omitted entirely, so the plan is what CHANGES
    and re-running converges. An unresolvable timer is reported rather than
    skipped: silence is the bug being fixed here, so this must not add more.
    """
    unit_dir = Path(unit_dir)
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
        execstart = _execstart_of(service)
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
                "wrapped_exec": wrapped,
                "dropin": str(unit_dir / f"{unit}.service.d" / DROPIN_NAME),
            }
        )
    return plan


def apply_wraps(unit_dir: Path, runner: str, plan: list[dict] | None = None) -> list[str]:
    """Write the drop-ins for a plan. Returns the paths written.

    Does NOT reload systemd: the caller decides when to
    ``systemctl --user daemon-reload``, so a batch of writes lands as one
    reload rather than N.
    """
    unit_dir = Path(unit_dir)
    plan = plan_wraps(unit_dir, runner) if plan is None else plan
    written: list[str] = []
    for entry in plan:
        if entry.get("problem"):
            continue
        path = Path(entry["dropin"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dropin_text(runner, entry["unit"], entry["exec"]), encoding="utf-8")
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
