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
from datetime import datetime, timezone
from pathlib import Path

from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, registry

_FROZEN_STATE_RE = re.compile(
    r"state: frozen=true mid_pass=(?P<mid>true|false) firing=(?P<firing>\d+) "
    r"stale=\d+ acted_before_freeze=(?P<acted>\d+)")
_STILL_FIRING_RE = re.compile(r"(\d+) condition\(s\) still firing")


def _parse_ts(value: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
            events.append(self._frozen_firing_event(window, text))
        return events

    def _frozen_firing_event(self, window: Window, text: str) -> WatchdogEvent:
        """AtlasFiring next to AtlasFrozen: what is waiting while Atlas is frozen.

        Two independent facts, both read-only:

        * LIVE firing, from the brief's machine line
          ``state: frozen=true mid_pass=... firing=N ... acted_before_freeze=M``
          (``brief_publish._state_line``). It is non-zero only when a freeze
          landed MID-pass: a pass that starts frozen observes nothing, so it is
          0 by construction. ``mid_pass`` and ``acted_before_freeze`` ride along.
        * The parked backlog: pending decisions in ``decisions/*.json``.
          ``parked_backlog`` (count), ``parked_apps``, ``parked_oldest``. This
          comes from BEFORE the freeze and cannot change while frozen.

        Any problem reading the decision store yields status Unknown with a
        reason, never a guessed backlog. Emitted only while frozen: unfrozen,
        the brief's own live AtlasFiring covers it and a second would double
        count.
        """
        base = dict(
            ts=window.until, source=self.name, object="atlas-firing",
            link=WatchdogLink(uri="skworld://skos/watchdog/atlas/brief",
                               http="https://atlas.skworld.io/"),
            ref=f"atlas:brief:AtlasFiring:frozen:{window.until[:10]}",
            kind="AtlasFiring",
        )
        sm = _FROZEN_STATE_RE.search(text)
        if sm:
            live = int(sm.group("firing"))
            mid_pass = sm.group("mid") == "true"
            acted = int(sm.group("acted"))
        else:
            fm = _STILL_FIRING_RE.search(text)
            mid_pass = "landed mid-pass" in text
            live = int(fm.group(1)) if fm else 0
            acted = 0
        reason, pending = self._read_pending()
        live_meta = {"firing": live, "mid_pass": mid_pass, "acted_before_freeze": acted}
        lead = f"Atlas is frozen with {live} condition(s) firing"
        if mid_pass:
            lead += f" (freeze landed mid-pass, {acted} action(s) taken before it)"
        if reason is not None:
            return WatchdogEvent(
                severity="notable",
                summary=f"{lead}; parked backlog is Unknown: {reason}",
                meta={"status": "Unknown", "reason": reason, **live_meta,
                      "source": "brief-state+parked-decisions"},
                **base)
        apps: set[str] = set()
        dated: list[tuple[datetime, str]] = []
        for rec in pending:
            opts = [o for o in (rec.get("options") or []) if isinstance(o, dict)]
            named = {str(o.get("app") or o.get("object")) for o in opts
                     if o.get("app") or o.get("object")}
            apps.update(named or {"unknown"})
            c = rec.get("created")
            dt = _parse_ts(c) if c else None
            if dt is not None:
                dated.append((dt, str(c)))
        oldest = min(dated)[1] if dated else None
        backlog = len(pending)
        meta = {"status": "Known", **live_meta, "parked_backlog": backlog,
                "parked_apps": sorted(apps), "parked_oldest": oldest,
                "source": "brief-state+parked-decisions"}
        summary = f"{lead}; parked backlog {backlog}"
        if backlog:
            summary += (f" across {', '.join(sorted(apps))}, oldest "
                        f"{oldest or 'undated'} (from before the freeze)")
        summary += "."
        return WatchdogEvent(
            severity="notable" if (live or backlog) else "info",
            summary=summary, meta=meta, **base)

    @staticmethod
    def _read_pending() -> tuple[str | None, list[dict]]:
        """(reason, pending records). reason is None on a clean read."""
        pending: list[dict] = []
        try:
            ddir = _decisions_dir()
            if not ddir.exists():
                return None, pending
            if not ddir.is_dir():
                return f"decisions path {ddir} is not a directory", pending
            with os.scandir(ddir) as it:
                names = sorted(e.name for e in it
                               if e.name.endswith(".json") and e.is_file())
            for name in names:
                try:
                    rec = json.loads((ddir / name).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    return f"unreadable decision {name}: {exc}", pending
                if not isinstance(rec, dict):
                    return f"decision {name} is not an object", pending
                if rec.get("status") == "pending":
                    pending.append(rec)
        except OSError as exc:
            return f"cannot read decisions: {exc}", pending
        return None, pending

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
