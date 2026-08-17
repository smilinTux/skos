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
    needs a replay. Resolving where that store lives is a pure read: see
    ``_gtd_dir`` for why it must never create the store.
  * ``WatchdogDigestFresh``  skwatchdog (spec section 4, Phase 4: "Atlas watches
    the watchdog") published a digest within the last 26h: reads the published
    ``<watchdog root>/digests/latest/digest.json`` (``skos.watchdog.publish``).
    Age comes from the digest's OWN window end first, with the file's mtime
    only as a fallback, so a stale digest that gets re-published today cannot
    look fresh just because the bytes were rewritten. A stale digest means the
    narrator itself went quiet, the failure an absent morning message is easy
    to misread as "nothing happened" rather than "nobody is watching".
  * ``GradingBacklog``   the WD-7 grading loop (``skos.watchdog.adapters.grading``)
    IS falling behind: reads the same latest digest for a ``GradingGap`` event
    whose ``meta.budget_exhausted`` is exactly ``True``, meaning a run had more
    outbound replies queued than its fixed time budget could grade. A
    skgateway outage or one unparseable reply is a grader-AVAILABILITY skip,
    not a backlog, and deliberately does not fire this condition on its own.
    Note the polarity: unlike the other three, this is a PROBLEM-when-True
    condition (see ``PROBLEM_WHEN_TRUE``), matching the adapter exactly.

The scheduler and GTD halves fail SAFE (report healthy) rather than raising a
false alarm when skos is unreachable, matching the adapter's ``_default_probe``
fail-safe posture. The two watchdog halves deliberately do NOT: they fail to
UNKNOWN, never to healthy. A missing, unreadable, non-JSON or non-object digest,
or a watchdog root that will not resolve, is exactly the "the narrator went
quiet" case, so reporting it as fresh would silence the very signal the
condition exists to raise. Unknown surfaces honestly as ``"Unknown"`` in the
observe payload (see ``_tri``), which the operator brief reads as stale.

Failing safe is not the same as inventing a reading. Every probe here resolves
its paths WITHOUT creating anything (``_gtd_dir``, ``_digest_path``), and where
it could not look it says so rather than reporting a confident zero: the
observe verb is read-only, in the filesystem sense as well as the contract
sense.

The act verb maps the two reversible standard actions the adapter declares onto
real actuation through an injectable runner:

  * ``restart_service``  ``systemctl --user restart <skscheduler unit>``.
  * ``replay_errors``    ``skos gtd replay-errors`` (replays the sink's
    quarantine backlog, the error-recovery queue).

Anything non-standard or unknown is refused at the act verb (an unknown action
raises; a declared non-standard action escalates as MAJOR and never actuates).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

#: The four operator conditions, matching Atlas's ``skos_adapter.CONDITIONS``
#: exactly and in order. The generated SKWorld manifest's ``operator.conditions``
#: block mirrors this same list, and skcapstone's drift guards
#: (tests/operator_seat/test_manifest_adapter_conformance.py and
#: test_manifest_adapter.py) assert all three agree.
CONDITIONS = ["SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog"]

#: The kinds skos exposes to the operator plane (mirrors skos_explain's kinds).
#: Grading is not its own kind: the grading loop is part of what the watchdog
#: narrates, and the signal is read out of the watchdog's own digest.
KINDS = ["scheduler", "gtd", "watchdog"]

#: Condition types that indicate a PROBLEM when their status is "True"; the rest
#: are health types, a problem when "False". ``GradingBacklog`` is a problem
#: type: a backlog EXISTS when it is True. skcapstone's operator loop unions this
#: optional module-level set across adapters (``loop.PROBLEM_WHEN_TRUE``), and
#: skos' own adapter declares the identical set. Without it a backlog condition
#: is read upside down: quiet when it fires, firing when it is quiet.
PROBLEM_WHEN_TRUE = frozenset({"GradingBacklog"})

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
#: The published-digest filename and the two path segments below the watchdog
#: root, mirroring ``publish.DIGEST_JSON_NAME`` / ``digests_dir`` / ``latest_dir``.
#: Spelled out rather than imported because importing ``publish`` to learn a
#: filename would be an import purely to read a constant, and the module it
#: would pull in is the one whose mkdir side effects this probe exists to avoid.
_DIGEST_JSON_NAME = "digest.json"
_DIGEST_SEGMENTS = ("digests", "latest")
#: The event kind the grading adapter emits for ungraded replies, and the meta
#: flag separating "ran out of time" (backlog) from "grader was unavailable".
_GRADING_GAP_KIND = "GradingGap"
_BUDGET_FLAG = "budget_exhausted"


def _b(value: bool) -> str:
    return "True" if value else "False"


def _tri(value: Optional[bool]) -> str:
    """Tri-state condition status, mirroring ``skos_adapter._tri``. ``None`` is
    the honest Unknown and is NOT collapsed to healthy: see the module
    docstring on why the watchdog halves must not fail safe."""
    if value is None:
        return "Unknown"
    return _b(bool(value))


# --- pure probe logic (unit-tested directly) ---------------------------------


def _scheduler_alive(newest_run_age_s: Optional[float]) -> bool:
    """The scheduler-stall rule: alive when the newest recorded cron run is
    within the staleness window. Unknown age fails SAFE (alive)."""
    if newest_run_age_s is None:
        return True
    return newest_run_age_s <= _SCHEDULER_MAX_AGE_S


def _sink_draining(quarantine_depth: Optional[int]) -> bool:
    """The sink is draining when the error-recovery (quarantine) backlog is at or
    below the limit. A backed-up backlog reads as not draining.

    An unknown depth (None, see ``_count_quarantine``) fails SAFE (draining),
    matching every other condition in this module: not being able to look is
    not evidence of a backlog, and firing on every node that simply has no GTD
    store yet would turn this condition into permanent noise. The distinction
    is not lost, it is carried by ``quarantine_depth`` staying None."""
    if quarantine_depth is None:
        return True
    return quarantine_depth <= _QUARANTINE_LIMIT


def _digest_fresh(age_s: Optional[float]) -> Optional[bool]:
    """The digest-freshness rule: fresh when the latest published digest's age
    is within the staleness window.

    Tri-state, and deliberately NOT fail-safe (mirrors
    ``skos_adapter._digest_fresh``). An unknown age stays UNKNOWN rather than
    collapsing to fresh, because every way the age can come back unknown --
    no digest on disk, an unreadable one, an unresolvable watchdog root -- is
    itself the quiet-narrator case this condition exists to catch. Reporting
    "fresh" there would be reporting that the watchdog is fine on the strength
    of never having looked at it.
    """
    if age_s is None:
        return None
    return age_s <= _DIGEST_MAX_AGE_S


# --- real signal readers (each fails safe = healthy) -------------------------


def _cron_ledger() -> str:
    home = os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))
    return os.environ.get("SKOS_CRON_LEDGER", str(Path(home) / "logs" / "cron-ledger.jsonl"))


def _gtd_dir() -> Optional[Path]:
    """The unified GTD store dir, resolved with the exact same precedence
    ``skos.gtd_ingest.gtd_dir`` uses (``SK_GTD_DIR`` > skcapstone's own
    shared-root resolver > ``<SKCAPSTONE_HOME>/coordination/gtd``) but WITHOUT
    that helper's mkdir side effect.

    Why this re-implements the precedence by hand instead of just calling
    ``gtd_dir()``: ``gtd_dir()`` CREATES the directory tree, and the skcapstone
    resolver it delegates to (``skcapstone.mcp_tools.gtd_tools._gtd_dir``)
    additionally seeds six empty store files. This module is the operator
    facet's explain / observe / act contract, and observation here is
    read-only, so merely asking "is the GTD sink draining" must never bring the
    store into existence. Before this was fixed, running the test suite or
    hitting the web UI's ``/status.json`` (which reaches ``_default_probe``
    with no env isolation at all) created ``~/.skcapstone/coordination/gtd``,
    seed files and all, as a side effect of a READ. An observer with a write
    side effect is not an observer. Do not "simplify" this back into a
    ``gtd_dir()`` call; ``_digest_path`` below re-implements
    ``watchdog_home()`` for exactly the same reason (WD-11).

    ``gtd_dir()`` itself is deliberately left alone: every one of its other
    callers (``gtd_ingest``'s own writers and ``_store_lock``, ``mail``,
    ``coldstart``, ``backup``) is a writer that wants the tree created.

    Returns None when the path cannot be resolved at all. That reads as
    UNKNOWN downstream, never as a confident "no backlog": see
    ``_count_quarantine``.
    """
    try:
        env = os.environ.get("SK_GTD_DIR")
        if env:
            return Path(env).expanduser()
        try:  # optional, soft: the same sibling alignment gtd_dir() does
            from skcapstone.mcp_tools._helpers import _shared_root

            return Path(_shared_root()).expanduser() / "coordination" / "gtd"
        except Exception:  # noqa: BLE001 - absent or broken sibling: documented default
            home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
            return home / "coordination" / "gtd"
    except Exception:
        return None


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


def _count_quarantine(gtd_dir: Optional[Path]) -> Optional[int]:
    """Count the sink's error-recovery backlog: quarantined ``*.corrupt-*`` store
    files. Tri-state on purpose, the same way ``_probe_digest_age`` is:

      * an ``int`` is a real observation. The store directory exists and was
        enumerated, so the count IS the backlog.
      * ``None`` means UNKNOWN: the path could not be resolved, the store does
        not exist, or the directory could not be listed. Absence of the store
        is not evidence the sink is fine, so this deliberately does not report
        a confident ``0``, a number the probe never actually verified. This
        matters more now that resolving the path no longer creates the store:
        before, the probe's own mkdir guaranteed the dir existed and the 0 was
        self-fulfilling.

    ``_sink_draining`` still fails safe on the unknown, so this raises no false
    alarm; the unknown-ness survives in the probe's ``quarantine_depth``, which
    stays None rather than 0, and the web UI already renders that as "n/a".
    """
    if gtd_dir is None:
        return None
    p = Path(gtd_dir)
    if not p.is_dir():
        return None
    try:
        return sum(1 for f in p.iterdir() if f.is_file() and ".corrupt-" in f.name)
    except Exception:
        return None


def _watchdog_home() -> Optional[Path]:
    """skos' watchdog state root, resolved WITHOUT creating it.

    Mirrors ``skos.watchdog.cursor.watchdog_home()``'s precedence exactly
    (``SK_WATCHDOG_DIR`` > ``<SKCAPSTONE_HOME>/watchdog`` >
    ``~/.skcapstone/watchdog``) but never mkdirs. This re-implements the
    precedence by hand for the same reason ``_gtd_dir`` above does, and the
    same "do not simplify this back into a helper call" note applies with full
    force: ``watchdog_home()`` mkdirs, and so do ``publish.digests_dir`` and
    ``publish.latest_dir``. An operator that creates the store it is only
    supposed to look at manufactures the state it then reports on, and a
    freshly-mkdir'd empty digests dir is indistinguishable from a watchdog that
    has never run.

    An empty/whitespace override falls back to the default rather than
    resolving against the cwd. Returns None when nothing resolves, which reads
    as UNKNOWN downstream, never as healthy.
    """
    try:
        env = (os.environ.get("SK_WATCHDOG_DIR") or "").strip()
        if env:
            return Path(env).expanduser()
        home = (os.environ.get("SKCAPSTONE_HOME") or "").strip()
        base = Path(home).expanduser() if home else Path.home() / ".skcapstone"
        return base / "watchdog"
    except Exception:
        return None


def _digest_path() -> Optional[Path]:
    """``<watchdog root>/digests/latest/digest.json``: the file
    ``publish.publish_digest`` atomically replaces on every digest run, and the
    one a served host (and the Flutter Digest tab) fetches."""
    root = _watchdog_home()
    if root is None:
        return None
    return root.joinpath(*_DIGEST_SEGMENTS, _DIGEST_JSON_NAME)


def _read_digest() -> Optional[dict]:
    """The published digest as a dict, or None (UNKNOWN) when it is absent,
    unreadable, not JSON, or not a JSON object.

    Never raises, never writes, and never creates a parent dir on the way to
    looking. Both watchdog conditions read this one snapshot, so a single
    filesystem read backs both and they can never disagree about what was on
    disk."""
    try:
        p = _digest_path()
        if p is None:
            return None
        if not p.is_file():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _parse_iso(ts) -> Optional[float]:
    """Epoch seconds for an ISO8601 stamp (a naive stamp reads as UTC), or None."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _window_end(digest: dict) -> Optional[float]:
    """The end of the period the digest run actually covered, epoch seconds.

    ``skos_adapter._digest_age_s`` reads ``window.until``; ``Window.to_dict``
    (``skos.watchdog.port``) serialises the same field as ``to``, so the shipped
    digests on disk carry ``{"from": ..., "to": ...}``. Both spellings are
    accepted, ``until`` first so this stays a strict superset of the adapter's
    behaviour: anything the adapter can date from the window, this dates
    identically, and the real digests skos publishes get dated too instead of
    silently falling through to mtime forever.
    """
    window = digest.get("window")
    if not isinstance(window, dict):
        return None
    for key in ("until", "to"):
        parsed = _parse_iso(window.get(key))
        if parsed is not None:
            return parsed
    return None


def _digest_age_s(digest: Optional[dict], *, now: Optional[float] = None) -> Optional[float]:
    """Age of the published digest in seconds, or None when unknown.

    Prefers the digest's OWN window end (what the run actually covered) over
    the file's mtime, so a stale digest that gets re-published cannot look
    fresh just because the bytes were rewritten. Falls back to the file mtime
    only when the window is missing or unparseable, and to None when even that
    cannot be read.
    """
    if digest is None:
        return None
    published = _window_end(digest)
    if published is None:
        try:
            p = _digest_path()
            published = p.stat().st_mtime if p is not None else None
        except Exception:
            published = None
    if published is None:
        return None
    return max(0.0, (time.time() if now is None else now) - published)


def _digest_events(digest: dict):
    """Every event a digest carries. ``assemble_digest`` puts problem- and
    notable-severity events in these two lists (info events are only counted,
    never carried), and ``GradingGap`` is emitted at notable severity."""
    for key in ("problems", "notable"):
        value = digest.get(key)
        if not isinstance(value, list):
            continue
        for event in value:
            if isinstance(event, dict):
                yield event


def _grading_backlog(digest: Optional[dict]) -> Optional[bool]:
    """True only when a ``GradingGap`` event says the run's own time budget ran
    out (``GRADE_RUN_BUDGET_S`` expired mid-list, per
    ``skos.watchdog.adapters.grading``): the real "more replies queued than one
    run had time to grade" signal.

    Deliberately NARROW, and it must stay that way. The same ``GradingGap`` kind
    is also emitted when the grader was unreachable or a reply did not parse
    (``grader.SkipReason.GATEWAY_UNREACHABLE`` / ``UNPARSEABLE_REPLY``); that is
    grader AVAILABILITY, not backlog. Widening this to "any GradingGap" would
    turn every skgateway blip into a backlog alarm and make the real signal
    worthless.

    The flag test is ``is True``, not a truthiness check, on purpose: a digest
    is JSON off disk and a meta value of the string ``"false"`` is truthy in
    Python. ``is True`` fires only on a real JSON boolean true.

    None (UNKNOWN) when there is no readable digest to judge from: an absent
    digest is ``WatchdogDigestFresh``'s alarm to raise, and claiming "no
    backlog" from a file nobody could read would be inventing a reading.
    """
    if digest is None:
        return None
    for event in _digest_events(digest):
        if event.get("kind") != _GRADING_GAP_KIND:
            continue
        meta = event.get("meta")
        if isinstance(meta, dict) and meta.get(_BUDGET_FLAG) is True:
            return True
    return False


def _default_probe() -> dict:
    """Best-effort skos health from real signals.

    Two different fail postures, on purpose (mirrors
    ``skos_adapter._default_probe``): the scheduler/GTD halves fail SAFE
    (healthy) when skos is unreachable so an inability to probe never raises a
    false alarm, while the two watchdog halves fail to UNKNOWN (None), never to
    healthy. Each half is read independently, so a failing scheduler probe never
    hides the digest reading and vice versa.
    """
    run_age = _probe_scheduler_run_age()
    depth = _count_quarantine(_gtd_dir())
    digest = _read_digest()
    return {
        "scheduler_alive": _scheduler_alive(run_age),
        "gtd_draining": _sink_draining(depth),
        "quarantine_depth": depth,
        "digest_fresh": _digest_fresh(_digest_age_s(digest)),
        "grading_backlog": _grading_backlog(digest),
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
            # Tri-state, and note the missing-key default is None -> "Unknown",
            # NOT True: a probe that did not report a watchdog reading has not
            # told us the watchdog is fine.
            {
                "type": "WatchdogDigestFresh",
                "status": _tri(st.get("digest_fresh")),
                "object": "watchdog-digest",
            },
            {
                "type": "GradingBacklog",
                "status": _tri(st.get("grading_backlog")),
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
    "PROBLEM_WHEN_TRUE",
    "explain",
    "observe",
    "act",
]
