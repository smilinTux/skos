"""gtd-ingest: the unified-GTD capture port.

Every input to Chef's one GTD (ITIL incidents, email across N mailboxes,
telegram, voice, calendar, cron failures) is a *source adapter* that produces
``GtdCapture`` objects and hands them to the single :func:`capture` sink. The
sink normalizes, dedupes (by ``(source, source_ref)``), and appends to the
unified GTD store, the same JSON store the skcapstone GTD/ITIL tools use.

Design: docs/gtd-ingest-architecture.md. Adding a source = one adapter, no core
changes.

    from skos.gtd_ingest import GtdCapture, capture, GtdSourceAdapter, registry

    # push adapter (e.g. ITIL, cron):
    capture(GtdCapture(text="cron FAILED: backup", source="cron",
                       source_ref="cron:backup@2026-07-03", context="@ops",
                       priority="high"))

    # pull adapter: subclass + implement poll(); a scheduler job drains it.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .adapter import Adapter, AdapterRegistry

# ── unified store location ───────────────────────────────────────────────────
# Shared with the skcapstone GTD tools. Precedence:
#   SK_GTD_DIR  >  <SKCAPSTONE_HOME>/coordination/gtd  >  ~/.skcapstone/coordination/gtd
def gtd_dir() -> Path:
    """Return (and create) the unified GTD store directory.

    Precedence: ``SK_GTD_DIR`` (explicit override) > skcapstone's own resolver
    (so the store is byte-identical to what its GTD/ITIL tools read/write, and
    honors ``SKCAPSTONE_SHARED_ROOT`` + tests) > ``<SKCAPSTONE_HOME>/coordination/gtd``.
    This keeps skos standalone-capable while staying perfectly unified when it
    runs alongside skcapstone."""
    env = os.environ.get("SK_GTD_DIR")
    if env:
        d = Path(env).expanduser()
    else:
        try:  # optional, soft: align with skcapstone's exact store location
            from skcapstone.mcp_tools.gtd_tools import _gtd_dir as _sk_gtd_dir
            return _sk_gtd_dir()  # already mkdirs
        except Exception:
            home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
            d = home / "coordination" / "gtd"
    d.mkdir(parents=True, exist_ok=True)
    return d


# status -> list file (matches the skcapstone GTD layout)
_LIST_FILE = {
    "inbox": "inbox.json",
    "next": "next-actions.json",
    "project": "projects.json",
    "waiting": "waiting-for.json",
    "someday": "someday-maybe.json",
    "reference": "someday-maybe.json",
}
_ALL_FILES = ["inbox.json", "next-actions.json", "projects.json",
              "waiting-for.json", "someday-maybe.json", "archive.json"]


@dataclass
class GtdCapture:
    """A normalized capture from any source, ready for the sink."""
    text: str
    source: str                      # itil | email | cron | telegram | voice | calendar | manual
    source_ref: str                  # stable dedup key (incident id, gmail thread id, ...)
    context: str = "@inbox"
    priority: str | None = None      # critical | high | medium | low | None
    privacy: str = "private"         # private | team | community | public
    status: str = "inbox"            # inbox | next | project | waiting | someday | reference
    delegate_to: str | None = None
    meta: dict = field(default_factory=dict)   # source-specific fields (email_*, itil_*, ...)


def _load(fname: str) -> list[dict]:
    p = gtd_dir() / fname
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(fname: str, items: list[dict]) -> None:
    (gtd_dir() / fname).write_text(json.dumps(items, indent=2, ensure_ascii=False, default=str),
                                   encoding="utf-8")


def _seen_refs() -> set[tuple[str, str]]:
    """All (source, source_ref) pairs already present anywhere in the store."""
    seen: set[tuple[str, str]] = set()
    for fname in _ALL_FILES:
        for it in _load(fname):
            ref = it.get("source_ref")
            if ref:
                seen.add((it.get("source"), ref))
    return seen


def capture(c: GtdCapture) -> str | None:
    """The single sink. Dedupe by (source, source_ref); normalize; append to the
    unified store. Returns the new item id, or None if it was a duplicate."""
    if c.source_ref and (c.source, c.source_ref) in _seen_refs():
        return None
    item = {
        "id": uuid.uuid4().hex[:12],
        "text": c.text,
        "source": c.source,
        "source_ref": c.source_ref,
        "privacy": c.privacy,
        "context": c.context,
        "priority": c.priority,
        "energy": None,
        "status": c.status,
        "delegate_to": c.delegate_to,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # namespace source-specific metadata (email_*, itil_*, ...) onto the item
    for k, v in (c.meta or {}).items():
        item.setdefault(k, v)
    fname = _LIST_FILE.get(c.status, "inbox.json")
    items = _load(fname)
    items.append(item)
    _save(fname, items)
    return item["id"]


def _find_item(source: str, source_ref: str):
    """Locate an item by (source, source_ref) anywhere in the store.
    Returns (fname, index, item, items_list) or (None, None, None, None)."""
    for fname in _ALL_FILES:
        items = _load(fname)
        for idx, it in enumerate(items):
            if it.get("source") == source and it.get("source_ref") == source_ref:
                return fname, idx, it, items
    return None, None, None, None


def upsert(c: GtdCapture) -> tuple[str, str]:
    """Create-or-update sink for *stateful* sources (orders/deliveries, builds, …).

    Unlike :func:`capture` (which skips a repeat ``source_ref``), ``upsert``
    reconciles the incoming capture onto the existing item: it patches changed
    fields, **moves** the item between list files when ``status`` changes, and
    **archives** it when ``status == "done"``. Source-specific ``meta`` is merged
    with overwrite (the adapter supplies the fresh state, e.g. the new order state).

    Returns ``(item_id, action)`` with ``action`` in
    ``{created, unchanged, updated, completed}``. On ``unchanged`` it performs **no
    write**, the property that keeps polling idempotent and notifications quiet.
    """
    if not c.source_ref:
        return (capture(c) or ""), "created"

    fname, idx, existing, items = _find_item(c.source, c.source_ref)
    if existing is None:
        return capture(c), "created"

    updated = dict(existing)
    changed = False
    for key, new_val in (("text", c.text), ("status", c.status),
                         ("priority", c.priority), ("context", c.context)):
        if new_val is not None and updated.get(key) != new_val:
            updated[key] = new_val
            changed = True
    for k, v in (c.meta or {}).items():          # merge source-specific state (overwrite)
        if updated.get(k) != v:
            updated[k] = v
            changed = True

    if not changed:
        return existing["id"], "unchanged"        # no write, no notify

    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    terminal = c.status == "done"
    if terminal:
        updated["completed_at"] = updated["updated_at"]
    dest = "archive.json" if terminal else _LIST_FILE.get(updated.get("status", "inbox"), "inbox.json")

    items.pop(idx)                                # remove from current file
    _save(fname, items)
    dest_items = items if dest == fname else _load(dest)
    dest_items.append(updated)
    _save(dest, dest_items)
    return updated["id"], ("completed" if terminal else "updated")


# ── the port: source adapters register here ──────────────────────────────────
registry = AdapterRegistry()


class GtdSourceAdapter(Adapter):
    """Base for every GTD source. PUSH adapters call :meth:`emit` on events;
    PULL adapters implement :meth:`poll` and are drained by a scheduler job."""
    capability = "gtd-ingest"
    name = ""

    def poll(self) -> list[GtdCapture]:  # pull adapters override
        return []

    def emit(self, c: GtdCapture) -> str | None:  # push adapters call this
        return capture(c)

    def drain(self) -> list[str]:
        """Poll then capture everything; return the ids actually written (deduped)."""
        return [i for c in self.poll() if (i := capture(c))]
