"""fleet_events: read-only adapter over the fleet append-only event log.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.3's Phase 1
table ("fleet_events: fleet/events.py::read() per node. crash loops, converge
actions, condition transitions"). Reads via
``skcapstone.fleet.events.read()`` / ``skcapstone.fleet.paths``, one node at a
time. ``skcapstone`` is an OPTIONAL sibling package on skos (see
tests/conftest.py's ``_HAVE_SKCAPSTONE``); it is imported lazily inside
``collect()`` rather than at module import time, so this module always
imports cleanly and an absent skcapstone degrades this adapter to a
``SourceUnavailable`` digest line through ``collect_safe`` instead of
silently vanishing from the registry.

Fleet events are role-gated for writes and documented as "observability, not
control flow: no controller may key a decision off this log"
(skcapstone.fleet.events docstring). This adapter only ever calls
``fleet.events.read()``; it never calls ``emit()``, so it is strictly
read-only by construction, matching the WD-2 card's explicit note.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..events import WatchdogEvent, WatchdogLink
from ..port import Window, WatchdogSourceAdapter, registry

#: reason -> severity. A crash loop or a failed converge action is a real
#: problem; a successful converge action is a quiet info line; everything
#: else (config/trust/degrade events skwatchdog does not specifically know
#: about yet) reads as notable, since it reached the fleet log at all, which
#: means someone thought it was worth recording.
_PROBLEM_REASONS = {
    "CrashLooping", "RestartFailed", "StartFailed", "SpecInvalid",
    "SpecUnreadable", "NodeDead", "SpecUnverified",
}
_INFO_REASONS = {"Restarted", "Started", "Placed"}


def _severity(reason: str) -> str:
    if reason in _PROBLEM_REASONS:
        return "problem"
    if reason in _INFO_REASONS:
        return "info"
    return "notable"


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def _in_window(ts: str, since_dt: datetime, until_dt: datetime) -> bool:
    dt = _parse_ts(ts)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return since_dt <= dt <= until_dt


@registry.register
class FleetEventsAdapter(WatchdogSourceAdapter):
    name = "fleet"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        # Optional sibling import, deliberately lazy (see module docstring):
        # an ImportError here propagates out of collect() and collect_safe()
        # turns it into exactly one SourceUnavailable event for "fleet".
        from skcapstone.fleet.paths import default_paths
        from skcapstone.fleet import events as fleet_events

        paths = default_paths()
        if not paths.status.is_dir():
            # No fleet tree at all: a fresh box that has never joined the
            # fleet reads as quiet, not broken.
            return []

        since_dt = _parse_ts(window.since) or datetime.min.replace(tzinfo=timezone.utc)
        until_dt = _parse_ts(window.until) or datetime.now(timezone.utc)

        out: list[WatchdogEvent] = []
        for node_dir in sorted(p for p in paths.status.iterdir() if p.is_dir()):
            node = node_dir.name
            records = fleet_events.read(paths, node, limit=1000)
            for rec in records:
                ts = str(rec.get("ts") or "")
                if not _in_window(ts, since_dt, until_dt):
                    continue
                reason = str(rec.get("reason") or "")
                etype = str(rec.get("type") or "")
                ekind = str(rec.get("kind") or "")
                name_ = str(rec.get("name") or "")
                message = str(rec.get("message") or "")
                object_ = f"{name_}@{node}" if name_ else node
                summary = f"{node}: {etype or ekind or 'fleet event'} {reason or ''}".strip()
                if name_:
                    summary += f" on {name_}"
                summary += "."
                if message:
                    summary += f" {message[:160]}"
                out.append(WatchdogEvent(
                    ts=ts,
                    source=self.name,
                    kind=reason or etype or "FleetEvent",
                    object=object_,
                    severity=_severity(reason),
                    summary=summary,
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/fleet/{node}", http=""),
                    ref=f"fleet:{node}:{ts}:{ekind}:{reason}:{name_}",
                    meta={"node": node, "kind": ekind, "type": etype, "reason": reason},
                ))
        return out
