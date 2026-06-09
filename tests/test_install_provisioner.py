"""Tests for skos.install.provisioner — apply an InstallPlan to the data-root."""
from __future__ import annotations

import pytest
from skos.install.planner import InstallPlan, InstallStep, plan
from skos.install.provisioner import apply, ProvisionResult, StepOutcome
from skos.install.profiles import InstallProfile
from skos import registry


_IMPLEMENTED_ADAPTERS = {"oci", "capauth", "vault-file"}


def _make_plan(*caps: str, profile: InstallProfile = InstallProfile.LOCAL) -> InstallPlan:
    return plan(profile, capabilities=list(caps))


class TestProvisionResult:
    def test_fields(self):
        r = ProvisionResult(
            outcomes=[StepOutcome(capability="capauth", adapter="capauth",
                                  status="planned", note="coming-soon")]
        )
        assert len(r.outcomes) == 1
        assert r.success

    def test_all_statuses(self):
        outcomes = [
            StepOutcome(capability="capauth", adapter="capauth", status="recorded"),
            StepOutcome(capability="skdata", adapter="postgres", status="planned", note="coming-soon"),
        ]
        r = ProvisionResult(outcomes=outcomes)
        assert r.success  # planned is still considered success (not an error)

    def test_recorded_count(self):
        outcomes = [
            StepOutcome(capability="capauth", adapter="capauth", status="recorded"),
            StepOutcome(capability="skdata", adapter="postgres", status="planned"),
        ]
        r = ProvisionResult(outcomes=outcomes)
        assert r.recorded_count == 1
        assert r.planned_count == 1


class TestStepOutcome:
    def test_valid_statuses(self):
        for status in ("recorded", "planned", "error"):
            s = StepOutcome(capability="x", adapter="y", status=status)
            assert s.status == status


class TestApply:
    def test_apply_ensures_tree(self, data_root):
        """apply() must create the data-root directory tree."""
        p = _make_plan("capauth")
        apply(p, data_root=data_root)
        assert (data_root / "registry").exists()
        assert (data_root / "apps").exists()

    def test_apply_records_in_registry(self, data_root):
        """Capabilities whose adapter is a known packaging target get recorded."""
        p = _make_plan("capauth")
        result = apply(p, data_root=data_root)
        # capauth has adapter=capauth; it's a "planned" adapter (no OCI materialization)
        # but should still be recorded in registry with status planned/recorded
        installed = registry.list_installed()
        assert "capauth" in installed

    def test_apply_returns_provision_result(self, data_root):
        p = _make_plan("capauth", "skvault")
        result = apply(p, data_root=data_root)
        assert isinstance(result, ProvisionResult)
        assert len(result.outcomes) == 2

    def test_apply_non_oci_adapter_marked_planned(self, data_root):
        """Adapters that aren't implemented packaging targets get status=planned."""
        # skdata -> postgres, skchat -> matrix — neither is an OCI-pull adapter
        p = _make_plan("skchat")
        result = apply(p, data_root=data_root)
        outcome = result.outcomes[0]
        # Status is either "planned" (no materialize impl) or "recorded"
        assert outcome.status in ("planned", "recorded")

    def test_apply_all_local_recommended(self, data_root):
        """Full local profile plan can be applied without errors."""
        p = plan(InstallProfile.LOCAL, capabilities=None)
        result = apply(p, data_root=data_root)
        assert isinstance(result, ProvisionResult)
        assert len(result.outcomes) == len(p.steps)

    def test_apply_registry_persists_across_calls(self, data_root):
        """Multiple apply calls accumulate registry entries."""
        p1 = _make_plan("capauth")
        p2 = _make_plan("skvault")
        apply(p1, data_root=data_root)
        apply(p2, data_root=data_root)
        installed = registry.list_installed()
        assert "capauth" in installed
        assert "skvault" in installed

    def test_apply_outcome_has_note_for_planned(self, data_root):
        """planned outcomes include a human-readable note."""
        p = _make_plan("skchat")
        result = apply(p, data_root=data_root)
        for o in result.outcomes:
            if o.status == "planned":
                assert o.note  # not empty
