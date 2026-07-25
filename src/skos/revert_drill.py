"""Revert drill for skos-applied changes (card 681514a5).

skos autopilot/deploy can APPLY changes to durable state (config, adapters,
GTD lists, the model registry). Applying is only half of a safe deploy: a bad
autopilot action has to be undo-able. This module proves an applied change can
be ROLLED BACK to the exact pre-change known-good tree, and packages that proof
as a repeatable **drill** (a fire drill for deploys).

Scope. "Revert" here means restoring durable **state/config files** that a
change touched back to their pre-change bytes, using a point-in-time
``skos.backup`` snapshot taken *before* the change as the known-good baseline.
(Autopilot's other revert surface, a git-merge revert, already lives in
``skos.autopilot.engineering.revert``; this module is the state/config side.)

Two pieces:

``revert_target(snapshot, label, target)``
    The explicit revert action. Restore one labeled source from a pre-change
    snapshot back over ``target`` **in place**: rewrite every snapshotted file
    to its captured bytes (undoing edits and restoring deletions) and remove any
    file the change *added* (present under ``target``, absent from the
    snapshot), so ``target`` returns to the exact pre-change tree. It is never
    fired automatically; a caller passes a concrete snapshot and target.

``run_drill(scratch_dir)``
    A self-contained exercise: seed a scratch target standing in for durable
    skos state, snapshot it (the baseline), apply a change (edit + add +
    delete), revert, and assert the target returns to the baseline byte-for-byte.
    It writes ONLY under ``scratch_dir`` and never touches live state.

CLI: ``skos revert-drill run``. Runbook: ``docs/runbooks/revert-drill.md``.
"""
from __future__ import annotations

import os
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from . import backup

_PAYLOAD = "payload"


def _source_entry(snapshot_path: str | os.PathLike, label: str) -> dict:
    """Return the manifest entry for ``label`` or raise a clear error. Reading
    the manifest first also surfaces a missing/unreadable snapshot as a clear
    error before any write."""
    manifest = backup.read_manifest(snapshot_path)
    src = next((s for s in manifest.get("sources", []) if s.get("label") == label), None)
    if src is None:
        raise ValueError(f"snapshot has no source labeled {label!r}")
    return src


def revert_target(snapshot_path: str | os.PathLike,
                  label: str,
                  target_dir: str | os.PathLike) -> dict:
    """Restore the pre-change tree for ``label`` back over ``target_dir``, in
    place, and return ``{"restored": [...], "removed": [...]}``.

    Every file captured in the snapshot is rewritten to its snapshotted bytes
    (undoing edits, recreating deletions); every file present under
    ``target_dir`` that the snapshot does NOT contain is deleted (undoing
    additions). After this call ``target_dir`` byte-matches the tree captured in
    the snapshot.

    Explicit revert: callers pass a concrete snapshot and target. For a drill
    the target is a scratch dir, never live state.
    """
    target = Path(target_dir)
    if not target.exists():
        raise ValueError(f"revert target does not exist: {target}")
    src = _source_entry(snapshot_path, label)  # clear error if snapshot/label bad

    snap_rels = {f["rel"] for f in src["files"]}
    restored: list[str] = []
    removed: list[str] = []

    troot = target.resolve()
    with tarfile.open(str(snapshot_path), "r:gz") as tar:
        for fmeta in src["files"]:
            out = target / fmeta["rel"]
            if os.path.relpath(out.resolve(), troot).startswith(".."):
                raise ValueError(f"unsafe path in snapshot: {fmeta['rel']}")
            member = tar.extractfile(fmeta["arc"])
            if member is None:
                raise ValueError(f"snapshot missing member {fmeta['arc']}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(member.read())
            restored.append(fmeta["rel"])

    # undo additions: any file the change created that the baseline lacked
    for p in sorted(target.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(target).as_posix()
        if rel not in snap_rels:
            p.unlink()
            removed.append(rel)

    return {"restored": sorted(restored), "removed": sorted(removed)}


@dataclass
class DrillResult:
    """Outcome of :func:`run_drill`. ``ok`` iff the reverted target matched the
    pre-change baseline byte-for-byte (no changed files, no leftover additions)."""
    ok: bool
    baseline_files: int
    reverted: dict
    mismatches: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)


# A tiny stand-in for the durable state a skos change would touch. Nested so the
# drill exercises subdirectories, not just top-level files.
_BASELINE: dict[str, str] = {
    "config.yaml": "adapters:\n  order: on\n  email: on\n",
    "adapters/order.py": "# order adapter (known-good)\n",
}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def run_drill(scratch_dir: str | os.PathLike) -> DrillResult:
    """Run a self-contained revert drill under ``scratch_dir`` and return a
    :class:`DrillResult`.

    Steps: seed a scratch target with a known-good tree, snapshot it (the
    pre-change baseline), apply a change (edit ``config.yaml``, add
    ``adapters/new_adapter.py``, delete ``adapters/order.py``), revert via
    :func:`revert_target`, then assert the target returns to the baseline
    byte-for-byte. Writes ONLY under ``scratch_dir``; never touches live state.
    """
    scratch = Path(scratch_dir)
    target = scratch / "target"
    target.mkdir(parents=True, exist_ok=True)

    # known-good pre-change state
    for rel, text in _BASELINE.items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    baseline = _tree_bytes(target)

    # snapshot BEFORE the change: the known-good baseline
    snap = backup.snapshot(scratch / "snaps",
                           sources=[backup.Source("target", target, "dir")])

    # ---- a skos change is applied to the target ----
    (target / "config.yaml").write_text("adapters:\n  order: OFF\n")   # edit
    (target / "adapters" / "new_adapter.py").write_text("# added by change\n")  # add
    (target / "adapters" / "order.py").unlink()                       # delete

    # ---- REVERT (explicit) ----
    reverted = revert_target(snap, "target", target)

    # ---- assert byte-for-byte return to baseline ----
    now = _tree_bytes(target)
    mismatches = sorted(k for k in baseline if now.get(k) != baseline[k])
    unexpected = sorted(k for k in now if k not in baseline)
    return DrillResult(
        ok=not mismatches and not unexpected,
        baseline_files=len(baseline),
        reverted=reverted,
        mismatches=mismatches,
        unexpected=unexpected,
    )
