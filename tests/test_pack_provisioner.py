"""Tests for skos.packs.provisioner: step dispatch, coupling, state (OPS1.2).

Every side effect is mocked via a FakeEffects, so these exercise the control
flow (dispatch, ordering adherence, fail-safe stop, pending handling, state
recording, blocked-plan refusal, and the remove path) with NO Postgres, docker,
git, skvault, or network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from skos.packs import state as _state
from skos.packs.effects import DONE, FAILED, PENDING, SKIPPED, StepResult
from skos.packs.loader import load_pack
from skos.packs.model import PackManifest, PackStep, Requires
from skos.packs.planner import NodeFacts
from skos.packs.provisioner import install, remove, status


def _facts() -> NodeFacts:
    return NodeFacts(
        capabilities=frozenset({"skmem-pg"}),
        packages={"skcapstone": "0.16", "skos": "0.3", "skmemory": "0.12"},
    )


class FakeEffects:
    """Records dispatch order; returns a configurable result per kind."""

    def __init__(self, results: dict[str, StepResult] | None = None):
        self.results = results or {}
        self.calls: list[str] = []
        self.dry_runs: list[bool] = []

    def _result(self, kind: str) -> StepResult:
        self.calls.append(kind)
        return self.results.get(kind, StepResult(DONE, f"{kind} ok"))

    def migrate(self, params, pack_dir, *, dry_run):
        self.dry_runs.append(dry_run)
        return self._result("sql_migration")

    def db_roles(self, params, *, dry_run):
        return self._result("db_roles")

    def content_repo(self, params, *, dry_run):
        return self._result("content_repo")

    def seed(self, params, *, dry_run):
        return self._result("seed")

    def fleet_objects(self, params, pack_dir, *, dry_run):
        return self._result("fleet_objects")

    def doctor(self, params, pack_id, *, dry_run):
        return self._result("doctor")

    def emit_manifest(self, pack_id, pack_dir, *, dry_run):
        self.emitted = getattr(self, "emitted", 0) + 1
        return self.results.get("emit_manifest", StepResult(DONE, "manifest emitted"))

    # reverse
    def remove_fleet_objects(self, params, *, dry_run):
        self.calls.append("remove_fleet_objects")
        return self.results.get("remove_fleet_objects", StepResult(DONE, "deleted"))

    def remove_manifest(self, pack_id, *, dry_run):
        self.calls.append("remove_manifest")
        return self.results.get("remove_manifest", StepResult(DONE, "removed"))

    def purge_db(self, params, *, dry_run):
        self.calls.append("purge_db")
        return self.results.get("purge_db", StepResult(DONE, "purged"))


@pytest.fixture
def skbrain() -> tuple[PackManifest, Path]:
    return load_pack("skbrain")


class TestDispatch:
    def test_all_steps_dispatched_in_order(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects()
        report = install(manifest, pack_dir, facts=_facts(), effects=fx)
        assert fx.calls == [
            "sql_migration",
            "db_roles",
            "content_repo",
            "seed",
            "seed",
            "seed",
            "fleet_objects",
            "doctor",
        ]
        assert report.ok
        assert report.status == _state.STATUS_INSTALLED
        assert fx.emitted == 1  # manifest emitted on completion
        assert report.steps[-1].kind == "manifest"

    def test_conforms_to_effects_protocol(self):
        from skos.packs.effects import Effects

        assert isinstance(FakeEffects(), Effects)


class TestCoupling:
    def test_failure_stops_and_marks_failed(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects({"content_repo": StepResult(FAILED, "boom")})
        report = install(manifest, pack_dir, facts=_facts(), effects=fx)
        # dispatch stops right after the failing content_repo step
        assert fx.calls == ["sql_migration", "db_roles", "content_repo"]
        assert report.status == _state.STATUS_FAILED
        assert not report.ok
        # remaining steps recorded as skipped
        statuses = [s.status for s in report.steps]
        assert statuses[:3] == [DONE, DONE, FAILED]
        assert all(s == SKIPPED for s in statuses[3:])

    def test_pending_does_not_stop_but_marks_partial(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects({"seed": StepResult(PENDING, "deferred")})
        report = install(manifest, pack_dir, facts=_facts(), effects=fx)
        # all steps still dispatched
        assert fx.calls.count("seed") == 3
        assert "doctor" in fx.calls
        assert report.status == _state.STATUS_PARTIAL
        assert report.ok  # partial is not a failure

    def test_manifest_emit_failure_marks_failed(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects({"emit_manifest": StepResult(FAILED, "modules dir unwritable")})
        report = install(manifest, pack_dir, facts=_facts(), effects=fx)
        assert report.status == _state.STATUS_FAILED
        assert report.steps[-1].kind == "manifest"
        assert report.steps[-1].status == FAILED


class TestBlockedPlan:
    def test_blocked_plan_is_not_executed(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects()
        # empty facts -> requires gate fails
        report = install(manifest, pack_dir, facts=NodeFacts(), effects=fx)
        assert report.blocked
        assert fx.calls == []  # NOTHING executed
        assert report.status == _state.STATUS_FAILED
        assert all(s.status == SKIPPED for s in report.steps)
        assert report.gate_reasons  # populated

    def test_blocked_records_failed_state(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        install(manifest, pack_dir, facts=NodeFacts(), effects=FakeEffects())
        assert status("skbrain")["status"] == _state.STATUS_FAILED


class TestDryRun:
    def test_dry_run_passes_flag_and_records_nothing(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects()
        install(manifest, pack_dir, facts=_facts(), effects=fx, dry_run=True)
        assert all(fx.dry_runs)  # migrate saw dry_run=True
        # nothing persisted
        assert _state.load("skbrain") is None


class TestState:
    def test_state_recorded_per_step(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        install(manifest, pack_dir, facts=_facts(), effects=FakeEffects())
        rec = _state.load("skbrain")
        assert rec["status"] == _state.STATUS_INSTALLED
        # the declared steps plus the trailing manifest-emission completion step
        assert len(rec["steps"]) == len(manifest.steps) + 1
        assert rec["steps"][0]["kind"] == "sql_migration"
        assert rec["steps"][-1]["kind"] == "manifest"

    def test_idempotent_reinstall(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        install(manifest, pack_dir, facts=_facts(), effects=FakeEffects())
        first = _state.load("skbrain")["installed_at"]
        install(manifest, pack_dir, facts=_facts(), effects=FakeEffects())
        second = _state.load("skbrain")
        # installed_at preserved across reinstall; still installed
        assert second["installed_at"] == first
        assert second["status"] == _state.STATUS_INSTALLED


class TestStatus:
    def test_not_installed(self, data_root):
        st = status("skbrain")
        assert st["status"] == "not-installed"
        assert st["healthy"] is False

    def test_partial_is_unhealthy(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        install(
            manifest,
            pack_dir,
            facts=_facts(),
            effects=FakeEffects({"seed": StepResult(PENDING, "deferred")}),
        )
        st = status("skbrain")
        assert st["status"] == _state.STATUS_PARTIAL
        assert st["healthy"] is False  # coupling: partial is unhealthy

    def test_full_install_is_healthy(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        install(manifest, pack_dir, facts=_facts(), effects=FakeEffects())
        assert status("skbrain")["healthy"] is True


class TestRemove:
    def test_remove_deletes_fleet_and_manifest(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        install(manifest, pack_dir, facts=_facts(), effects=FakeEffects())
        fx = FakeEffects()
        report = remove(manifest, pack_dir, effects=fx)
        assert "remove_fleet_objects" in fx.calls
        assert "remove_manifest" in fx.calls
        assert "purge_db" not in fx.calls  # not by default
        assert report.status == _state.STATUS_REMOVED
        assert status("skbrain")["status"] == _state.STATUS_REMOVED

    def test_remove_purge_db_runs_rollback(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects()
        remove(manifest, pack_dir, effects=fx, purge_db=True)
        assert "purge_db" in fx.calls

    def test_remove_failure_reports_failed(self, skbrain, data_root):
        manifest, pack_dir = skbrain
        fx = FakeEffects({"remove_manifest": StepResult(FAILED, "locked")})
        report = remove(manifest, pack_dir, effects=fx)
        assert report.status == _state.STATUS_FAILED
        assert not report.ok
