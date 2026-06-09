"""Tests for skos.render.kubernetes — KubernetesRenderer."""
import pytest
import yaml

from skos.descriptor import AppDescriptor
from skos.render.kubernetes import KubernetesRenderer
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


def _no_ports_no_data():
    return AppDescriptor.model_validate({
        "name": "probe",
        "capability": "test",
        "packaging": {"oci": {"image": "example/probe:1"}},
    })


def _with_env():
    return AppDescriptor.model_validate({
        "name": "envtest",
        "capability": "test",
        "packaging": {"oci": {"image": "example/envtest:1", "ports": [9000], "env": {"FOO": "bar"}}},
    })


def _parse_docs(out: str) -> list[dict]:
    return list(yaml.safe_load_all(out))


class TestKubernetesRenderer:
    def setup_method(self):
        self.r = KubernetesRenderer()

    def test_platform(self):
        assert self.r.platform == "kubernetes"

    def test_render_returns_valid_yaml(self):
        out = self.r.render(_capauth())
        docs = _parse_docs(out)
        assert len(docs) >= 2

    def test_render_has_deployment(self):
        docs = _parse_docs(self.r.render(_capauth()))
        kinds = [d["kind"] for d in docs]
        assert "Deployment" in kinds

    def test_render_has_service(self):
        docs = _parse_docs(self.r.render(_capauth()))
        kinds = [d["kind"] for d in docs]
        assert "Service" in kinds

    def test_deployment_image(self):
        docs = _parse_docs(self.r.render(_capauth()))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        containers = deploy["spec"]["template"]["spec"]["containers"]
        assert containers[0]["image"] == "ghcr.io/smilintux/capauth:latest"

    def test_deployment_name_prefixed(self):
        docs = _parse_docs(self.r.render(_capauth()))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        assert deploy["metadata"]["name"] == "skos-capauth"

    def test_service_name_prefixed(self):
        docs = _parse_docs(self.r.render(_capauth()))
        svc = next(d for d in docs if d["kind"] == "Service")
        assert svc["metadata"]["name"] == "skos-capauth"

    def test_deployment_port_matches_oci(self):
        docs = _parse_docs(self.r.render(_capauth()))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        container_ports = deploy["spec"]["template"]["spec"]["containers"][0]["ports"]
        assert any(p["containerPort"] == 8088 for p in container_ports)

    def test_service_port_matches_oci(self):
        docs = _parse_docs(self.r.render(_capauth()))
        svc = next(d for d in docs if d["kind"] == "Service")
        assert any(p["port"] == 8088 for p in svc["spec"]["ports"])

    def test_service_type_clusterip(self):
        docs = _parse_docs(self.r.render(_capauth()))
        svc = next(d for d in docs if d["kind"] == "Service")
        assert svc["spec"]["type"] == "ClusterIP"

    def test_pvc_per_data_entry(self):
        docs = _parse_docs(self.r.render(_capauth()))
        pvcs = [d for d in docs if d["kind"] == "PersistentVolumeClaim"]
        assert len(pvcs) == 1
        assert pvcs[0]["metadata"]["name"] == "skos-capauth-keys"

    def test_pvc_readwriteonce(self):
        docs = _parse_docs(self.r.render(_capauth()))
        pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
        assert "ReadWriteOnce" in pvc["spec"]["accessModes"]

    def test_volume_mount_in_deployment(self):
        docs = _parse_docs(self.r.render(_capauth()))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        container = deploy["spec"]["template"]["spec"]["containers"][0]
        mounts = container["volumeMounts"]
        assert any(m["mountPath"] == "/data/keys" for m in mounts)

    def test_no_ports_no_service(self):
        docs = _parse_docs(self.r.render(_no_ports_no_data()))
        kinds = [d["kind"] for d in docs]
        assert "Service" not in kinds

    def test_no_data_no_pvc(self):
        docs = _parse_docs(self.r.render(_no_ports_no_data()))
        pvcs = [d for d in docs if d["kind"] == "PersistentVolumeClaim"]
        assert len(pvcs) == 0

    def test_env_vars_in_container(self):
        docs = _parse_docs(self.r.render(_with_env()))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        container = deploy["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e["value"] for e in container["env"]}
        assert env["FOO"] == "bar"

    def test_skmemory_fixture(self):
        docs = _parse_docs(self.r.render(_skmemory()))
        kinds = [d["kind"] for d in docs]
        assert "PersistentVolumeClaim" in kinds
        assert "Deployment" in kinds
        assert "Service" in kinds

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

    def test_deployment_replicas_default_one(self):
        docs = _parse_docs(self.r.render(_capauth()))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        assert deploy["spec"]["replicas"] == 1

    def test_selector_matches_pod_labels(self):
        docs = _parse_docs(self.r.render(_capauth()))
        deploy = next(d for d in docs if d["kind"] == "Deployment")
        selector = deploy["spec"]["selector"]["matchLabels"]
        pod_labels = deploy["spec"]["template"]["metadata"]["labels"]
        assert selector == pod_labels
