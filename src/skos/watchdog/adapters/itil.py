"""itil: read-only adapter over ITIL incidents, problems, and changes.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.3 and the
2026-08-13 addendum ("Change Management is a SOURCE, never a second
notifier"). Reads via ``ITILManager`` (``skcapstone.itil``, a transparent
re-export of ``skcoord.itil``, the real implementation post CR-4.1
extraction). ``skcapstone``/``skcoord`` are OPTIONAL sibling packages on
skos, imported lazily inside ``collect()`` so an absent sibling degrades
this adapter to ``SourceUnavailable`` via ``collect_safe`` rather than
vanishing it from the registry.

``ITILManager``'s own record loaders (``list_incidents`` / ``list_problems``
/ ``list_changes``) already fail safe internally (a missing store directory
folds to an empty list, a single corrupt record folds to "skipped, logged",
never a raise -- see ``skcoord.itil._load_records``), so this adapter's own
realistic "unavailable" trigger is the import itself, not a runtime read
error.

What this narrates, and why it is not filtered by the digest window: open
incidents, open problems, changes awaiting a CAB vote, and scheduled changes
with their deploy window are STANDING attention items, the same shape as the
Atlas adapter's parked escalations -- they need to stay visible every day
they remain open, not just the one day they were created. The window still
stamps every event's ``ts`` (so a digest run always has a timestamp inside
its own window) and every event's ``ref`` includes the digest date, so a
persisting item is intentionally re-emitted once per day rather than
disappearing after its first mention; ``assemble_digest``'s dedupe-by-ref
only ever collapses duplicates WITHIN one run, so this never floods a single
digest.

Change Management non-negotiable (2026-08-13 addendum): this module ONLY
narrates change records it already reads from ITIL. It never subscribes to
the deploy runner, never adds a change-specific alert path, and a failed
change appears here as an ordinary "problem" narrative line with a deep
link, exactly like any other record -- CM keeps its own ``on_failure`` page
for the operational alert; this is the daily story, not a second page.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..events import WatchdogEvent, WatchdogLink
from ..port import Window, WatchdogSourceAdapter, registry


def _skcapstone_home() -> Path:
    return Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))).expanduser()


_INCIDENT_SEVERITY_PROBLEM = {"sev1", "sev2"}


@registry.register
class ItilAdapter(WatchdogSourceAdapter):
    name = "itil"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        # Optional sibling import, deliberately lazy (see module docstring).
        from skcapstone.itil import ITILManager, OPEN_INCIDENT_STATUSES

        mgr = ITILManager(_skcapstone_home())
        date = window.until[:10]
        out: list[WatchdogEvent] = []

        for inc in mgr.list_incidents():
            if inc.status.value not in OPEN_INCIDENT_STATUSES:
                continue
            sev = "problem" if inc.severity.value in _INCIDENT_SEVERITY_PROBLEM else "notable"
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="IncidentOpen",
                object=inc.id, severity=sev,
                summary=(f"incident {inc.id} ({inc.severity.value}): {inc.title} "
                         f"[{inc.status.value}]."),
                link=WatchdogLink(uri=f"skworld://skos/watchdog/itil/incident/{inc.id}", http=""),
                ref=f"itil:incident:{inc.id}:{inc.status.value}:{date}",
                meta={"severity": inc.severity.value, "status": inc.status.value},
            ))

        for prob in mgr.list_problems():
            if prob.status.value == "resolved":
                continue
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="ProblemOpen",
                object=prob.id, severity="notable",
                summary=f"problem {prob.id}: {prob.title} [{prob.status.value}].",
                link=WatchdogLink(uri=f"skworld://skos/watchdog/itil/problem/{prob.id}", http=""),
                ref=f"itil:problem:{prob.id}:{prob.status.value}:{date}",
                meta={"status": prob.status.value},
            ))

        for chg in mgr.list_changes():
            status = chg.status.value
            if status == "reviewing":
                out.append(WatchdogEvent(
                    ts=window.until, source=self.name, kind="ChangeAwaitingCAB",
                    object=chg.id, severity="notable",
                    summary=f"change {chg.id}: {chg.title} is awaiting a CAB vote.",
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/itil/change/{chg.id}", http=""),
                    ref=f"itil:change:{chg.id}:cab-pending:{date}",
                    meta={"status": status, "risk": chg.risk.value},
                ))
            elif status == "scheduled" and chg.scheduled_window:
                w = chg.scheduled_window
                start = w.get("window_start") or ("ASAP" if w.get("asap") else "an unset time")
                end = w.get("window_end") or "an unset time"
                out.append(WatchdogEvent(
                    ts=window.until, source=self.name, kind="ChangeScheduled",
                    object=chg.id, severity="info",
                    summary=f"change {chg.id}: {chg.title} is scheduled {start} to {end}.",
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/itil/change/{chg.id}", http=""),
                    ref=f"itil:change:{chg.id}:scheduled:{date}",
                    meta={"status": status, "scheduled_window": w},
                ))
            elif status == "failed":
                out.append(WatchdogEvent(
                    ts=window.until, source=self.name, kind="ChangeFailed",
                    object=chg.id, severity="problem",
                    summary=f"change {chg.id}: {chg.title} failed to deploy.",
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/itil/change/{chg.id}", http=""),
                    ref=f"itil:change:{chg.id}:failed:{date}",
                    meta={"status": status},
                ))
        return out
