"""Tests for skos.packs.loader + the side-effect-free parts of effects."""

from __future__ import annotations

from pathlib import Path

import pytest
from skos.packs import loader
from skos.packs.effects import DONE, DefaultEffects, resolve_package_path


class TestLoader:
    def test_skbrain_is_a_pack(self):
        assert loader.is_pack("skbrain")
        assert "skbrain" in loader.available()

    def test_unknown_is_not_a_pack(self):
        assert not loader.is_pack("nope")

    def test_load_pack_returns_manifest_and_dir(self):
        manifest, pack_dir = loader.load_pack("skbrain")
        assert manifest.id == "skbrain"
        assert (pack_dir / "skworld.module.json").is_file()

    def test_pack_dir_missing_raises(self):
        with pytest.raises(loader.PackNotFound):
            loader.pack_dir("does-not-exist")

    def test_fleet_templates_present(self):
        _, pack_dir = loader.load_pack("skbrain")
        assert (pack_dir / "fleet" / "cronjob-skbrain-sync.json").is_file()
        assert (pack_dir / "fleet" / "cronjob-skbrain-cmdb-reconcile.json").is_file()


class TestResolvePackagePath:
    def test_bare_existing_path(self, tmp_path):
        f = tmp_path / "x.sql"
        f.write_text("select 1;")
        assert resolve_package_path(str(f)) == f

    def test_bare_missing_path(self, tmp_path):
        assert resolve_package_path(str(tmp_path / "absent.sql")) is None

    def test_package_qualified_via_env(self, tmp_path, monkeypatch):
        (tmp_path / "deploy").mkdir()
        script = tmp_path / "deploy" / "m.sql"
        script.write_text("select 1;")
        monkeypatch.setenv("SKMEMORY_REPO", str(tmp_path))
        assert resolve_package_path("skmemory:deploy/m.sql") == script

    def test_package_qualified_unresolved(self, monkeypatch):
        monkeypatch.setenv("SKMEMORY_REPO", "/nonexistent-xyz")
        assert resolve_package_path("skmemory:deploy/nope.sql") is None


class TestEffectsDryRun:
    """Dry-run must describe, never touch the world (no docker/git/psql)."""

    def test_migrate_dry_run(self):
        fx = DefaultEffects()
        r = fx.migrate(
            {"script": "skmemory:deploy/x.sql", "db": "skmem-pg", "pre_dump": True},
            Path("."),
            dry_run=True,
        )
        assert r.status == DONE
        assert "would apply" in r.note

    def test_db_roles_dry_run(self):
        fx = DefaultEffects()
        r = fx.db_roles(
            {"logins": {"skbrain_projector": "skbrain_ops_rw"}, "password_source": "skvault"},
            dry_run=True,
        )
        assert r.status == DONE
        assert "would create login roles" in r.note

    def test_env_drop_in_uses_reader_api_dsn_names(self, tmp_path):
        path = tmp_path / "skbrain.conf"
        DefaultEffects()._write_env_drop_in(
            str(path),
            {"SKBRAIN_PG_PROJECTOR_PW": "projector-secret"},
            {
                "skbrain_projector": "postgresql://projector",
                "skbrain_reader": "postgresql://reader",
            },
        )
        text = path.read_text()
        assert "SKBRAIN_PG_PROJECTOR_DSN=postgresql://projector" in text
        assert "SKBRAIN_PG_READER_DSN=postgresql://reader" in text
        assert "SKBRAIN_SKBRAIN_" not in text

    def test_seed_dry_run(self):
        fx = DefaultEffects()
        r = fx.seed({"cmd": ["skoperator", "kedb-seed"]}, dry_run=True)
        assert r.status == DONE
        assert "kedb-seed" in r.note

    def test_content_repo_dry_run(self):
        fx = DefaultEffects()
        r = fx.content_repo({"name": "skbrain-ops", "dest": "/tmp/x"}, dry_run=True)
        assert r.status == DONE
        assert "would clone" in r.note

    def test_fleet_objects_dry_run(self):
        fx = DefaultEffects(fleet_objects_dir=Path("/tmp/fleet"))
        r = fx.fleet_objects({"objects": ["a.json", "b.json"]}, Path("."), dry_run=True)
        assert r.status == DONE
        assert "2 fleet object" in r.note

    def test_doctor_dry_run(self):
        fx = DefaultEffects()
        r = fx.doctor({"checks": ["skbrain:schema"]}, "skbrain", dry_run=True)
        assert r.status == DONE

    def test_fleet_objects_write_and_idempotent(self, tmp_path):
        """A real (non-dry) fleet write is idempotent: second run reports unchanged."""
        _, pack_dir = loader.load_pack("skbrain")
        fx = DefaultEffects(fleet_objects_dir=tmp_path / "fleet")
        params = {
            "objects": [
                "fleet/cronjob-skbrain-sync.json",
                "fleet/cronjob-skbrain-cmdb-reconcile.json",
            ]
        }
        first = fx.fleet_objects(params, pack_dir, dry_run=False)
        assert "2 written" in first.note
        second = fx.fleet_objects(params, pack_dir, dry_run=False)
        assert "0 written, 2 unchanged" in second.note

    def test_seed_missing_cmd_defer_ok_pending(self):
        fx = DefaultEffects()
        r = fx.seed({"cmd": ["definitely-not-a-real-binary-xyz"], "defer_ok": True}, dry_run=False)
        assert r.status == "pending"

    def test_seed_missing_cmd_not_deferred_failed(self):
        fx = DefaultEffects()
        r = fx.seed({"cmd": ["definitely-not-a-real-binary-xyz"]}, dry_run=False)
        assert r.status == "failed"
