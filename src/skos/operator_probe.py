"""skos operator-facet probe: the explain / observe / act contract (R2.12-style).

This is the canonical operator contract for skos, the module the `skos operator`
CLI is built over and the exact shape Atlas's skos adapter
(``skcapstone/src/skcapstone/operator_seat/skos_adapter.py``) mirrors. One
operator, many apps: skos conforms by exposing the same three verbs, byte-
compatible in shape with the adapter (kinds / conditions / actions from explain,
``{conditions:[{type,status,object}]}`` from observe).

The observe probes are REAL and injectable (tests never touch a live skos):

  * ``SchedulerAlive``    the skos scheduler-as-code pipeline is running jobs:
    read from the cron run-ledger (``~/.skcapstone/logs/cron-ledger.jsonl``), the
    same ledger ``skos status cron`` renders. A ledger whose newest run is older
    than the staleness window reads as a stalled scheduler (the cron pipeline is
    the "skscheduler"; there is no long-lived daemon, so freshness IS liveness).
  * ``GtdSinkDraining``   the GTD ingest sink is not backed up on failed items:
    the "error-recovery queue" is the sink's quarantine backlog, the
    ``*.corrupt-*`` files ``gtd_ingest._quarantine`` preserves when a store file
    fails to parse. A non-empty backlog reads as a sink that is not draining and
    needs a replay.
  * ``WatchdogDigestFresh``  skwatchdog (spec section 4, Phase 4: "Atlas watches
    the watchdog") published a digest within the last 26h: reads the mtime of
    the published ``latest/digest.json`` (``skos.watchdog.publish``), the exact
    moment ``publish_digest`` last landed one. A stale digest means the
    narrator itself went quiet, the failure an absent morning message is easy
    to misread as "nothing happened" rather than "nobody is watching".
  * ``GradingBacklog``   the WD-7 grading loop (``skos.watchdog.adapters.grading``)
    is not falling behind: reads the same latest digest for a ``GradingGap``
    event whose ``meta.budget_exhausted`` is true, meaning a run had more
    outbound replies queued than its fixed time budget could grade. A
    skgateway outage or one unparseable reply is a grader-AVAILABILITY skip,
    not a backlog, and deliberately does not fire this condition on its own.

Every probe fails SAFE (reports healthy) rather than raising a false alarm when
skos is unreachable, matching the adapter's ``_default_probe`` fail-safe posture.
``WatchdogDigestFresh`` carries one deliberate exception to "unreachable reads
as healthy": a digest file that genuinely does not exist is not the same as a
probe that could not look. See ``_probe_digest_age`` for how the two are told
apart.

The act verb maps the two reversible standard actions the adapter declares onto
real actuation through an injectable runner:

  * ``restart_service``  ``systemctl --user restart <skscheduler unit>``.
  * ``replay_errors``    ``skos gtd replay-errors`` (replays the sink's
    quarantine backlog, the error-recovery queue).

Anything non-standard or unknown is refused at the act verb (an unknown action
raises; a declared non-standard action escalates as MAJOR and never actuates).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

#: The four operator conditions. The first two match Atlas's skos_adapter.
#: CONDITIONS exactly; WatchdogDigestFresh and GradingBacklog (WD-11, Phase 4)
#: are ahead of that mirror until skcapstone lands its own follow-up card --
#: see the module docstring's "Phase 4" bullets.
CONDITIONS = ["SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog"]

#: The kinds skos exposes to the operator plane (mirrors skos_explain's kinds,
#: same Phase-4-ahead-of-the-mirror caveat as CONDITIONS above).
KINDS = ["scheduler", "gtd", "watchdog", "grading"]

#: Health-type conditions (they fire when status is False): a stalled scheduler
#: or a backed-up GTD sink both read as False -> firing. The two actions mirror
#: Atlas's skos_adapter._ACTIONS byte-for-byte (name/standard/reversible/
#: blast_radius/runbook/kedb_refs).
_ACTIONS = [
    {
        "name": "restart_service",
        "standard": True,
        "reversible": True,
        "blast_radius": "low",
        "runbook": "restart the skscheduler service",
        "kedb_refs": [],
    },
    {
        "name": "replay_errors",
        "standard": True,
        "reversible": True,
        "blast_radius": "low",
        "runbook": "replay the skos error-recovery queue",
        "kedb_refs": [],
    },
]

#: A scheduler whose newest recorded run is older than this reads as stalled. The
#: skos cron pipeline runs jobs at least daily (most every 3h), so a day-plus of
#: silence is a real "the scheduler stopped" signal, not normal quiet.
_SCHEDULER_MAX_AGE_S = 26 * 3600
#: Quarantine backlog above this many files reads as a sink that is not draining.
_QUARANTINE_LIMIT = 0
#: The systemd unit restart_service restarts (overridable; there is no long-lived
#: scheduler daemon on every node, so the operator's runner maps actuation).
_SCHEDULER_UNIT = "skscheduler.service"
#: A published digest older than this reads as stale (spec section 4, Phase 4:
#: "fires when no digest landed in 26h"). Same window as the scheduler's: the
#: watchdog runs daily, so more than a day-plus of silence is genuinely "the
#: narrator stopped", not a normal quiet morning.
_DIGEST_MAX_AGE_S = 26 * 3600


def _b(value: bool) -> str:
    return "True" if value else "False"


# --- pure probe logic (unit-tested directly) ---------------------------------


def _scheduler_alive(newest_run_age_s: Optional[float]) -> bool:
    """The scheduler-stall rule: alive when the newest recorded cron run is
    within the staleness window. Unknown age fails SAFE (alive)."""
    if newest_run_age_s is None:
        return True
    return newest_run_age_s <= _SCHEDULER_MAX_AGE_S


def _sink_draining(quarantine_depth: int) -> bool:
    """The sink is draining when the error-recovery (quarantine) backlog is at or
    below the limit. A backed-up backlog reads as not draining."""
    return quarantine_depth <= _QUARANTINE_LIMIT


def _digest_fresh(digest_age_s: Optional[float]) -> bool:
    """The digest-freshness rule: fresh when the latest published digest's age
    is within the staleness window. Unknown age fails SAFE (fresh) -- but note
    that a genuinely absent digest is NOT unknown here: `_probe_digest_age`
    returns `float('inf')` for "looked, found nothing", which always compares
    stale below, on purpose. Only `None` ("could not look at all") takes the
    fail-safe branch."""
    if digest_age_s is None:
        return True
    return digest_age_s <= _DIGEST_MAX_AGE_S


def _grading_not_backlogged(budget_exhausted: bool) -> bool:
    """The grading loop is not falling behind when its most recent digest run
    did not have to cut its grading list short for lack of time. Note the
    inversion: the condition is named GradingBacklog (matching the spec and
    Atlas's eventual mirror), but every condition in this module follows the
    same status convention -- True means healthy -- so True here means NO
    backlog was observed, exactly like SchedulerAlive/GtdSinkDraining."""
    return not budget_exhausted


# --- real signal readers (each fails safe = healthy) -------------------------


def _cron_ledger() -> str:
    home = os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))
    return os.environ.get("SKOS_CRON_LEDGER", str(Path(home) / "logs" / "cron-ledger.jsonl"))


def _gtd_dir() -> Optional[Path]:
    """The unified GTD store dir, via skos's own resolver. None on any failure
    (fails safe: an unresolvable store reads as an empty backlog)."""
    try:
        from skos.gtd_ingest import gtd_dir

        return gtd_dir()
    except Exception:
        env = os.environ.get("SK_GTD_DIR")
        return Path(env).expanduser() if env else None


def _probe_scheduler_run_age() -> Optional[float]:
    """Age in seconds of the newest run in the cron ledger, or None when there is
    no readable ledger (fails safe: unknown age reads as alive)."""
    try:
        import json
        import time
        from datetime import datetime, timezone

        p = Path(_cron_ledger())
        if not p.is_file():
            return None
        newest: Optional[float] = None
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                t = datetime.fromisoformat(rec["start"])
            except Exception:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            ts = t.timestamp()
            if newest is None or ts > newest:
                newest = ts
        if newest is None:
            return None
        return max(0.0, time.time() - newest)
    except Exception:
        return None


def _count_quarantine(gtd_dir: Optional[Path]) -> int:
    """Count the sink's error-recovery backlog: quarantined ``*.corrupt-*`` store
    files. A missing/unresolvable dir is zero (healthy)."""
    if gtd_dir is None:
        return 0
    p = Path(gtd_dir)
    if not p.is_dir():
        return 0
    try:
        return sum(1 for f in p.iterdir() if f.is_file() and ".corrupt-" in f.name)
    except Exception:
        return 0


def _digest_path() -> Optional[Path]:
    """The published `latest/digest.json` path, resolved with the exact same
    precedence `skos.watchdog.cursor.watchdog_home` uses (`SK_WATCHDOG_DIR` >
    `<SKCAPSTONE_HOME>/watchdog` > `~/.skcapstone/watchdog`) but WITHOUT that
    helper's mkdir side effect: this probe only ever reads (card: "Read-only
    observation. This card adds no actions and actuates nothing."), so
    merely asking whether a digest exists must never create the directory
    tree underneath it. None on any resolution failure (fails safe: unknown,
    mirroring `_gtd_dir`'s own soft-import posture)."""
    try:
        from skos.watchdog.publish import DIGEST_JSON_NAME
    except Exception:
        DIGEST_JSON_NAME = "digest.json"  # noqa: N806 - matches the publish.py constant
    try:
        env = os.environ.get("SK_WATCHDOG_DIR")
        if env:
            home = Path(env).expanduser()
        else:
            base = os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))
            home = Path(base) / "watchdog"
        return home / "digests" / "latest" / DIGEST_JSON_NAME
    except Exception:
        return None


def _probe_digest_age() -> Optional[float]:
    """Age in seconds since the latest published digest landed, or a sentinel
    for two DIFFERENT failure modes -- this probe must tell them apart, not
    collapse them into one fail-safe branch:

      * the digest file genuinely does not exist (`FileNotFoundError`): the
        probe DID look, successfully, and there is nothing there. That is
        exactly the "narrator went quiet" signal `WatchdogDigestFresh` exists
        to catch, so it reads back as an unbounded age (`float('inf')`) and
        always fails the freshness check in `_digest_fresh` -- never as an
        unknown that fails safe.
      * anything else (a permission error on the digests dir, an unresolvable
        watchdog home, or any other `OSError` reading the path): the probe
        could NOT look at all. That is a probe failure, not an observation of
        staleness, so it returns `None`, the same "I don't know" every other
        probe in this module uses to stay quiet rather than cry wolf on a
        permissions blip.
    """
    import time

    try:
        p = _digest_path()
    except Exception:
        return None
    if p is None:
        return None
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError:
        return float("inf")
    except OSError:
        return None
    return max(0.0, time.time() - mtime)


def _probe_grading_budget_exhausted() -> bool:
    """Whether the latest published digest reports the WD-7 grading loop's own
    run-time budget as exhausted (`GRADE_RUN_BUDGET_S` ran out mid-list, per
    `skos.watchdog.adapters.grading`): the real "falling behind" signal, more
    outbound replies queued than one run's fixed time budget could grade.
    Deliberately narrower than "any grade was skipped this run": a skgateway
    outage or one unparseable reply (`grader.SkipReason.GATEWAY_UNREACHABLE` /
    `UNPARSEABLE_REPLY`) is a grader-AVAILABILITY problem, not a backlog, and
    must not fire this condition on its own.

    Unlike `_probe_digest_age`, this does not need a tri-state result: a
    missing or unreadable digest is not, itself, evidence of a backlog (an
    absent digest is `WatchdogDigestFresh`'s alarm to raise, not this one's),
    so any failure to read or parse it -- missing file, corrupt JSON, an
    unreadable dir -- reads as False, the same "unresolvable reads as no
    problem" shape `_count_quarantine` already uses."""
    import json

    try:
        p = _digest_path()
        if p is None:
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    for bucket in ("problems", "notable"):
        for event in data.get(bucket) or ():
            if not isinstance(event, dict):
                continue
            if event.get("source") != "grading" or event.get("kind") != "GradingGap":
                continue
            if bool((event.get("meta") or {}).get("budget_exhausted")):
                return True
    return False


def _default_probe() -> dict:
    """Best-effort skos health from real signals. Fails SAFE (healthy) when skos
    is unreachable, so an inability to probe never raises a false alarm (with
    WatchdogDigestFresh's one deliberate exception, see `_probe_digest_age`)."""
    run_age = _probe_scheduler_run_age()
    depth = _count_quarantine(_gtd_dir())
    digest_age = _probe_digest_age()
    budget_exhausted = _probe_grading_budget_exhausted()
    return {
        "scheduler_alive": _scheduler_alive(run_age),
        "gtd_draining": _sink_draining(depth),
        "quarantine_depth": depth,
        "digest_fresh": _digest_fresh(digest_age),
        "grading_ok": _grading_not_backlogged(budget_exhausted),
    }


# --- contract verbs ----------------------------------------------------------


def explain() -> dict:
    """skos' self-description in the operator-contract shape (mirrors skos_explain)."""
    return {
        "kinds": list(KINDS),
        "conditions": list(CONDITIONS),
        "actions": [dict(a) for a in _ACTIONS],
    }


def observe(probe: Optional[Callable[[], dict]] = None) -> dict:
    """Read-only skos health snapshot in the operator-contract shape.

    ``probe`` is injectable so tests are hermetic; the default reads real signals
    and fails safe. Shape is byte-compatible with skos_adapter.skos_observe:
    the same condition types, statuses, and objects.
    """
    st = (probe or _default_probe)()
    return {
        "conditions": [
            {
                "type": "SchedulerAlive",
                "status": _b(bool(st.get("scheduler_alive", True))),
                "object": "skscheduler",
            },
            {
                "type": "GtdSinkDraining",
                "status": _b(bool(st.get("gtd_draining", True))),
                "object": "gtd-sink",
            },
            {
                "type": "WatchdogDigestFresh",
                "status": _b(bool(st.get("digest_fresh", True))),
                "object": "watchdog-digest",
            },
            {
                "type": "GradingBacklog",
                "status": _b(bool(st.get("grading_ok", True))),
                "object": "grading-loop",
            },
        ]
    }


def _action_meta(action: str) -> Optional[dict]:
    for a in _ACTIONS:
        if a["name"] == action:
            return a
    return None


def _command_for(action: str, *, unit: Optional[str] = None) -> Optional[list]:
    """The command a reversible standard action actuates through the runner."""
    if action == "restart_service":
        target = unit or os.environ.get("SKOS_SCHEDULER_UNIT") or _SCHEDULER_UNIT
        return ["systemctl", "--user", "restart", target]
    if action == "replay_errors":
        return ["skos", "gtd", "replay-errors"]
    return None


def _default_runner(cmd) -> dict:
    """Run an actuation command, capturing the result. Never invoked under test."""
    import subprocess

    proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def act(
    action: str,
    *,
    runner: Optional[Callable[[list], dict]] = None,
    unit: Optional[str] = None,
) -> dict:
    """Perform a reversible standard skos action, or refuse.

    ``restart_service`` and ``replay_errors`` (both standard, reversible, low
    blast) actuate through the injected ``runner`` (defaults to a real
    subprocess): restart_service runs ``systemctl --user restart <unit>`` and
    replay_errors runs ``skos gtd replay-errors`` to drain the error-recovery
    (quarantine) queue. A declared non-standard action escalates as MAJOR and
    never actuates; an unknown action is refused.
    """
    meta = _action_meta(action)
    if meta is None:
        raise ValueError(f"unknown skos operator action {action!r}")
    if not meta.get("standard"):
        # No non-standard action is declared today; this stays as the defensive
        # refusal path so any future irreversible action escalates, never runs.
        return {
            "action": action,
            "performed": False,
            "escalate": "MAJOR",
            "reason": (
                "non-standard: human-approval-only, escalates as MAJOR by "
                "construction (policy.classify_change) and never actuates here"
            ),
        }
    cmd = _command_for(action, unit=unit)
    if cmd is None:  # pragma: no cover - standard actions always map
        raise ValueError(f"no command mapping for skos action {action!r}")
    result = (runner or _default_runner)(cmd)
    return {
        "action": action,
        "performed": True,
        "command": cmd,
        "result": result,
    }


__all__ = [
    "CONDITIONS",
    "KINDS",
    "explain",
    "observe",
    "act",
]
