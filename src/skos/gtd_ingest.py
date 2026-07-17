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

import fcntl
import json
import logging
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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


log = logging.getLogger("skos.gtd_ingest")

# Optional alert hook fired when a corrupt store file is quarantined.
# Signature: hook(original_path, quarantine_path, exception). Wire this to
# sk-alert (or any notifier) at app startup; the default is log-only.
corrupt_alert_hook: Callable[[Path, Path, Exception], None] | None = None


@contextmanager
def _store_lock():
    """Advisory flock over the whole store, held across every
    load-modify-save cycle so concurrent writers cannot lose updates.
    Not reentrant: internal callers use the *_locked helpers."""
    lock_path = gtd_dir() / ".gtd.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _quarantine(p: Path, exc: Exception) -> None:
    """Preserve a corrupt store file as <name>.corrupt-<utc-ts> and alert.
    Never silently discards data: the bad bytes stay on disk for forensics."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    qpath = p.with_name(f"{p.name}.corrupt-{ts}")
    n = 0
    while qpath.exists():  # pragma: no cover (sub-microsecond collision)
        n += 1
        qpath = p.with_name(f"{p.name}.corrupt-{ts}.{n}")
    os.replace(p, qpath)
    log.error("gtd store: corrupt file %s quarantined to %s (%s)", p, qpath, exc)
    hook = corrupt_alert_hook
    if hook is not None:
        try:
            hook(p, qpath, exc)
        except Exception:  # alert failure must not mask the quarantine
            log.exception("gtd store: corrupt_alert_hook failed for %s", qpath)


def _load(fname: str) -> list[dict]:
    """Load a store list. A corrupt file (bad JSON, or JSON that is not a
    list) is quarantined loudly, never silently treated as empty. I/O errors
    other than a missing file propagate: failing loud beats losing data."""
    p = gtd_dir() / fname
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"expected a JSON list, got {type(data).__name__}")
        return data
    except (json.JSONDecodeError, ValueError) as e:
        _quarantine(p, e)
        return []


def _save(fname: str, items: list[dict]) -> None:
    """Atomic save: write to a temp file in the same directory, fsync,
    os.replace over the target, then fsync the directory. The target is
    never truncated in place; a crash leaves either the old or new file."""
    d = gtd_dir()
    target = d / fname
    payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
    fd, tmp = tempfile.mkstemp(prefix=f".{fname}.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(target))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


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
    unified store. Returns the new item id, or None if it was a duplicate.
    The whole load-modify-save cycle runs under the store lock."""
    with _store_lock():
        return _capture_locked(c)


def _capture_locked(c: GtdCapture) -> str | None:
    """capture() body; caller must hold the store lock."""
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

    The whole reconcile (find + patch + move) runs under the store lock, and a
    cross-file move is ordered write-then-delete: the destination is written
    first, so a crash in between duplicates rather than loses the item, and the
    next move self-heals the duplicate.
    """
    with _store_lock():
        return _upsert_locked(c)


def _upsert_locked(c: GtdCapture) -> tuple[str, str]:
    if not c.source_ref:
        return (_capture_locked(c) or ""), "created"

    fname, idx, existing, items = _find_item(c.source, c.source_ref)
    if existing is None:
        return _capture_locked(c), "created"

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

    if dest == fname:
        items[idx] = updated                      # in-place update, one write
        _save(fname, items)
    else:
        # write-then-delete: land the item in the destination first, so a
        # crash between the two saves can only duplicate, never lose it.
        # Drop any same-id copy already in the dest (self-heal after a
        # previous crash) before appending the fresh state.
        dest_items = [it for it in _load(dest) if it.get("id") != updated["id"]]
        dest_items.append(updated)
        _save(dest, dest_items)
        items.pop(idx)                            # then remove from the source
        _save(fname, items)
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
