"""Tests for skos.render.nomad — NomadRenderer (closes the advertised-but-unwired gap)."""
import pytest

from skos.descriptor import AppDescriptor
from skos.render.nomad import NomadRenderer
from skos.render.base import RenderError
from skos.render import get_renderer, RENDERERS


def _capauth():
    return AppDescriptor.model_validate({
        "name": "capauth",
        "capability": "identity",
        "packaging": {"oci": {"image": "ghcr.io/smilintux/capauth:latest", "ports": [8088]}},
        "data": ["keys"],
    })


def _with_env():
    return AppDescriptor.model_validate({
        "name": "envtest",
        "capability": "test",
        "packaging": {"oci": {"image": "example/envtest:1", "ports": [9000], "env": {"FOO": "bar", "BAZ": "qux"}}},
    })


def _no_oci():
    return AppDescriptor.model_validate({
        "name": "native-app", "capability": "test",
        "packaging": {"native": {"cmd": "echo hi"}},
    })


class TestNomadRenderer:
    def test_renders_a_docker_job(self):
        out = NomadRenderer().render(_capauth())
        assert 'job "skos-capauth" {' in out
        assert 'driver = "docker"' in out
        assert 'image = "ghcr.io/smilintux/capauth:latest"' in out
        assert 'port "p8088" { to = 8088 }' in out
        assert 'type        = "service"' in out

    def test_volume_and_mount(self):
        out = NomadRenderer().render(_capauth())
        assert 'volume "capauth-keys" {' in out
        assert 'destination = "/data/keys"' in out

    def test_env_is_sorted_deterministic(self):
        out = NomadRenderer().render(_with_env())
        assert out.index("BAZ") < out.index("FOO")        # sorted

    def test_no_oci_raises(self):
        with pytest.raises(RenderError):
            NomadRenderer().render(_no_oci())

    def test_registered_in_the_registry(self):
        assert "nomad" in RENDERERS
        assert get_renderer("nomad").platform == "nomad"
