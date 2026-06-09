"""Tests for CLI commands: skos init, skos plan, skos up."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner
from skos.cli import app
from skos import registry

runner = CliRunner()


class TestInit:
    def test_init_local_creates_tree(self, data_root):
        r = runner.invoke(app, ["init", "--profile", "local"])
        assert r.exit_code == 0
        # Tree created
        assert (data_root / "registry").exists()
        assert (data_root / "apps").exists()

    def test_init_prints_recommended(self, data_root):
        r = runner.invoke(app, ["init", "--profile", "local"])
        assert r.exit_code == 0
        # Personal-first caps printed
        assert "capauth" in r.stdout
        assert "skmemory" in r.stdout
        assert "skchat" in r.stdout

    def test_init_cluster_shows_more_caps(self, data_root):
        r = runner.invoke(app, ["init", "--profile", "cluster"])
        assert r.exit_code == 0
        # cluster has local caps + extras
        assert "skmesh" in r.stdout or "skbus" in r.stdout

    def test_init_default_profile_is_local(self, data_root):
        """skos init with no --profile defaults to local."""
        r = runner.invoke(app, ["init"])
        assert r.exit_code == 0
        assert "capauth" in r.stdout

    def test_init_invalid_profile_fails(self, data_root):
        r = runner.invoke(app, ["init", "--profile", "bogus"])
        assert r.exit_code != 0


class TestPlan:
    def test_plan_local_shows_adapters(self, data_root):
        r = runner.invoke(app, ["plan", "--profile", "local"])
        assert r.exit_code == 0
        # Shows capability -> adapter pairs
        assert "capauth" in r.stdout
        assert "skmemory" in r.stdout

    def test_plan_custom_caps(self, data_root):
        r = runner.invoke(app, ["plan", "--profile", "local",
                                "--cap", "skdata", "--cap", "skfence"])
        assert r.exit_code == 0
        assert "skdata" in r.stdout
        assert "postgres" in r.stdout  # skdata default adapter
        assert "skfence" in r.stdout
        assert "traefik" in r.stdout   # skfence default adapter

    def test_plan_unknown_cap_fails(self, data_root):
        r = runner.invoke(app, ["plan", "--profile", "local", "--cap", "nope"])
        assert r.exit_code != 0

    def test_plan_output_has_profile_header(self, data_root):
        r = runner.invoke(app, ["plan", "--profile", "local"])
        assert r.exit_code == 0
        assert "local" in r.stdout

    def test_plan_cluster_profile(self, data_root):
        r = runner.invoke(app, ["plan", "--profile", "cluster"])
        assert r.exit_code == 0
        assert "skmesh" in r.stdout or "skbus" in r.stdout


class TestUp:
    def test_up_local_records_in_registry(self, data_root):
        r = runner.invoke(app, ["up", "--profile", "local"])
        assert r.exit_code == 0
        installed = registry.list_installed()
        assert "capauth" in installed
        assert "skmemory" in installed

    def test_up_custom_caps(self, data_root):
        r = runner.invoke(app, ["up", "--profile", "local",
                                "--cap", "skdata"])
        assert r.exit_code == 0
        assert "skdata" in registry.list_installed()

    def test_up_shows_status_per_step(self, data_root):
        r = runner.invoke(app, ["up", "--profile", "local",
                                "--cap", "capauth", "--cap", "skdata"])
        assert r.exit_code == 0
        # Should show per-step outcome
        assert "capauth" in r.stdout
        assert "skdata" in r.stdout

    def test_up_unknown_cap_fails(self, data_root):
        r = runner.invoke(app, ["up", "--profile", "local", "--cap", "nope"])
        assert r.exit_code != 0

    def test_up_default_profile_is_local(self, data_root):
        """skos up with no --profile defaults to local."""
        r = runner.invoke(app, ["up"])
        assert r.exit_code == 0
        installed = registry.list_installed()
        assert "capauth" in installed

    def test_up_idempotent(self, data_root):
        """Running up twice doesn't fail."""
        runner.invoke(app, ["up", "--profile", "local", "--cap", "capauth"])
        r = runner.invoke(app, ["up", "--profile", "local", "--cap", "capauth"])
        assert r.exit_code == 0
