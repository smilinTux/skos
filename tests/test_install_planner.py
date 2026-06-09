"""Tests for skos.install.planner — resolve + order an install plan."""
from __future__ import annotations

import pytest
from skos.install.planner import InstallPlan, InstallStep, plan, PlanError
from skos.install.profiles import InstallProfile


class TestInstallStep:
    def test_fields(self):
        step = InstallStep(capability="capauth", adapter="capauth")
        assert step.capability == "capauth"
        assert step.adapter == "capauth"


class TestInstallPlan:
    def test_steps_attribute(self):
        steps = [InstallStep(capability="capauth", adapter="capauth")]
        p = InstallPlan(profile="local", steps=steps)
        assert len(p.steps) == 1
        assert p.profile == "local"

    def test_capability_names(self):
        steps = [
            InstallStep(capability="capauth", adapter="capauth"),
            InstallStep(capability="skvault", adapter="vault-file"),
        ]
        p = InstallPlan(profile="local", steps=steps)
        assert p.capability_names() == ["capauth", "skvault"]


class TestPlan:
    def test_plan_local_defaults(self):
        """plan() with LOCAL profile + None caps uses the profile's recommended set."""
        p = plan(InstallProfile.LOCAL, capabilities=None)
        assert isinstance(p, InstallPlan)
        assert p.profile == InstallProfile.LOCAL.value
        assert len(p.steps) > 0
        # All recommended caps are present
        from skos.install.profiles import recommended
        rec = set(recommended(InstallProfile.LOCAL))
        assert rec == set(p.capability_names())

    def test_plan_resolves_adapter(self):
        """Each step's adapter is a real resolver result."""
        p = plan(InstallProfile.LOCAL, capabilities=["skdata"])
        assert len(p.steps) == 1
        assert p.steps[0].capability == "skdata"
        # local default for skdata is postgres
        assert p.steps[0].adapter == "postgres"

    def test_plan_custom_caps(self):
        p = plan(InstallProfile.LOCAL, capabilities=["capauth", "skfence"])
        names = [s.capability for s in p.steps]
        assert "capauth" in names
        assert "skfence" in names
        assert len(p.steps) == 2

    def test_plan_cluster_adapters(self):
        """Cluster profile resolves same defaults (no profile overrides in catalog yet)."""
        p = plan(InstallProfile.CLUSTER, capabilities=["skobject"])
        assert p.steps[0].adapter == "garage"

    def test_plan_unknown_capability_raises(self):
        with pytest.raises(PlanError, match="Unknown capability"):
            plan(InstallProfile.LOCAL, capabilities=["nope"])

    def test_plan_order_is_deterministic(self):
        """Same input produces the same ordered output."""
        caps = ["skdata", "capauth", "skfence", "skmon"]
        p1 = plan(InstallProfile.LOCAL, capabilities=caps)
        p2 = plan(InstallProfile.LOCAL, capabilities=caps)
        assert [s.capability for s in p1.steps] == [s.capability for s in p2.steps]

    def test_plan_no_duplicates(self):
        """Even if a cap appears twice, result has it once."""
        p = plan(InstallProfile.LOCAL, capabilities=["skdata", "skdata"])
        names = [s.capability for s in p.steps]
        assert names.count("skdata") == 1

    def test_empty_caps_raises(self):
        """Empty list (not None) means nothing to install — raise PlanError."""
        with pytest.raises(PlanError, match="empty"):
            plan(InstallProfile.LOCAL, capabilities=[])
