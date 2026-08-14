"""coord_autocode: read-only adapter over the unified coord + autocode board.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.3: "cards
opened/completed, staged children awaiting release, autopilot decisions
pending". Reads three real, local stores directly, no import of skcoord /
skcapstone needed at all (this is the one Phase-1 adapter, besides atlas,
with zero optional-sibling dependency, since coord's board is plain JSON /
JSONL on disk):

  ``coordination/tasks/<id>-<slug>.json``    one immutable file per card.
  ``coordination/card_events/<node>.jsonl``  append-only per-writer move/
                                              label log.
  ``coordination/autopilot/runs/<run_id>.json``  the autocode run journal.

THE STATUS TRAP (this is the real one that burned people, including Lumina,
this week -- see MEMORY.md "coord flood validated + decomposer fixed
2026-08-08"): a card's status is NOT a field on its ``tasks/*.json`` file.
Task files are immutable after creation (the coordination protocol's "tasks
are immutable" rule), so they carry no status at all past ``created_at``.
Status also does NOT reliably live in ``agents/<agent>.json`` -- that file is
one agent's own claimed/completed lists, a partial, per-writer view, not the
board's source of truth, and reading it as if it were "the" status leads
straight into exactly the trap this comment exists to name. The real,
timestamped, board-wide status history lives in ``card_events/*.jsonl``
(``{"card_id", "action", "ts", "column", ...}``), the same event-sourced log
skos-hardening's ITIL refactor introduced. This adapter reads THAT for
"completed" (``action == "move"`` to ``column == "done"``), and reads task
files only for their trustworthy, unchanging ``created_at`` + ``tags``.

Staged children awaiting release: a card tagged ``autopilot-staged``
(``skharness.autocode.orchestrator``'s "born STAGED into the Proposed lane")
sits outside the claimable pool until a human runs ``autopilot release``.
This is reported as one daily summary line (count), not filtered by window,
for the same "standing attention item" reason the itil/atlas adapters give:
it needs to stay visible every day it remains staged.

Every read here is independently fail-safe (a missing directory yields
nothing, a malformed file/line is skipped, never raised) EXCEPT the
top-level filesystem walk itself, which is left to raise on a genuinely
unreadable coordination tree (e.g. permission denied); ``collect_safe``
turns that into the adapter's ``SourceUnavailable`` line.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..events import WatchdogEvent, WatchdogLink
from ..port import Window, WatchdogSourceAdapter, registry

STAGED_TAG = "autopilot-staged"


def _coord_root() -> Path:
    home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))).expanduser()
    return home / "coordination"


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def _in_window(ts, since_dt: datetime, until_dt: datetime) -> bool:
    dt = _parse_ts(ts)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return since_dt <= dt <= until_dt


def _iter_json_files(directory: Path):
    """Yield parsed dicts from every ``*.json`` file in ``directory``.

    A missing directory yields nothing; a file that fails to read or parse
    is skipped, never raised -- this is a per-file fail-safe read, distinct
    from the top-level directory walk itself, which is allowed to raise
    (e.g. on a permission-denied coordination tree) and propagate to
    ``collect_safe``. Deliberately ``Path.iterdir()``, not ``Path.glob()``:
    glob swallows a permission-denied ``OSError`` on some Python versions
    (silently yielding nothing), which would misreport "unreachable" as
    "quiet"; ``iterdir()`` raises, as an unreachable source should.
    """
    if not directory.is_dir():
        return
    for p in sorted(directory.iterdir()):
        if not p.is_file() or p.suffix != ".json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            yield p, data


@registry.register
class CoordAutocodeAdapter(WatchdogSourceAdapter):
    name = "coord_autocode"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        root = _coord_root()
        since_dt = _parse_ts(window.since) or datetime.min.replace(tzinfo=timezone.utc)
        until_dt = _parse_ts(window.until) or datetime.now(timezone.utc)
        date = window.until[:10]

        out: list[WatchdogEvent] = []
        tasks_by_id: dict[str, dict] = {}
        staged = 0

        for _path, task in _iter_json_files(root / "tasks"):
            tid = str(task.get("id") or "")
            if not tid:
                continue
            tasks_by_id[tid] = task
            tags = {str(t).lower() for t in (task.get("tags") or [])}
            if STAGED_TAG in tags:
                staged += 1
            created_at = task.get("created_at")
            if _in_window(created_at, since_dt, until_dt):
                out.append(WatchdogEvent(
                    ts=str(created_at), source=self.name, kind="CardOpened",
                    object=tid, severity="info",
                    summary=f"card {tid} opened: {str(task.get('title', ''))[:120]}",
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/coord/{tid}", http=""),
                    ref=f"coord:card:{tid}:opened",
                    meta={"tags": sorted(tags)},
                ))

        out.extend(self._completed_events(root, tasks_by_id, since_dt, until_dt))

        if staged:
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="StagedAwaitingRelease",
                object="skwatchdog-findings", severity="notable",
                summary=f"{staged} staged card(s) awaiting `autopilot release`.",
                link=WatchdogLink(uri="skworld://skos/watchdog/coord/staged", http=""),
                ref=f"coord:staged-awaiting-release:{date}",
                meta={"count": staged},
            ))

        out.extend(self._autopilot_run_events(root, since_dt, until_dt))
        return out

    def _completed_events(self, root, tasks_by_id, since_dt, until_dt):
        out: list[WatchdogEvent] = []
        events_dir = root / "card_events"
        if not events_dir.is_dir():
            return out
        for p in sorted(events_dir.iterdir()):
            if not p.is_file() or p.suffix != ".jsonl":
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("action") != "move" or rec.get("column") != "done":
                    continue
                ts = rec.get("ts")
                if not _in_window(ts, since_dt, until_dt):
                    continue
                cid = str(rec.get("card_id") or "")
                if not cid:
                    continue
                title = str((tasks_by_id.get(cid) or {}).get("title") or cid)
                out.append(WatchdogEvent(
                    ts=str(ts), source=self.name, kind="CardCompleted",
                    object=cid, severity="info",
                    summary=f"card {cid} completed: {title[:120]}",
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/coord/{cid}", http=""),
                    ref=f"coord:card:{cid}:completed:{ts}",
                ))
        return out

    def _autopilot_run_events(self, root, since_dt, until_dt):
        out: list[WatchdogEvent] = []
        for p, run in _iter_json_files(root / "autopilot" / "runs"):
            updated = run.get("updated_at") or run.get("created_at")
            if not _in_window(updated, since_dt, until_dt):
                continue
            items = run.get("items") or {}
            run_id = str(run.get("run_id") or p.stem)
            out.append(WatchdogEvent(
                ts=str(updated), source=self.name, kind="AutopilotRun",
                object=run_id, severity="info",
                summary=(f"autopilot run {run_id}: {len(items)} item(s), "
                         f"{run.get('decisions', 0)} decision(s)."),
                link=WatchdogLink(uri=f"skworld://skos/watchdog/coord/run/{run_id}", http=""),
                ref=f"coord:run:{run_id}:{updated}",
                meta={"tokens": run.get("tokens"), "cost_usd": run.get("cost_usd")},
            ))
        return out
