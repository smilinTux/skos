"""Tests for skos.render.compose — ComposeRenderer + SwarmRenderer."""
import pytest
import yaml

from skos.descriptor import AppDescriptor
from skos.render.compose import ComposeRenderer, SwarmRenderer
from skos.render.base import RenderError


def _capauth():
    return AppDescriptor.model_validate({
        "name": "capauth",
        "capability": "identity",
        "description": "Sovereign PGP challenge-response identity.",
        "packaging": {"oci": {"image": "ghcr.io/smilintux/capauth:latest", "ports": [8088]}},
        "data": ["keys"],
    })


def _skmemory():
    return AppDescriptor.model_validate({
        "name": "skmemory",
        "capability": "memory",
        "packaging": {"oci": {"image": "skmem-pg:pg17-bm25-age", "ports": [5432]}},
        "data": ["pgdata"],
    })


def _no_ports():
    return AppDescriptor.model_validate({
        "name": "probe",
        "capability": "test",
        "packaging": {"oci": {"image": "example/probe:1"}},
    })


def _with_env():
    return AppDescriptor.model_validate({
        "name": "envtest",
        "capability": "test",
        "packaging": {"oci": {"image": "example/envtest:1", "ports": [9000], "env": {"FOO": "bar", "BAZ": "qux"}}},
    })


class TestComposeRenderer:
    def setup_method(self):
        self.r = ComposeRenderer()

    def test_platform(self):
        assert self.r.platform == "compose"

    def test_render_returns_valid_yaml(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        assert isinstance(doc, dict)

    def test_render_has_correct_image(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        svc = doc["services"]["skos-capauth"]
        assert svc["image"] == "ghcr.io/smilintux/capauth:latest"

    def test_render_service_name_prefixed(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        assert "skos-capauth" in doc["services"]

    def test_render_ports_identity_mapped(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        ports = doc["services"]["skos-capauth"]["ports"]
        assert "8088:8088" in ports

    def test_render_named_volume_per_data_entry(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        assert "skos-capauth-keys" in doc["volumes"]

    def test_render_volume_mount_in_service(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        mounts = doc["services"]["skos-capauth"]["volumes"]
        assert any("skos-capauth-keys" in m for m in mounts)

    def test_render_no_ports_omits_ports_key(self):
        out = self.r.render(_no_ports())
        doc = yaml.safe_load(out)
        svc = doc["services"]["skos-probe"]
        assert "ports" not in svc

    def test_render_no_data_omits_volumes_top(self):
        out = self.r.render(_no_ports())
        doc = yaml.safe_load(out)
        assert "volumes" not in doc

    def test_render_env_vars_present(self):
        out = self.r.render(_with_env())
        doc = yaml.safe_load(out)
        env = doc["services"]["skos-envtest"]["environment"]
        assert env["FOO"] == "bar"
        assert env["BAZ"] == "qux"

    def test_render_skmemory_fixture(self):
        out = self.r.render(_skmemory())
        doc = yaml.safe_load(out)
        svc = doc["services"]["skos-skmemory"]
        assert svc["image"] == "skmem-pg:pg17-bm25-age"
        assert "5432:5432" in svc["ports"]
        assert "skos-skmemory-pgdata" in doc["volumes"]

    def test_render_no_oci_raises(self):
        d = AppDescriptor.model_validate({
            "name": "native-app",
            "capability": "test",
            "packaging": {"native": {"cmd": "echo hi"}},
        })
        with pytest.raises(RenderError, match="OCI spec"):
            self.r.render(d)

    def test_output_is_deterministic(self):
        a = self.r.render(_capauth())
        b = self.r.render(_capauth())
        assert a == b

    def test_version_field_present(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        assert "version" in doc


class TestSwarmRenderer:
    def setup_method(self):
        self.r = SwarmRenderer()

    def test_platform(self):
        assert self.r.platform == "swarm"

    def test_render_valid_for_swarm(self):
        out = self.r.render(_capauth())
        doc = yaml.safe_load(out)
        svc = doc["services"]["skos-capauth"]
        assert svc["image"] == "ghcr.io/smilintux/capauth:latest"
        assert "8088:8088" in svc["ports"]

    def test_swarm_inherits_compose_logic(self):
        compose_out = ComposeRenderer().render(_capauth())
        swarm_out = self.r.render(_capauth())
        # Both platforms produce functionally identical output (same schema)
        assert yaml.safe_load(compose_out) == yaml.safe_load(swarm_out)
