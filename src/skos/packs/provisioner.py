"""The pack provisioner (OPS1.2): execute a :class:`PackPlan` step by step,
idempotently and fail-safe, through an injected :class:`Effects` boundary.

This is the REVIEW half of the pack machinery. It owns dispatch, ordering
adherence, the all-or-nothing coupling rule, per-step state recording, and the
``install`` / ``remove`` / ``status`` orchestration. It does NOT itself touch the
DB, git, skvault, docker, or the fleet store: every side effect goes through the
:class:`Effects` object, so tests drive the whole control flow with a fake.

Coupling (Chef: "install one, get the other"): a pack is indivisible. There is no
step selection. On the first ``failed`` step the install STOPS (fail-safe), the
partial state is recorded, and the pack reads as unhealthy. A ``pending`` step
(a deferred seed, a disarmed guarded action) does NOT stop the install: the pack
records as ``partial`` and re-running resumes it idempotently.

A BLOCKED plan (unsatisfied ``requires`` gate) is never executed: every step is
recorded ``skipped`` and the install reports failure with the gate reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from skos.packs import state as _state
from skos.packs.effects import (
    DONE,
    FAILED,
    PENDING,
    SKIPPED,
    DefaultEffects,
    Effects,
    StepResult,
)
from skos.packs.loader import load_pack
from skos.packs.model import PackManifest
from skos.packs.planner import NodeFacts, plan_pack


@dataclass(frozen=True)
class ExecutedStep:
    """The recorded outcome of one executed (or skipped) plan step."""

    order: int
    kind: str
    status: str
    note: str

    def as_dict(self) -> dict:
        return {"order": self.order, "kind": self.kind, "status": self.status, "note": self.note}


@dataclass
class ProvisionReport:
    """The aggregate outcome of an install / remove run.

    Attributes:
        pack_id: The pack id.
        action: ``"install"`` or ``"remove"``.
        steps: Per-step executed outcomes, in execution order.
        status: The derived pack-level status (a :mod:`skos.packs.state` constant).
        blocked: True when an install was refused because the requires gate failed.
        gate_reasons: The gate reasons when blocked.
    """

    pack_id: str
    action: str
    steps: list[ExecutedStep] = field(default_factory=list)
    status: str = _state.STATUS_INSTALLED
    blocked: bool = False
    gate_reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the run produced no failed step and was not blocked."""
        return not self.blocked and self.status != _state.STATUS_FAILED

    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.status == DONE)
        pending = sum(1 for s in self.steps if s.status == PENDING)
        failed = sum(1 for s in self.steps if s.status == FAILED)
        skipped = sum(1 for s in self.steps if s.status == SKIPPED)
        return (
            f"{self.action} {self.pack_id}: status={self.status}  "
            f"done={done} pending={pending} failed={failed} skipped={skipped}"
        )


def install(
    manifest: PackManifest,
    pack_dir: Path,
    *,
    facts: NodeFacts | None = None,
    effects: Effects | None = None,
    dry_run: bool = False,
    record: bool = True,
) -> ProvisionReport:
    """Plan and execute a pack install, idempotently and fail-safe.

    Args:
        manifest: The parsed pack manifest.
        pack_dir: The pack's asset dir (resolves pack-relative fleet templates).
        facts: Node facts for the requires gate (empty node when None).
        effects: The side-effect boundary (a :class:`DefaultEffects` when None).
        dry_run: When True, effects describe instead of touching the world and no
            state is recorded.
        record: When True (and not dry-run), persist per-step state to packs.json.

    Returns:
        The :class:`ProvisionReport`.
    """
    effects = effects or DefaultEffects()
    plan = plan_pack(manifest, facts)

    report = ProvisionReport(pack_id=plan.pack_id, action="install")

    if plan.blocked:
        report.blocked = True
        report.gate_reasons = plan.gate.reasons()
        report.status = _state.STATUS_FAILED
        report.steps = [
            ExecutedStep(order=s.order, kind=s.kind, status=SKIPPED, note="requires gate not met")
            for s in plan.steps
        ]
        if record and not dry_run:
            _state.record(
                plan.pack_id,
                status=_state.STATUS_FAILED,
                steps=[s.as_dict() for s in report.steps],
            )
        return report

    stopped = False
    for step in plan.steps:
        if stopped:
            report.steps.append(
                ExecutedStep(step.order, step.kind, SKIPPED, "skipped after an earlier failure")
            )
            continue
        result = _dispatch(effects, step.kind, step.params, pack_dir, plan.pack_id, dry_run=dry_run)
        report.steps.append(ExecutedStep(step.order, step.kind, result.status, result.note))
        if result.status == FAILED:
            stopped = True

    # Completion: emit the (signed) manifest into the shell/Atlas modules dir. The
    # remove path deletes it symmetrically. Skipped if an earlier step failed.
    manifest_order = len(plan.steps)
    if stopped:
        report.steps.append(
            ExecutedStep(manifest_order, "manifest", SKIPPED, "skipped after an earlier failure")
        )
    else:
        emit = effects.emit_manifest(plan.pack_id, pack_dir, dry_run=dry_run)
        report.steps.append(ExecutedStep(manifest_order, "manifest", emit.status, emit.note))

    report.status = _state.status_from_steps([s.as_dict() for s in report.steps])
    if record and not dry_run:
        _state.record(
            plan.pack_id, status=report.status, steps=[s.as_dict() for s in report.steps]
        )
    return report


def remove(
    manifest: PackManifest,
    pack_dir: Path,
    *,
    effects: Effects | None = None,
    dry_run: bool = False,
    purge_db: bool = False,
    record: bool = True,
) -> ProvisionReport:
    """Reverse a pack's activation without destroying operational state.

    Deletes the pack's fleet objects (controllers stop scheduling) and the signed
    manifest (the shell drops the tab, the seat drops the adapter on next
    bootstrap), then marks the pack ``removed``. The ops schema/data, the ITIL/CMDB
    file stores, the content checkout, and skvault entries are NOT touched unless
    ``purge_db`` is set (which runs the documented DROP SCHEMA rollback).

    Args:
        manifest: The parsed pack manifest.
        pack_dir: The pack's asset dir.
        effects: The side-effect boundary (a :class:`DefaultEffects` when None).
        dry_run: Describe instead of touching the world.
        purge_db: Also run the documented ops-schema rollback.
        record: Persist the ``removed`` state when True (and not dry-run).

    Returns:
        The :class:`ProvisionReport`.
    """
    effects = effects or DefaultEffects()
    report = ProvisionReport(pack_id=manifest.id, action="remove")
    order = 0

    for step in manifest.steps:
        if step.kind == "fleet_objects":
            result = effects.remove_fleet_objects(step.params, dry_run=dry_run)
            report.steps.append(ExecutedStep(order, step.kind, result.status, result.note))
            order += 1
        elif step.kind == "sql_migration" and purge_db:
            result = effects.purge_db(step.params, dry_run=dry_run)
            report.steps.append(ExecutedStep(order, "purge_db", result.status, result.note))
            order += 1

    manifest_result = effects.remove_manifest(manifest.id, dry_run=dry_run)
    report.steps.append(ExecutedStep(order, "manifest", manifest_result.status, manifest_result.note))

    report.status = (
        _state.STATUS_FAILED
        if any(s.status == FAILED for s in report.steps)
        else _state.STATUS_REMOVED
    )
    if record and not dry_run and report.status != _state.STATUS_FAILED:
        _state.mark_removed(manifest.id)
    return report


def status(pack_id: str) -> dict:
    """Return a pack's recorded status, or a fresh 'not installed' record.

    Partial installs read as unhealthy (Chef's coupling rule): the returned
    ``healthy`` flag is True ONLY when every step is done and the pack is fully
    installed.
    """
    rec = _state.load(pack_id)
    if rec is None:
        return {"id": pack_id, "status": "not-installed", "healthy": False, "steps": []}
    healthy = rec.get("status") == _state.STATUS_INSTALLED
    return {
        "id": pack_id,
        "status": rec.get("status"),
        "healthy": healthy,
        "steps": rec.get("steps", []),
        "installed_at": rec.get("installed_at"),
        "updated_at": rec.get("updated_at"),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch(
    effects: Effects,
    kind: str,
    params,
    pack_dir: Path,
    pack_id: str,
    *,
    dry_run: bool,
) -> StepResult:
    """Route one plan step to its :class:`Effects` method."""
    if kind == "sql_migration":
        return effects.migrate(params, pack_dir, dry_run=dry_run)
    if kind == "db_roles":
        return effects.db_roles(params, dry_run=dry_run)
    if kind == "content_repo":
        return effects.content_repo(params, dry_run=dry_run)
    if kind == "seed":
        return effects.seed(params, dry_run=dry_run)
    if kind == "fleet_objects":
        return effects.fleet_objects(params, pack_dir, dry_run=dry_run)
    if kind == "doctor":
        return effects.doctor(params, pack_id, dry_run=dry_run)
    return StepResult(FAILED, f"no dispatch for step kind {kind!r}")


# ---------------------------------------------------------------------------
# Convenience: resolve a built-in pack by name and act on it.
# ---------------------------------------------------------------------------


def install_pack(
    pack_id: str,
    *,
    facts: NodeFacts | None = None,
    effects: Effects | None = None,
    dry_run: bool = False,
) -> ProvisionReport:
    """Resolve a built-in pack by name and install it."""
    manifest, pack_dir = load_pack(pack_id)
    return install(manifest, pack_dir, facts=facts, effects=effects, dry_run=dry_run)


def remove_pack(
    pack_id: str,
    *,
    effects: Effects | None = None,
    dry_run: bool = False,
    purge_db: bool = False,
) -> ProvisionReport:
    """Resolve a built-in pack by name and remove it."""
    manifest, pack_dir = load_pack(pack_id)
    return remove(manifest, pack_dir, effects=effects, dry_run=dry_run, purge_db=purge_db)


__all__ = [
    "ExecutedStep",
    "ProvisionReport",
    "install",
    "remove",
    "status",
    "install_pack",
    "remove_pack",
]
