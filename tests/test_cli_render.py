"""Tests for the `skos render` CLI command."""
import yaml
import pytest
from typer.testing import CliRunner

from skos.cli import app

runner = CliRunner()

CAPAUTH_YAML = """\
name: capauth
capability: identity
description: "Sovereign PGP challenge-response identity."
packaging:
  oci:
    image: ghcr.io/smilintux/capauth:latest
    ports: [8088]
data: [keys]
"""


@pytest.fixture
def capauth_app_yaml(tmp_path):
    p = tmp_path / "app.yaml"
    p.write_text(CAPAUTH_YAML)
    return str(p)


def test_render_swarm_exits_ok(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "swarm"])
    assert r.exit_code == 0, r.output


def test_render_compose_exits_ok(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "compose"])
    assert r.exit_code == 0, r.output


def test_render_kubernetes_exits_ok(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "kubernetes"])
    assert r.exit_code == 0, r.output


def test_render_swarm_output_has_image(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "swarm"])
    assert "ghcr.io/smilintux/capauth:latest" in r.output


def test_render_swarm_output_has_port(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "swarm"])
    assert "8088" in r.output


def test_render_swarm_valid_yaml(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "swarm"])
    doc = yaml.safe_load(r.output)
    assert "services" in doc


def test_render_kubernetes_has_deployment(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "kubernetes"])
    docs = list(yaml.safe_load_all(r.output))
    kinds = [d["kind"] for d in docs]
    assert "Deployment" in kinds


def test_render_kubernetes_has_service(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "kubernetes"])
    docs = list(yaml.safe_load_all(r.output))
    kinds = [d["kind"] for d in docs]
    assert "Service" in kinds


def test_render_bad_platform_exits_1(capauth_app_yaml):
    r = runner.invoke(app, ["render", capauth_app_yaml, "--platform", "badplatform"])
    assert r.exit_code == 1
    assert "badplatform" in r.output


def test_render_bad_app_yaml_exits_nonzero(tmp_path):
    p = tmp_path / "broken.yaml"
    p.write_text("name: x\n")  # missing capability + packaging
    r = runner.invoke(app, ["render", str(p), "--platform", "swarm"])
    assert r.exit_code != 0


def test_render_missing_platform_flag(capauth_app_yaml):
    """--platform is required; typer should return non-zero without it."""
    r = runner.invoke(app, ["render", capauth_app_yaml])
    assert r.exit_code != 0
