import subprocess
import pytest
from skos.descriptor import AppDescriptor
from skos.packaging.oci import OciAdapter


def _app():
    return AppDescriptor.model_validate({
        "name": "capauth", "capability": "identity",
        "packaging": {"oci": {"image": "ghcr.io/smilintux/capauth:latest", "ports": [8088]}},
    })


def test_locate_returns_image(monkeypatch):
    monkeypatch.setattr("skos.packaging.oci.runtime.run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "ghcr.io/smilintux/capauth:latest\n", ""))
    assert OciAdapter().locate(_app()) == "ghcr.io/smilintux/capauth:latest"


def test_materialize_pulls_and_runs(monkeypatch):
    calls = []
    monkeypatch.setattr("skos.packaging.oci.runtime.run",
                        lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "ok", ""))
    res = OciAdapter().materialize(_app())
    assert res.running is True and res.adapter == "oci"
    assert any(a[0] == "pull" for a in calls)
    assert any(a[0] == "run" for a in calls)


def test_health_false_when_not_running(monkeypatch):
    monkeypatch.setattr("skos.packaging.oci.runtime.run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    assert OciAdapter().health(_app()) is False
