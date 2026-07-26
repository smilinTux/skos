"""Storage-placement policy engine.

Decides WHERE an ingested blob/asset should live (which node / store / tier)
from a declarative policy, tracks the decision in a catalog, and records the
location at ingest time. The policy vocabulary is deliberately git-annex
flavored so a store maps 1:1 onto a git-annex repository/remote and each rule
compiles into a preferred-content boolean expression.

Layers (all self-contained, no live git-annex calls here):

  1. Declarative schema + loader  ``placement.yaml`` -> :class:`PlacementPolicy`.
  2. Pure resolver               :func:`resolve_placement` (blob attrs -> target).
  3. Catalog                     JSON store (skos state subdir), locked +
                                 atomic write, upsert-by-blob-id via
                                 :func:`record_ingest_location`.
  4. preferred-content seam      :func:`preferred_content_expr` renders the
                                 git-annex ``wanted`` expression for a store.

Live ``git annex wanted <remote> <expr>`` wiring is intentionally deferred to a
follow-up (see card df328458); everything here is pure/offline and testable
without touching a real annex or any live store.

Conventions mirror :mod:`skos.gtd_ingest`: pydantic+yaml for the declarative
contract (as :mod:`skos.descriptor`), a flock'd JSON store with atomic replace
and loud quarantine of corrupt files.
"""
from __future__ import annotations

import fcntl
import fnmatch
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

log = logging.getLogger("skos.placement")


class PlacementError(ValueError):
    pass


# ── declarative schema ───────────────────────────────────────────────────────
class Store(BaseModel):
    """A physical destination: a node + tier, mapped onto a git-annex remote."""
    node: str                        # host/node the store lives on (dot158, ...)
    tier: str = "default"            # ssd | hdd | archive | ... (advisory)
    annex: str | None = None         # git-annex remote/repo name for this store
    description: str = ""


class Match(BaseModel):
    """Blob-attribute predicate for a rule. All present fields must hold (AND).

    Absent fields are ignored. An empty Match matches every blob (catch-all)."""
    mimetype: str | None = None      # fnmatch glob, e.g. "video/*"
    ext: str | None = None           # file extension w/o dot, case-insensitive
    source: str | None = None        # ingest source tag, exact
    min_size: int | None = None      # bytes, inclusive
    max_size: int | None = None      # bytes, inclusive
    any_tags: list[str] = Field(default_factory=list)   # blob has ANY of these
    all_tags: list[str] = Field(default_factory=list)   # blob has ALL of these

    def is_catchall(self) -> bool:
        return not any([
            self.mimetype, self.ext, self.source,
            self.min_size is not None, self.max_size is not None,
            self.any_tags, self.all_tags,
        ])


class Rule(BaseModel):
    name: str
    match: Match = Field(default_factory=Match)
    target: str                      # store name (must exist in policy.stores)


class PlacementPolicy(BaseModel):
    """Ordered rules + a mandatory default store. First matching rule wins."""
    version: int = 1
    stores: dict[str, Store]
    rules: list[Rule] = Field(default_factory=list)
    default: str                     # store name used when no rule matches

    @model_validator(mode="after")
    def _check_targets(self) -> PlacementPolicy:
        if self.default not in self.stores:
            raise ValueError(f"default store {self.default!r} not declared in stores")
        for r in self.rules:
            if r.target not in self.stores:
                raise ValueError(
                    f"rule {r.name!r} targets undeclared store {r.target!r}"
                )
        return self


class Placement(BaseModel):
    """Resolved destination for a blob."""
    store: str
    node: str
    tier: str
    annex: str | None = None
    rule: str                        # name of the rule that decided, or "<default>"


DEFAULT_RULE = "<default>"


# ── loader ───────────────────────────────────────────────────────────────────
def default_policy_path() -> Path:
    """``$SKOS_PLACEMENT_POLICY`` (explicit) else ``<data-root>/config/placement.yaml``."""
    env = os.environ.get("SKOS_PLACEMENT_POLICY", "").strip()
    if env:
        return Path(env).expanduser()
    from skos import paths  # local import avoids import cycle at module load
    return paths.subdir("config") / "placement.yaml"


def load_policy(path: str | Path | None = None) -> PlacementPolicy:
    """Load + validate a placement.yaml. Raises :class:`PlacementError` on any
    malformed file (bad YAML, schema violation, dangling store reference)."""
    p = Path(path) if path is not None else default_policy_path()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlacementError(f"placement policy not found: {p}") from exc
    except yaml.YAMLError as exc:
        raise PlacementError(f"invalid placement.yaml at {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PlacementError(f"placement.yaml must be a mapping, got {type(raw).__name__}")
    try:
        return PlacementPolicy.model_validate(raw)
    except ValidationError as exc:
        raise PlacementError(f"invalid placement policy at {p}: {exc}") from exc


# ── pure resolver ────────────────────────────────────────────────────────────
def _norm_ext(name_or_ext: str) -> str:
    e = name_or_ext.rsplit(".", 1)[-1] if "." in name_or_ext else name_or_ext
    return e.lower().lstrip(".")


def _matches(match: Match, attrs: dict[str, Any]) -> bool:
    """True iff every present predicate in ``match`` holds for ``attrs``.

    Recognized attrs: mimetype, ext (or filename), source, size, tags(list)."""
    if match.is_catchall():
        return True
    if match.mimetype is not None:
        mt = attrs.get("mimetype")
        if not (isinstance(mt, str) and fnmatch.fnmatch(mt, match.mimetype)):
            return False
    if match.ext is not None:
        raw = attrs.get("ext") or attrs.get("filename") or ""
        if _norm_ext(str(raw)) != _norm_ext(match.ext):
            return False
    if match.source is not None:
        if attrs.get("source") != match.source:
            return False
    size = attrs.get("size")
    if match.min_size is not None:
        if not isinstance(size, int) or size < match.min_size:
            return False
    if match.max_size is not None:
        if not isinstance(size, int) or size > match.max_size:
            return False
    if match.any_tags or match.all_tags:
        tags = set(attrs.get("tags") or [])
        if match.any_tags and tags.isdisjoint(match.any_tags):
            return False
        if match.all_tags and not set(match.all_tags).issubset(tags):
            return False
    return True


def resolve_placement(attrs: dict[str, Any], policy: PlacementPolicy) -> Placement:
    """Pure decision: first rule whose match holds wins; else the default store.

    ``attrs`` is a plain blob-attribute dict (mimetype, size, tags, source, ext,
    filename). No I/O, no catalog side effects."""
    chosen_rule = DEFAULT_RULE
    store_name = policy.default
    for rule in policy.rules:
        if _matches(rule.match, attrs):
            chosen_rule = rule.name
            store_name = rule.target
            break
    store = policy.stores[store_name]
    return Placement(
        store=store_name, node=store.node, tier=store.tier,
        annex=store.annex, rule=chosen_rule,
    )


# ── git-annex preferred-content seam (offline: renders the expr only) ─────────
def _rule_expr(match: Match) -> str:
    """Render one rule's match as a git-annex preferred-content sub-expression."""
    if match.is_catchall():
        return "anything"
    terms: list[str] = []
    if match.mimetype is not None:
        terms.append(f'mimeglob={match.mimetype}')
    if match.ext is not None:
        terms.append(f'include=*.{_norm_ext(match.ext)}')
    if match.min_size is not None:
        terms.append(f'largerthan={match.min_size}b')
    if match.max_size is not None:
        terms.append(f'smallerthan={match.max_size + 1}b')
    if match.source is not None:
        terms.append(f'metadata=source={match.source}')
    if match.any_tags:  # ANY -> OR of the tag predicates
        ors = " or ".join(f'metadata=tag={t}' for t in match.any_tags)
        terms.append(f"({ors})" if len(match.any_tags) > 1 else ors)
    for t in match.all_tags:  # ALL -> each conjoined
        terms.append(f'metadata=tag={t}')
    if not terms:  # only tag-less predicates rendered above; guard for safety
        return "anything"
    return "(" + " and ".join(terms) + ")" if len(terms) > 1 else terms[0]


def preferred_content_expr(policy: PlacementPolicy, store_name: str) -> str:
    """Render the git-annex ``wanted`` boolean expression for one store.

    A blob is *wanted* by ``store_name`` when the FIRST matching rule targets it,
    or (for the default store) when no earlier rule matched. This mirrors the
    first-match semantics of :func:`resolve_placement`. The returned string is a
    valid git-annex preferred-content expression; wiring it via
    ``git annex wanted`` is deferred to a follow-up."""
    if store_name not in policy.stores:
        raise PlacementError(f"unknown store {store_name!r}")
    prior: list[str] = []          # earlier rules' exprs (must NOT match)
    wanted: list[str] = []
    for rule in policy.rules:
        expr = _rule_expr(rule.match)
        if rule.target == store_name:
            guard = " and ".join(f"not {p}" for p in prior)
            wanted.append(f"({expr} and {guard})" if guard else expr)
        prior.append(expr)
    if store_name == policy.default:
        guard = " and ".join(f"not {p}" for p in prior)
        wanted.append(guard or "anything")
    if not wanted:
        return "nothing"
    return " or ".join(wanted) if len(wanted) > 1 else wanted[0]


# ── blob catalog (JSON store, locked, atomic) ────────────────────────────────
def catalog_path() -> Path:
    """``$SKOS_BLOB_CATALOG`` (explicit) else ``<data-root>/state/blob_catalog.json``."""
    env = os.environ.get("SKOS_BLOB_CATALOG", "").strip()
    if env:
        p = Path(env).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    from skos import paths
    d = paths.subdir("state")
    d.mkdir(parents=True, exist_ok=True)
    return d / "blob_catalog.json"


@contextmanager
def _catalog_lock():
    """Advisory flock over the whole catalog, held across load-modify-save."""
    lock_path = catalog_path().with_suffix(".json.lock")
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
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    qpath = p.with_name(f"{p.name}.corrupt-{ts}")
    n = 0
    while qpath.exists():  # pragma: no cover
        n += 1
        qpath = p.with_name(f"{p.name}.corrupt-{ts}.{n}")
    os.replace(p, qpath)
    log.error("blob catalog: corrupt file %s quarantined to %s (%s)", p, qpath, exc)


def _load_catalog() -> list[dict]:
    p = catalog_path()
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


def _save_catalog(items: list[dict]) -> None:
    target = catalog_path()
    d = target.parent
    payload = json.dumps(items, indent=2, ensure_ascii=False, default=str)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(d))
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


def record_ingest_location(
    blob_id: str,
    placement: Placement,
    meta: dict | None = None,
) -> dict:
    """Record (or update) where a blob landed. Upsert by ``blob_id``.

    Idempotent-by-key: re-recording the same ``blob_id`` overwrites its row in
    place (no duplicates), refreshing the placement/meta and bumping
    ``updated_at`` while preserving the original ``recorded_at``. Returns the
    stored row. Blob content attributes worth catching (size, hash, mimetype,
    source) are passed through ``meta`` and hoisted onto the row."""
    if not blob_id:
        raise PlacementError("blob_id is required")
    meta = dict(meta or {})
    now = datetime.now(timezone.utc).isoformat()
    with _catalog_lock():
        items = _load_catalog()
        row: dict[str, Any] = {
            "blob_id": blob_id,
            "store": placement.store,
            "node": placement.node,
            "tier": placement.tier,
            "annex": placement.annex,
            "rule": placement.rule,
            "size": meta.pop("size", None),
            "hash": meta.pop("hash", None),
            "mimetype": meta.pop("mimetype", None),
            "source": meta.pop("source", None),
            "meta": meta,
            "recorded_at": now,
            "updated_at": now,
        }
        for i, existing in enumerate(items):
            if existing.get("blob_id") == blob_id:
                row["recorded_at"] = existing.get("recorded_at", now)
                items[i] = row
                _save_catalog(items)
                return row
        items.append(row)
        _save_catalog(items)
    return row


def get_placement(blob_id: str) -> dict | None:
    """Read back a catalog row by ``blob_id`` (None if absent)."""
    for row in _load_catalog():
        if row.get("blob_id") == blob_id:
            return row
    return None


def list_placements() -> list[dict]:
    """All catalog rows, in insertion order."""
    return _load_catalog()


def store_blob(
    blob_id: str,
    attrs: dict[str, Any],
    policy: PlacementPolicy | None = None,
) -> dict:
    """The ``skos store`` primitive: resolve a blob's placement from policy and
    record it in the catalog in one call. Returns the catalog row.

    ``attrs`` carries both the match inputs (mimetype/size/tags/source/ext) and
    the content facts to catch (size/hash/mimetype/source flow into the row)."""
    pol = policy if policy is not None else load_policy()
    placement = resolve_placement(attrs, pol)
    meta = {
        k: attrs[k]
        for k in ("size", "hash", "mimetype", "source")
        if k in attrs and attrs[k] is not None
    }
    for k, v in attrs.items():
        if k not in ("size", "hash", "mimetype", "source", "tags", "ext", "filename"):
            meta[k] = v
    return record_ingest_location(blob_id, placement, meta)
