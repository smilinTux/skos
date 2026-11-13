"""atlas: read-only adapter over Atlas's published brief + parked decisions.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.3: "firing/
stale summary, parked escalations with decide commands". Reads two real
artifacts ``skoperator run`` already writes every tick (section 3's table:
"Atlas brief artifact ... the publish pattern and co-location target for the
daily digest"):

  ``<fleet_root>/atlas/brief/brief.md``   the markdown half of what
                                           ``operator_seat.brief_publish.
                                           publish_brief`` writes (index.html
                                           is the same content HTML-rendered;
                                           brief.md is the smaller text this
                                           adapter parses).
  ``<fleet_root>/decisions/*.json``       ``operator_seat.decisions.park()``
                                           records (``{id, status, options,
                                           ...}``).

Both are plain files, so this is the one Phase-1 adapter with NO import
dependency on the optional skcapstone sibling: no ``ITILManager``, no fleet
paths module, just text and JSON. ``<fleet_root>`` still follows the fleet
tree's own ``SKFLEET_ROOT`` convention (mirroring
``skcapstone.fleet.paths.default_paths()``'s override) so a test -- or a
future co-located deploy -- can redirect both reads without touching real
fleet state, and two dedicated env vars let either half be pointed
independently when the brief and decisions stores are not co-located.

Per spec section 4 point 4 ("the safety plane monitors the report plane,
never the reverse"): this adapter only ever READS Atlas's already-published
output. It never calls into ``operator_seat.loop`` and never resolves a
decision; resolving is a human-only act via ``skoperator decide``, which is
exactly the command line this adapter renders into the digest so a firing
escalation is one command away from actionable, not just visible.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, registry

_STATE_RE = re.compile(r"\*\*(\d+) firing\*\*, (\d+) stale\.")


def _fleet_root() -> Path:
    env = os.environ.get("SKFLEET_ROOT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".skcapstone" / "fleet"


def _brief_dir() -> Path:
    env = os.environ.get("SKWATCHDOG_ATLAS_BRIEF_DIR")
    if env:
        return Path(env).expanduser()
    return _fleet_root() / "atlas" / "brief"


def _decisions_dir() -> Path:
    env = os.environ.get("SKWATCHDOG_ATLAS_DECISIONS_DIR")
    if env:
        return Path(env).expanduser()
    return _fleet_root() / "decisions"


@registry.register
class AtlasAdapter(WatchdogSourceAdapter):
    name = "atlas"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        out: list[WatchdogEvent] = []
        out.extend(self._brief_events(window))
        out.extend(self._decision_events(window))
        return out

    def _brief_events(self, window: Window) -> list[WatchdogEvent]:
        path = _brief_dir() / "brief.md"
        if not path.is_file():
            # Atlas has never ticked on this box: quiet, not broken.
            return []
        # A read error (permission denied, a truncated/binary write caught
        # mid-fsync) propagates from here; collect_safe degrades the WHOLE
        # adapter to one SourceUnavailable("atlas") event, which is fine --
        # it is the published artifact itself that is unreadable, so both
        # halves of this adapter (brief + decisions) are equally unable to
        # tell a coherent story this run.
        text = path.read_text(encoding="utf-8")
        date = window.until[:10]

        frozen = "**FROZEN**" in text
        quiet = "**All quiet**" in text
        m = _STATE_RE.search(text)
        firing = int(m.group(1)) if m else 0
        stale = int(m.group(2)) if m else 0

        if frozen:
            kind, severity = "AtlasFrozen", "notable"
            summary = "Atlas is FROZEN: standing down, no actuation."
        elif quiet:
            kind, severity = "AtlasQuiet", "info"
            summary = "Atlas: all quiet, nothing firing."
        elif firing or stale:
            kind, severity = "AtlasFiring", "notable"
            summary = f"Atlas: {firing} condition(s) firing, {stale} stale."
        else:
            # Brief exists but matched none of the known shapes (a future
            # brief_publish rewrite, most likely): say nothing rather than
            # guess a severity the brief itself did not state.
            return []

        events = [WatchdogEvent(
            ts=window.until, source=self.name, kind=kind, object="atlas-brief",
            severity=severity, summary=summary,
            link=WatchdogLink(uri="skworld://skos/watchdog/atlas/brief",
                               http="https://atlas.skworld.io/"),
            ref=f"atlas:brief:{kind}:{date}",
            meta={"firing": firing, "stale": stale, "frozen": frozen},
        )]
        if frozen:
            events.append(self._frozen_firing_event(window))
        return events

    def _frozen_firing_event(self, window: Window) -> WatchdogEvent:
        """AtlasFiring next to AtlasFrozen: what is waiting while Atlas stands down.

        A frozen ``skoperator run`` does no observation, so the brief's own
        ``firing=`` is structurally 0 and says nothing. The one durable,
        side-effect-free record of work waiting through a freeze is the parked
        decision store (``decisions.park()``), which this adapter already reads.
        count = pending decisions, apps = distinct ``app`` of their options
        ("unknown" where an older record carries none), oldest = earliest
        ``created``. Any read problem yields status Unknown with a reason,
        never a guessed count. Emitted ONLY while frozen: when unfrozen the
        brief itself emits AtlasFiring from live observation, and a second one
        would double count.
        """
        base = dict(
            ts=window.until, source=self.name, object="atlas-firing",
            link=WatchdogLink(uri="skworld://skos/watchdog/atlas/brief",
                               http="https://atlas.skworld.io/"),
            ref=f"atlas:brief:AtlasFiring:frozen:{window.until[:10]}",
            kind="AtlasFiring",
        )
        ddir = _decisions_dir()
        reason = ""
        pending: list[dict] = []
        if ddir.exists() and not ddir.is_dir():
            reason = f"decisions path {ddir} is not a directory"
        elif ddir.is_dir():
            try:
                paths = sorted(ddir.glob("*.json"))
            except OSError as exc:
                paths = []
                reason = f"cannot list decisions: {exc}"
            for p in paths:
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    reason = f"unreadable decision {p.name}: {exc}"
                    break
                if not isinstance(rec, dict):
                    reason = f"decision {p.name} is not an object"
                    break
                if rec.get("status") == "pending":
                    pending.append(rec)
        if reason:
            return WatchdogEvent(
                severity="notable",
                summary=f"Atlas is frozen; whether conditions are firing is Unknown: {reason}",
                meta={"status": "Unknown", "reason": reason, "source": "parked-decisions"},
                **base)
        apps: set[str] = set()
        for rec in pending:
            opts = [o for o in (rec.get("options") or []) if isinstance(o, dict)]
            named = {str(o["app"]) for o in opts if o.get("app")}
            apps.update(named or {"unknown"})
        created = [str(r["created"]) for r in pending if r.get("created")]
        oldest = min(created) if created else None
        count = len(pending)
        meta = {"status": "Known", "count": count, "apps": sorted(apps),
                "oldest": oldest, "source": "parked-decisions"}
        if count == 0:
            return WatchdogEvent(
                severity="info",
                summary="Atlas is frozen; nothing pending in the parked decision queue.",
                meta=meta, **base)
        return WatchdogEvent(
            severity="notable",
            summary=(f"Atlas is frozen with {count} pending decision(s) across "
                     f"{', '.join(sorted(apps))}; oldest {oldest or 'undated'}."),
            meta=meta, **base)

    def _decision_events(self, window: Window) -> list[WatchdogEvent]:
        ddir = _decisions_dir()
        if not ddir.is_dir():
            return []
        date = window.until[:10]
        out: list[WatchdogEvent] = []
        for p in sorted(ddir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                # One corrupt decision file must not blank the rest of the
                # pending queue: a per-file synthetic marker, not a raise.
                out.append(source_unavailable(
                    f"{self.name}:decision:{p.stem}", ts=window.until, error=str(exc)))
                continue
            if not isinstance(rec, dict) or rec.get("status") != "pending":
                continue
            did = str(rec.get("id") or p.stem)
            options = rec.get("options") or []
            first = options[0] if options else {}
            choice_flag = " --choice N" if len(options) > 1 else ""
            decide = f"skoperator decide {did} --approve{choice_flag}  (or --reject)"
            action = str(first.get("action") or "an action")
            rationale = str(first.get("rationale") or "")[:120]
            summary = f"Atlas parked a decision: {action}"
            if rationale:
                summary += f" ({rationale})"
            summary += f". Decide with: {decide}"
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="AtlasPendingDecision",
                object=did, severity="notable", summary=summary,
                link=WatchdogLink(uri=f"skworld://skos/watchdog/atlas/decision/{did}", http=""),
                ref=f"atlas:decision:{did}:pending:{date}",
                meta={"options": options, "decide_cmd": decide},
            ))
        return out
