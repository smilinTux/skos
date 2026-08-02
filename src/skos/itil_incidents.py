"""ITIL open-incident read model: fold-on-read over the incident event log.

Mirrors the fold shape already used to read the cron run-ledger
(:func:`skos.status.cron_status`): the incident store is an append-only JSONL
event log (one event per line, newest last), and every read replays it from
scratch, folding each ``incident_id``'s events into its current state (later
events patch fields onto the accumulated record). There is no separate mutable
snapshot to fall out of sync with the log.

    from skos.itil_incidents import load_open_incidents

    open_incidents = load_open_incidents()   # -> list[Incident]

Event shape (one JSON object per line)::

    {"incident_id": "inc-1", "ts": "2026-08-01T00:00:00Z", "title": "disk full",
     "status": "detected", "service": "skmem-pg", "severity": "high"}
    {"incident_id": "inc-1", "ts": "2026-08-01T00:05:00Z", "status": "investigating"}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Terminal incident statuses (docs/skos-autopilot-architecture.md section 6.2):
# detected -> acknowledged -> investigating -> resolved; escalated is a
# separate branch and stays open until a human resolves it.
CLOSED_STATUSES = frozenset({"resolved", "closed"})


def itil_dir() -> Path:
    """Return (and create) the ITIL coordination-store directory.

    Precedence: ``SK_ITIL_DIR`` (explicit override) > ``<SKCAPSTONE_HOME>/coordination/itil``,
    matching :func:`skos.gtd_ingest.gtd_dir`'s resolution order for the sibling
    GTD store."""
    env = os.environ.get("SK_ITIL_DIR")
    if env:
        d = Path(env).expanduser()
    else:
        home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
        d = home / "coordination" / "itil"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _events_path() -> Path:
    return itil_dir() / "incidents.jsonl"


@dataclass(frozen=True)
class Incident:
    """Folded state of one ITIL incident, as of the newest event read."""

    id: str
    title: str
    status: str
    severity: str | None = None
    service: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _read_events(path: Path) -> list[dict]:
    """Parse the JSONL event log in order. A line that fails to parse is
    skipped, never raised: one corrupt line must not blind the reader to every
    incident recorded around it (matches skos.status.cron_status's tolerance
    of bad ledger lines)."""
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _fold_incidents(events: list[dict]) -> dict[str, dict]:
    """Reduce an ordered incident-event stream into current per-incident state.

    Each event patches the fields it carries onto the accumulated record for
    its ``incident_id``; later events win. Events without an ``incident_id``
    are skipped."""
    state: dict[str, dict] = {}
    for ev in events:
        iid = ev.get("incident_id")
        if not iid:
            continue
        rec = state.setdefault(iid, {"id": iid})
        for k, v in ev.items():
            if k == "incident_id" or v is None:
                continue
            rec[k] = v
        ts = ev.get("ts")
        if ts:
            rec.setdefault("created_at", ts)
            rec["updated_at"] = ts
    return state


def load_open_incidents() -> list[Incident]:
    """Fold-on-read: replay the ITIL incident event log and return every
    incident whose folded status is not terminal (``resolved``/``closed``),
    oldest first."""
    state = _fold_incidents(_read_events(_events_path()))
    open_incidents = [
        Incident(
            id=rec["id"],
            title=rec.get("title", ""),
            status=rec.get("status", "detected"),
            severity=rec.get("severity"),
            service=rec.get("service"),
            created_at=rec.get("created_at"),
            updated_at=rec.get("updated_at"),
        )
        for rec in state.values()
        if rec.get("status") not in CLOSED_STATUSES
    ]
    open_incidents.sort(key=lambda i: i.created_at or "")
    return open_incidents
