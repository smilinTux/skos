"""Per-pack install state, persisted under ``$SK_DATA_ROOT/registry/packs.json``.

The topology registry (:mod:`skos.registry`) keys ``installed.json`` by app name
with an ``{adapter, ref}`` shape. Pack installs carry a richer per-STEP state
(the OPS1.2 requirement), so they live in a sibling ``packs.json`` rather than
overloading that shape. Each pack record captures the overall status plus the
status of every step, so a partial install is legible as partial (Chef's
coupling rule: status reports partial-install as unhealthy).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from skos import paths

#: A pack whose every step is ``done``.
STATUS_INSTALLED = "installed"
#: A pack with at least one ``pending`` (deferred) step and no failures.
STATUS_PARTIAL = "partial"
#: A pack with at least one ``failed`` step.
STATUS_FAILED = "failed"
#: A pack whose activation was reversed by ``skos remove``.
STATUS_REMOVED = "removed"


def _file():
    paths.ensure_tree()
    return paths.subdir("registry") / "packs.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_all() -> dict[str, Any]:
    """Return every pack record keyed by pack id (empty when none installed)."""
    f = _file()
    return json.loads(f.read_text()) if f.exists() else {}


def load(pack_id: str) -> dict[str, Any] | None:
    """Return the record for one pack, or None when it has never been installed."""
    return load_all().get(pack_id)


def record(pack_id: str, *, status: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Create or update a pack's install record.

    Args:
        pack_id: The pack id (e.g. ``skbrain``).
        status: One of the module-level STATUS_* constants.
        steps: Per-step state dicts (``{"order", "kind", "status", "note"}``).

    Returns:
        The stored record.
    """
    items = load_all()
    existing = items.get(pack_id, {})
    rec = {
        "id": pack_id,
        "status": status,
        "steps": steps,
        "installed_at": existing.get("installed_at") or _now(),
        "updated_at": _now(),
    }
    items[pack_id] = rec
    _file().write_text(json.dumps(items, indent=2, sort_keys=True) + "\n")
    return rec


def mark_removed(pack_id: str) -> dict[str, Any] | None:
    """Flag a pack ``removed`` (state kept for audit), or None if never installed."""
    items = load_all()
    rec = items.get(pack_id)
    if rec is None:
        return None
    rec["status"] = STATUS_REMOVED
    rec["updated_at"] = _now()
    items[pack_id] = rec
    _file().write_text(json.dumps(items, indent=2, sort_keys=True) + "\n")
    return rec


def status_from_steps(step_states: list[dict[str, Any]]) -> str:
    """Derive the pack-level status from its per-step states (coupling rule).

    A single failed step makes the pack FAILED; any pending step (with no
    failures) makes it PARTIAL; only an all-``done`` pack is INSTALLED.
    """
    statuses = {s.get("status") for s in step_states}
    if STATUS_FAILED in statuses or "failed" in statuses:
        return STATUS_FAILED
    if "pending" in statuses:
        return STATUS_PARTIAL
    return STATUS_INSTALLED


__all__ = [
    "STATUS_INSTALLED",
    "STATUS_PARTIAL",
    "STATUS_FAILED",
    "STATUS_REMOVED",
    "load_all",
    "load",
    "record",
    "mark_removed",
    "status_from_steps",
]
