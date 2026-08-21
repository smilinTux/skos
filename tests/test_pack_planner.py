"""Tests for skos.packs.planner: the PURE pack planner (OPS1.1).

Order + gate + dry-run, all with INJECTED node facts and no side effects.
"""
from __future__ import annotations

import pytest

from skos.packs.loader import load_manifest_dict
from skos.packs.model import PackError, PackManifest, PackStep, Requires
from skos.packs.planner import GateResult, NodeFacts, PackPlan, plan_pack


@pytest.fixture
def skbrain() -> PackManifest:
    return PackManifest.from_dict(load_manifest_dict("skbrain"))


def _satisfying_facts() -> NodeFacts:
    return NodeFacts(
        capabilities=frozenset({"skmem-pg"}),
        packages={"skcapstone": "0.16.0", "skos": "0.3.0", "skmemory": "0.12.1"},
    )


class TestOrdering:
    def test_canonical_order(self, skbrain):
        plan = plan_pack(skbrain, _satisfying_facts())
        kinds = [s.kind for s in plan.steps]
        # schema -> roles -> content -> seeds(x2) -> fleet -> doctor
        assert kinds == [
            "sql_migration",
            "db_roles",
            "content_repo",
            "seed",
            "seed",
            "fleet_objects",
            "doctor",
        ]
        assert [s.order for s in plan.steps] == list(range(len(plan.steps)))

    def test_out_of_order_manifest_is_reordered(self):
        # doctor + db_roles declared BEFORE the migration; planner reorders them.
        manifest = PackManifest(
            id="p",
            schema_version="1.2",
            requires=Requires(),
            steps=(
                PackStep("doctor", {"checks": ["x:one"]}, index=0),
                PackStep("db_roles", {"logins": {"a": "b"}, "password_source": "skvault"}, index=1),
                PackStep("sql_migration", {"db": "d", "script": "s"}, index=2),
            ),
            knowledge=None,
            signed=False,
            raw={},
        )
        kinds = [s.kind for s in plan_pack(manifest).steps]
        assert kinds == ["sql_migration", "db_roles", "doctor"]

    def test_stable_within_kind(self, skbrain):
        # The two seeds keep their declaration order.
        plan = plan_pack(skbrain, _satisfying_facts())
        seeds = [s for s in plan.steps if s.kind == "seed"]
        cmds = [s.params["cmd"][0] for s in seeds]
        assert cmds == ["skoperator", "skbrain"]

    def test_deterministic(self, skbrain):
        f = _satisfying_facts()
        a = [s.kind for s in plan_pack(skbrain, f).steps]
        b = [s.kind for s in plan_pack(skbrain, f).steps]
        assert a == b


class TestDependencyValidation:
    def test_db_roles_without_migration_raises(self):
        manifest = PackManifest(
            id="p",
            schema_version="1.2",
            requires=Requires(),
            steps=(
                PackStep("db_roles", {"logins": {"a": "b"}, "password_source": "skvault"}, index=0),
            ),
            knowledge=None,
            signed=False,
            raw={},
        )
        with pytest.raises(PackError, match="no sql_migration"):
            plan_pack(manifest)


class TestGate:
    def test_satisfied(self, skbrain):
        plan = plan_pack(skbrain, _satisfying_facts())
        assert plan.gate.satisfied
        assert not plan.blocked
        assert plan.gate.reasons() == ()

    def test_missing_capability(self, skbrain):
        facts = NodeFacts(
            capabilities=frozenset(),
            packages={"skcapstone": "0.16", "skos": "0.3", "skmemory": "0.12"},
        )
        plan = plan_pack(skbrain, facts)
        assert plan.blocked
        assert "skmem-pg" in plan.gate.missing_capabilities

    def test_package_too_old(self, skbrain):
        facts = NodeFacts(
            capabilities=frozenset({"skmem-pg"}),
            packages={"skcapstone": "0.15.0", "skos": "0.3", "skmemory": "0.12"},
        )
        plan = plan_pack(skbrain, facts)
        assert plan.blocked
        unmet = {u.package for u in plan.gate.unmet_packages}
        assert "skcapstone" in unmet
        assert any("does not satisfy" in r for r in plan.gate.reasons())

    def test_package_absent(self, skbrain):
        facts = NodeFacts(
            capabilities=frozenset({"skmem-pg"}),
            packages={"skcapstone": "0.16", "skos": "0.3"},  # skmemory missing
        )
        plan = plan_pack(skbrain, facts)
        assert plan.blocked
        assert any(u.package == "skmemory" and u.found is None for u in plan.gate.unmet_packages)

    def test_none_facts_blocks_when_requires(self, skbrain):
        plan = plan_pack(skbrain, None)
        assert plan.blocked

    def test_no_requires_always_satisfied(self):
        manifest = PackManifest(
            id="p",
            schema_version="1.2",
            requires=Requires(),
            steps=(PackStep("doctor", {"checks": ["x"]}, index=0),),
            knowledge=None,
            signed=False,
            raw={},
        )
        assert plan_pack(manifest, None).gate.satisfied

    def test_invalid_constraint_is_unmet(self):
        manifest = PackManifest(
            id="p",
            schema_version="1.2",
            requires=Requires(packages={"skos": "not-a-constraint"}),
            steps=(PackStep("doctor", {"checks": ["x"]}, index=0),),
            knowledge=None,
            signed=False,
            raw={},
        )
        plan = plan_pack(manifest, NodeFacts(packages={"skos": "0.3"}))
        assert plan.blocked
        assert any("invalid version constraint" in r for r in plan.gate.reasons())


class TestDryRun:
    def test_dry_run_lists_every_step(self, skbrain):
        text = plan_pack(skbrain, _satisfying_facts()).dry_run()
        assert "install plan: skbrain" in text
        assert "apply skmemory:deploy/skmem-pg/03-ops-namespace.sql" in text
        assert "create login roles" in text
        assert text.count("\n") >= len(skbrain.steps)

    def test_dry_run_shows_blocked(self, skbrain):
        text = plan_pack(skbrain, None).dry_run()
        assert "BLOCKED" in text
        assert "missing capability: skmem-pg" in text


class TestTypes:
    def test_gate_result_reasons_ordering(self):
        g = GateResult(satisfied=False, missing_capabilities=("cap-a",), unmet_packages=())
        assert g.reasons() == ("missing capability: cap-a",)

    def test_pack_plan_is_frozen(self, skbrain):
        plan = plan_pack(skbrain, _satisfying_facts())
        assert isinstance(plan, PackPlan)
        with pytest.raises(Exception):
            plan.pack_id = "other"  # type: ignore[misc]
