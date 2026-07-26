"""skos.coldstart - cold-start bootstrap ordering + empty-store guard.

The problem (card f15d086d): nothing defines what order a wiped or freshly
provisioned node comes back in. The unified GTD store lives at
``~/.skcapstone/coordination/gtd`` and is Syncthing-*replicated* (replication is
not restore). If skos runs and *emits* before the store has been restored or
synced, it writes onto an EMPTY store and Syncthing then propagates that empty
state fleet-wide, clobbering the real data on every other node.

This module adds two things:

1. A **node sentinel** (``node-initialized`` marker) that records "this node has
   been set up and previously had a real store". It is a *local, per-node* file
   kept OUTSIDE the synced store dir on purpose: it must not travel with the
   data it is guarding (see the runbook, ``docs/runbooks/skos-coldstart.md``).

2. An **empty-store guard** (:func:`guard_store`) that write paths call before
   they emit. The decision matrix:

   =============== ============ ==============================================
   marker present  store empty  outcome
   =============== ============ ==============================================
   yes             yes          **GUARD TRIPS** - dangerous cold-start-before-
                                restore. Refuse to emit so we do not clobber
                                other nodes with empty state.
   yes             no           proceed (normal steady state).
   no              yes          genuine fresh init - allowed (nothing to lose).
   no              no           proceed; opportunistically stamp the marker so
                                a *later* wipe-to-empty is caught.
   =============== ============ ==============================================

   The guard only ever *refuses* - it never deletes or truncates anything.

Override: set ``SKOS_ALLOW_EMPTY_STORE=1`` for a genuine fresh init / restore
drill / test where emitting onto an empty store is intended.
"""
from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .gtd_ingest import _ALL_FILES, _load, gtd_dir

log = logging.getLogger("skos.coldstart")

_MARKER_NAME = "node-initialized"
_TRUTHY = {"1", "true", "yes", "on"}


class ColdStartGuardError(RuntimeError):
    """Raised when the empty-store guard refuses an emit onto an initialized
    node whose store is empty (the dangerous cold-start-before-restore case)."""


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def allow_empty_override() -> bool:
    """True when the operator has explicitly authorized emitting onto an empty
    store (``SKOS_ALLOW_EMPTY_STORE``) - fresh init, restore drill, or tests."""
    return _env_truthy("SKOS_ALLOW_EMPTY_STORE")


def state_dir() -> Path:
    """The local, per-node state dir the sentinel lives in.

    Precedence: ``SKOS_STATE_DIR`` > ``$XDG_STATE_HOME/skos`` > ``~/.local/state/skos``.
    Deliberately NOT under the Syncthing-synced GTD store: the "this node is
    initialized" fact is local and must never replicate."""
    env = os.environ.get("SKOS_STATE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "skos"


def marker_path() -> Path:
    """Absolute path of the node-initialized sentinel.

    Precedence: ``SKOS_COLDSTART_MARKER`` (explicit full path) > ``state_dir()/node-initialized``."""
    env = os.environ.get("SKOS_COLDSTART_MARKER", "").strip()
    if env:
        return Path(env).expanduser()
    return state_dir() / _MARKER_NAME


def is_initialized() -> bool:
    """True if this node has been stamped as initialized."""
    return marker_path().exists()


def mark_initialized() -> Path:
    """Stamp this node as initialized (idempotent). Records host + UTC time so a
    human can see when/where the node was set up. Returns the marker path."""
    p = marker_path()
    if p.exists():
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"host={socket.gethostname()}\ninitialized_at={datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )
    log.info("coldstart: stamped node-initialized marker at %s", p)
    return p


def store_item_count() -> int:
    """Total GTD items across every store list file (0 == empty/absent)."""
    return sum(len(_load(fname)) for fname in _ALL_FILES)


def store_is_empty() -> bool:
    """True when the unified GTD store holds zero items (files absent or all
    empty lists). This is what an un-restored / un-synced cold store looks like."""
    return store_item_count() == 0


@dataclass
class ColdStartReport:
    """Snapshot of the cold-start decision for ``skos coldstart check``."""
    initialized: bool
    store_empty: bool
    item_count: int
    override: bool
    would_trip: bool
    reason: str
    marker: str
    store_dir: str


def evaluate() -> ColdStartReport:
    """Compute the guard decision without side effects (no marking, no raising)."""
    initialized = is_initialized()
    count = store_item_count()
    empty = count == 0
    override = allow_empty_override()

    if override:
        reason = "override: SKOS_ALLOW_EMPTY_STORE set - emit allowed regardless"
        would_trip = False
    elif initialized and empty:
        reason = (
            "DANGER: node is initialized but the store is EMPTY - "
            "cold-start-before-restore. Emitting now would replicate empty state."
        )
        would_trip = True
    elif not initialized and empty:
        reason = "genuine fresh init (no marker, empty store) - emit allowed"
        would_trip = False
    else:
        reason = "store populated - emit allowed"
        would_trip = False

    return ColdStartReport(
        initialized=initialized,
        store_empty=empty,
        item_count=count,
        override=override,
        would_trip=would_trip,
        reason=reason,
        marker=str(marker_path()),
        store_dir=str(gtd_dir()),
    )


def guard_store(op: str = "emit") -> None:
    """Guard a write/emit path. Raises :class:`ColdStartGuardError` in the
    dangerous cold-start-before-restore case; otherwise returns.

    On a populated store it opportunistically stamps the node-initialized marker
    so a *future* wipe-to-empty on this same node is caught. It never deletes or
    truncates store data - the only action is refuse-or-allow (plus that stamp).
    """
    report = evaluate()
    if report.would_trip:
        raise ColdStartGuardError(
            f"skos coldstart guard: refusing to {op} - {report.reason} "
            f"(store={report.store_dir}, marker={report.marker}). "
            f"Restore the store BEFORE first run (see docs/runbooks/skos-coldstart.md), "
            f"or set SKOS_ALLOW_EMPTY_STORE=1 for a genuine fresh init."
        )
    # Populated store on a not-yet-stamped node: record initialization so a
    # later empty state is recognized as dangerous rather than "fresh".
    if not report.store_empty and not report.initialized:
        try:
            mark_initialized()
        except OSError as e:  # marking is best-effort; never block a valid emit
            log.warning("coldstart: could not stamp node-initialized marker: %s", e)
