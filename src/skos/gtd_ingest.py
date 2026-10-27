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
import socket
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
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


# ── estate guard: one GTD store per estate ───────────────────────────────────
# Chef's estate (nor) and Casey's cluster (chi) each own a GTD store on their
# own Syncthing ring. A store may carry an ``ESTATE`` file naming its estate;
# a host whose own estate differs refuses to write it (lists or spool), so a
# mounted, copied or mis-shared store can never collect the other estate's
# items. Cross-estate items travel as skmail to the other estate's agent and
# are captured THERE, by a host of that estate. No ESTATE file = no check.
#
# Host estate: ``SKWORLD_ESTATE`` > hostname prefix (``nor*``/``chi*``). A host
# with neither (bare names like ``ollama``, ``ziowk01``) is unknown and allowed
# with a warning, so the guard never strands a writer it cannot place.
ESTATE_FILENAME = "ESTATE"
# naming-standard labels are aliases of the legacy prefixes
_ESTATE_ALIASES = {"chef": "nor", "cakjr": "chi"}


class EstateMismatchError(RuntimeError):
    """This host belongs to a different estate than the GTD store it tried to write."""


def _norm_estate(name: str | None) -> str | None:
    name = (name or "").strip().lower()
    return _ESTATE_ALIASES.get(name, name) or None


def store_estate(d: Path | None = None) -> str | None:
    """Estate named in the store's ESTATE file (first non-comment line), or None."""
    try:
        raw = ((d or gtd_dir()) / ESTATE_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            return _norm_estate(line)
    return None


def host_estate() -> str | None:
    """This host's estate: SKWORLD_ESTATE, else the nor/chi hostname prefix, else None."""
    env = _norm_estate(os.environ.get("SKWORLD_ESTATE"))
    if env:
        return env
    host = _hostname().lower()
    for prefix in ("nor", "chi"):
        if host.startswith(prefix):
            return prefix
    return None


def guard_estate(d: Path | None = None) -> None:
    """Raise :class:`EstateMismatchError` if this host may not write store ``d``."""
    want = store_estate(d)
    if want is None:
        return
    have = host_estate()
    if have is None:
        log.warning("gtd store: estate of host %s unknown (set SKWORLD_ESTATE); "
                    "writing %s store unchecked", _hostname(), want)
        return
    if have != want:
        raise EstateMismatchError(
            f"gtd store {d or gtd_dir()} belongs to estate {want!r}; host {_hostname()} "
            f"is estate {have!r}. Send the item to that estate's agent over skmail "
            f"instead of writing its store.")


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


#: Subdir under the GTD store where a replay stages the quarantine backlog.
REPLAY_DIRNAME = ".replay"


def quarantine_backlog() -> list[Path]:
    """The sink's error-recovery queue: the ``*.corrupt-*`` files preserved by
    ``_quarantine`` when a store file failed to parse. Empty when the sink is
    draining normally. Never raises: an unresolvable store reads as empty."""
    try:
        d = gtd_dir()
    except Exception:
        return []
    try:
        return sorted(p for p in d.iterdir() if p.is_file() and ".corrupt-" in p.name)
    except OSError:
        return []


def replay_quarantine() -> list[str]:
    """Replay the error-recovery queue: move each quarantined ``*.corrupt-*`` file
    into the ``.replay`` staging subdir for reprocessing/forensics. Reversible
    (files are relocated, never deleted) and low blast: it only clears the
    backlog location, flipping GtdSinkDraining back green. Returns the moved
    file names."""
    backlog = quarantine_backlog()
    if not backlog:
        return []
    dest = gtd_dir() / REPLAY_DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for src in backlog:
        target = dest / src.name
        n = 0
        while target.exists():  # pragma: no cover (sub-microsecond collision)
            n += 1
            target = dest / f"{src.name}.{n}"
        os.replace(src, target)
        moved.append(src.name)
    log.info("gtd store: replayed %d quarantined file(s) to %s", len(moved), dest)
    return moved


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
    guard_estate(d)
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


# ── single-owner writes (the Syncthing collision fix) ────────────────────────
# The store is a set of WHOLE-FILE JSON lists in a Syncthing-shared folder.
# flock and atomic rename only serialize writers on ONE host. When two hosts each
# load-modify-save inbox.json inside the sync window, Syncthing keeps one version
# and parks the other as a .sync-conflict copy, so every item only the loser had
# vanishes from the store (2026-10-27: noroc2027 and .41 both captured cron
# failures just after local midnight, 6 items survived only in conflict copies).
#
# Fix: one OWNER host writes the lists. Every other host appends its captures to
# its own ``spool/<host>.jsonl`` (one writer per file, append-only, so a sync is
# a plain copy and can never conflict), and the owner folds the spools in under
# the store lock. The owner is named in the synced ``OWNER`` file beside the
# lists (or ``SK_GTD_OWNER_HOST``); with neither set every host writes directly,
# which is the old behavior and stays correct for a single-host store.
OWNER_FILENAME = "OWNER"
SPOOL_DIRNAME = "spool"


def _hostname() -> str:
    return os.environ.get("SK_GTD_HOST") or socket.gethostname()


def owner_host() -> str | None:
    """The one host allowed to rewrite the store lists, or None (any host)."""
    env = (os.environ.get("SK_GTD_OWNER_HOST") or "").strip()
    if env:
        return env
    try:
        raw = (gtd_dir() / OWNER_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            return line
    return None


def is_owner() -> bool:
    """True when this host may write the store lists directly."""
    owner = owner_host()
    if owner is None:
        return True
    me = _hostname()
    return me == owner or me.split(".", 1)[0] == owner.split(".", 1)[0]


def _spool_path(host: str | None = None) -> Path:
    guard_estate(gtd_dir())
    d = gtd_dir() / SPOOL_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{(host or _hostname()).replace('/', '-')}.jsonl"


def _read_spool(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """Complete records from ``offset`` on, plus the offset after the last one.
    A trailing line without a newline is a write still in flight (or a partial
    sync) and is left for the next pass."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    records: list[dict] = []
    consumed = 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        consumed += len(line)
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            log.error("gtd spool: skipping unparseable line in %s", path)
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records, offset + consumed


def _spool(op: str, c: GtdCapture, item_id: str) -> None:
    """Append one op to this host's spool. Single writer per file, so no merge."""
    _spool_record({"v": 1, "op": op, "id": item_id, "host": _hostname(),
                   "ts": datetime.now(timezone.utc).isoformat(), "capture": asdict(c)})


def _spool_record(rec: dict) -> None:
    """Append one complete record (one line) to this host's spool, fsynced."""
    op, item_id = rec.get("op"), rec.get("id")
    path = _spool_path()
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    log.info("gtd store: %s spooled %s for owner %s", op, item_id, owner_host())


def _spooled_refs() -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for rec in _read_spool(_spool_path())[0]:
        cap = rec.get("capture") or {}
        if rec.get("op") == "capture" and cap.get("source_ref"):
            refs.add((cap.get("source"), cap["source_ref"]))
    return refs


# ── spooled mutations (clarify / move / done / reopen) and raw appends ───────
# capture/upsert are not the only whole-file rewrites: the skcapstone GTD tools
# move items between lists and the dreaming engine appends to someday-maybe. On
# a non-owner host those spool too. A mutation record says "put item <id> in
# list <to>, after applying <patch> and dropping <drop>, if it is currently in
# one of <src>" and the owner applies it onto ITS current copy of the item, so
# fields changed elsewhere in the meantime survive.
#
#   {"v": 1, "op": "done", "id": "<item id>", "host": "...", "ts": "<iso>",
#    "mutation": {"to": "archive", "src": ["inbox", ...] | null,
#                 "patch": {"status": "done", "completed_at": "<iso>"},
#                 "drop": []}}
#   {"v": 1, "op": "append", "id": "<item id>", "host": "...", "ts": "<iso>",
#    "to": "someday-maybe", "item": {...full item...}}
#
# Lists are named by file stem (inbox, next-actions, projects, waiting-for,
# someday-maybe, archive). Replay is safe: a mutation is skipped (logged, never
# raised) when the item is missing, is outside ``src``, already sits in ``to``
# with the same patch, or carries a mutation stamp at or after the record's
# ``ts`` (it was applied already, or the owner changed it later and wins). An
# append is skipped when its id is already anywhere in the store.
MUTATION_OPS = ("clarify", "move", "done", "reopen")
LIST_NAMES = tuple(f[: -len(".json")] for f in _ALL_FILES)
# Every timestamp a GTD mutation stamps on an item; the newest is its "last touched".
_MUTATION_STAMPS = ("clarified_at", "moved_at", "completed_at", "reopened_at", "updated_at")


def _list_file(name: str) -> str:
    if name not in LIST_NAMES:
        raise ValueError(f"unknown GTD list {name!r} (valid: {', '.join(LIST_NAMES)})")
    return f"{name}.json"


def spool_mutation(action: str, item_id: str, *, to: str, src: list[str] | None = None,
                   patch: dict | None = None, drop: list[str] | tuple = (),
                   ts: str | None = None) -> dict:
    """Non-owner hosts: queue a clarify/move/done/reopen of ``item_id`` for the
    owner. ``ts`` should be the same instant the caller stamped into ``patch``
    (e.g. ``completed_at``) so a replay recognises the op as already applied.
    Returns the record written."""
    if action not in MUTATION_OPS:
        raise ValueError(f"unknown GTD mutation {action!r} (valid: {', '.join(MUTATION_OPS)})")
    _list_file(to)
    for name in src or ():
        _list_file(name)
    rec = {"v": 1, "op": action, "id": item_id, "host": _hostname(),
           "ts": ts or datetime.now(timezone.utc).isoformat(),
           "mutation": {"to": to, "src": list(src) if src is not None else None,
                        "patch": dict(patch or {}), "drop": list(drop)}}
    _spool_record(rec)
    return rec


def spool_append(item: dict, to: str) -> dict:
    """Non-owner hosts: queue a whole, already-built item for the owner to append
    to list ``to`` (e.g. dreaming-engine items into someday-maybe). The item
    must carry its final ``id``. Returns the record written."""
    _list_file(to)
    if not item.get("id"):
        raise ValueError("spool_append needs an item with an id")
    rec = {"v": 1, "op": "append", "id": item["id"], "host": _hostname(),
           "ts": datetime.now(timezone.utc).isoformat(), "to": to, "item": dict(item)}
    _spool_record(rec)
    return rec


def _last_touched(item: dict) -> str:
    return max((str(item.get(k)) for k in _MUTATION_STAMPS if item.get(k)), default="")


def _locate(item_id: str) -> tuple[str | None, int | None, dict | None, list | None]:
    """(list name, index, item, items) for ``item_id``; live lists shadow archive."""
    for fname in _ALL_FILES:
        items = _load(fname)
        for idx, it in enumerate(items):
            if it.get("id") == item_id:
                return fname[: -len(".json")], idx, it, items
    return None, None, None, None


def _apply_mutation_locked(rec: dict) -> int:
    """Apply one spooled mutation; caller holds the store lock. 1 if it landed,
    0 if it was skipped (every skip is logged, none raises)."""
    action, item_id, ts = rec.get("op"), rec.get("id"), str(rec.get("ts") or "")
    m = rec.get("mutation") or {}
    to, src = m.get("to"), m.get("src")
    patch, drop = m.get("patch") or {}, m.get("drop") or []
    if not item_id or to not in LIST_NAMES or not isinstance(patch, dict):
        log.error("gtd spool: malformed %s record for %r, skipped", action, item_id)
        return 0
    cur, idx, item, items = _locate(item_id)
    if item is None:
        log.warning("gtd spool: %s %s skipped: item not in the store", action, item_id)
        return 0
    if src is not None and cur not in src:
        log.warning("gtd spool: %s %s skipped: item is in %s, op expected %s",
                    action, item_id, cur, src)
        return 0
    if ts and _last_touched(item) >= ts:
        log.info("gtd spool: %s %s skipped: already applied or superseded (%s >= %s)",
                 action, item_id, _last_touched(item), ts)
        return 0
    updated = {**item, **patch}
    for k in drop:
        updated.pop(k, None)
    if cur == to and updated == item:
        log.info("gtd spool: %s %s skipped: already in %s", action, item_id, to)
        return 0
    if cur == to:
        items[idx] = updated
        _save(_list_file(to), items)
    else:
        # write-then-delete, as everywhere else: a crash can only duplicate.
        dest = [it for it in _load(_list_file(to)) if it.get("id") != item_id]
        dest.append(updated)
        _save(_list_file(to), dest)
        items.pop(idx)
        _save(_list_file(cur), items)
    log.info("gtd spool: applied %s %s (%s -> %s) from %s", action, item_id, cur, to,
             rec.get("host"))
    return 1


def _apply_append_locked(rec: dict) -> int:
    """Apply one spooled append; caller holds the store lock. Idempotent by id."""
    item, to = rec.get("item"), rec.get("to")
    if not isinstance(item, dict) or not item.get("id") or to not in LIST_NAMES:
        log.error("gtd spool: malformed append record %r, skipped", rec.get("id"))
        return 0
    if item["id"] in _all_ids():
        return 0
    items = _load(_list_file(to))
    items.append(item)
    _save(_list_file(to), items)
    return 1


def _offsets_path() -> Path:
    """Per-host, NOT synced: how far the owner has applied each spool."""
    base = os.environ.get("SK_GTD_SPOOL_STATE")
    if base:
        return Path(base).expanduser()
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return state / "skos" / "gtd-spool-offsets.json"


def _drain_spool_locked() -> int:
    """Apply every non-owner spool record; caller holds the store lock.
    Idempotent: a capture whose id or (source, source_ref) is already in the
    store is skipped, so a lost offsets file only costs a re-scan."""
    d = gtd_dir() / SPOOL_DIRNAME
    if not d.is_dir():
        return 0
    op_path = _offsets_path()
    try:
        offsets = json.loads(op_path.read_text(encoding="utf-8"))
        if not isinstance(offsets, dict):
            offsets = {}
    except (OSError, json.JSONDecodeError):
        offsets = {}
    applied = 0
    changed = False
    for path in sorted(d.glob("*.jsonl")):
        if ".sync-conflict-" in path.name:
            continue
        start = int(offsets.get(path.name, 0))
        try:
            if path.stat().st_size < start:   # rotated or replaced: rescan
                start = 0
        except OSError:
            continue
        records, end = _read_spool(path, start)
        for rec in records:
            op = rec.get("op")
            if op in MUTATION_OPS:
                applied += _apply_mutation_locked(rec)
                continue
            if op == "append":
                applied += _apply_append_locked(rec)
                continue
            if op not in ("capture", "upsert"):
                log.error("gtd spool: unknown op %r (%s) in %s, skipped", op, rec.get("id"), path)
                continue
            try:
                cap = GtdCapture(**(rec.get("capture") or {}))
            except TypeError:
                log.error("gtd spool: bad capture record %s in %s", rec.get("id"), path)
                continue
            if rec.get("op") == "upsert":
                _, action = _upsert_locked(cap, item_id=rec.get("id"))
                applied += action != "unchanged"
            elif _capture_locked(cap, item_id=rec.get("id")):
                applied += 1
        if end != start:
            offsets[path.name] = end
            changed = True
    if changed:
        op_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = op_path.with_name(op_path.name + ".tmp")
        tmp.write_text(json.dumps(offsets, indent=2), encoding="utf-8")
        os.replace(tmp, op_path)
    if applied:
        log.info("gtd store: applied %d spooled op(s)", applied)
    return applied


def drain_spool() -> int:
    """Owner only: fold the other hosts' spools into the lists. 0 elsewhere."""
    if not is_owner():
        return 0
    from .coldstart import guard_store  # local import avoids an import cycle
    with _store_lock():
        guard_store("drain-spool")
        return _drain_spool_locked()


def capture(c: GtdCapture) -> str | None:
    """The single sink. Dedupe by (source, source_ref); normalize; append to the
    unified store. Returns the new item id, or None if it was a duplicate.
    The whole load-modify-save cycle runs under the store lock.

    On a non-owner host (see :func:`owner_host`) the capture goes to this host's
    spool instead, with its id assigned now, and the owner lands it.

    Before emitting, the cold-start guard refuses to write when this node is
    initialized but the store is empty (cold-start-before-restore), so an
    un-restored node cannot replicate empty state fleet-wide. See skos.coldstart.
    """
    if not is_owner():
        if c.source_ref and ((c.source, c.source_ref) in _seen_refs()
                             or (c.source, c.source_ref) in _spooled_refs()):
            return None
        item_id = uuid.uuid4().hex[:12]
        _spool("capture", c, item_id)
        return item_id
    from .coldstart import guard_store  # local import avoids an import cycle
    with _store_lock():
        guard_store("capture")
        _drain_spool_locked()
        return _capture_locked(c)


def _all_ids() -> set[str]:
    return {it.get("id") for fname in _ALL_FILES for it in _load(fname)}


def _capture_locked(c: GtdCapture, item_id: str | None = None) -> str | None:
    """capture() body; caller must hold the store lock. ``item_id`` is the id a
    non-owner host already handed out; a repeat of it is a no-op."""
    if c.source_ref and (c.source, c.source_ref) in _seen_refs():
        return None
    if item_id and item_id in _all_ids():
        return None
    item = {
        "id": item_id or uuid.uuid4().hex[:12],
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
    ``{created, unchanged, updated, completed}``, or ``spooled`` on a non-owner
    host (the owner applies it on its next drain). On ``unchanged`` it performs **no
    write**, the property that keeps polling idempotent and notifications quiet.

    The whole reconcile (find + patch + move) runs under the store lock, and a
    cross-file move is ordered write-then-delete: the destination is written
    first, so a crash in between duplicates rather than loses the item, and the
    next move self-heals the duplicate.

    Like :func:`capture`, this passes through the cold-start guard first so an
    un-restored node (initialized marker present, store empty) cannot emit and
    replicate empty state. See skos.coldstart.
    """
    if not is_owner():
        _, _, existing, _ = _find_item(c.source, c.source_ref) if c.source_ref else (None,) * 4
        item_id = existing["id"] if existing else uuid.uuid4().hex[:12]
        _spool("upsert", c, item_id)
        return item_id, "spooled"
    from .coldstart import guard_store  # local import avoids an import cycle
    with _store_lock():
        guard_store("upsert")
        _drain_spool_locked()
        return _upsert_locked(c)


def _upsert_locked(c: GtdCapture, item_id: str | None = None) -> tuple[str, str]:
    if not c.source_ref:
        return (_capture_locked(c, item_id) or item_id or ""), "created"

    fname, idx, existing, items = _find_item(c.source, c.source_ref)
    if existing is None:
        return (_capture_locked(c, item_id) or item_id or ""), "created"

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
