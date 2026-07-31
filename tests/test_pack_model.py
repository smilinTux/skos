"""Tests for skos.packs.model: parse a v1.2 manifest install facet (OPS1.1)."""
from __future__ import annotations

import copy

import pytest

from skos.packs.loader import load_manifest_dict
from skos.packs.model import PackError, PackManifest, PackStep


@pytest.fixture
def skbrain_raw() -> dict:
    return load_manifest_dict("skbrain")


class TestFromDict:
    def test_parses_skbrain(self, skbrain_raw):
        m = PackManifest.from_dict(skbrain_raw)
        assert m.id == "skbrain"
        assert m.schema_version == "1.2"
        assert m.requires.capabilities == ("skmem-pg",)
        assert m.requires.packages["skcapstone"] == ">=0.16"
        kinds = [s.kind for s in m.steps]
        assert kinds == [
            "sql_migration",
            "db_roles",
            "content_repo",
            "seed",
            "seed",
            "seed",
            "fleet_objects",
            "doctor",
        ]
        assert m.knowledge is not None
        assert m.knowledge["namespace"] == "ops"

    def test_steps_preserve_index_and_params(self, skbrain_raw):
        m = PackManifest.from_dict(skbrain_raw)
        migration = m.steps[0]
        assert isinstance(migration, PackStep)
        assert migration.index == 0
        assert "kind" not in migration.params
        assert migration.params["script"].startswith("skmemory:")

    def test_non_mapping_raises(self):
        with pytest.raises(PackError, match="must be a mapping"):
            PackManifest.from_dict(["not", "a", "manifest"])

    def test_missing_id_raises(self, skbrain_raw):
        skbrain_raw.pop("id")
        with pytest.raises(PackError, match="non-empty str 'id'"):
            PackManifest.from_dict(skbrain_raw)

    def test_no_install_facet_raises(self, skbrain_raw):
        skbrain_raw.pop("install")
        with pytest.raises(PackError, match="no 'install' facet"):
            PackManifest.from_dict(skbrain_raw)

    def test_wrong_schema_version_raises(self, skbrain_raw):
        skbrain_raw["schemaVersion"] = "1.1"
        with pytest.raises(PackError, match="must declare schemaVersion"):
            PackManifest.from_dict(skbrain_raw)

    def test_unknown_step_kind_raises(self, skbrain_raw):
        skbrain_raw["install"]["steps"].append({"kind": "wormhole"})
        with pytest.raises(PackError, match="unknown kind"):
            PackManifest.from_dict(skbrain_raw)

    def test_missing_required_step_field_raises(self, skbrain_raw):
        # sql_migration requires 'script'
        broken = copy.deepcopy(skbrain_raw)
        broken["install"]["steps"][0].pop("script")
        with pytest.raises(PackError, match="missing required field 'script'"):
            PackManifest.from_dict(broken)

    def test_empty_steps_raises(self, skbrain_raw):
        skbrain_raw["install"]["steps"] = []
        with pytest.raises(PackError, match="non-empty list"):
            PackManifest.from_dict(skbrain_raw)

    def test_signed_flag_false_without_envelope(self, skbrain_raw):
        assert PackManifest.from_dict(skbrain_raw).signed is False

    def test_signed_flag_true_with_envelope(self, skbrain_raw):
        skbrain_raw["signature"] = {"alg": "capauth", "sig": "..."}
        assert PackManifest.from_dict(skbrain_raw).signed is True

    def test_bad_requires_packages_raises(self, skbrain_raw):
        skbrain_raw["install"]["requires"]["packages"] = {"skos": 3}
        with pytest.raises(PackError, match="map of package name to constraint"):
            PackManifest.from_dict(skbrain_raw)
