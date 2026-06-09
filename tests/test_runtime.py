import pytest
from skos.packaging import runtime


def test_detect_prefers_podman(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda b: f"/usr/bin/{b}" if b in ("podman", "docker") else None)
    assert runtime.detect() == "podman"


def test_detect_falls_back_to_docker(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda b: "/usr/bin/docker" if b == "docker" else None)
    assert runtime.detect() == "docker"


def test_detect_none_raises(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda b: None)
    with pytest.raises(runtime.RuntimeError_):
        runtime.detect()


def test_explicit_env_overrides(monkeypatch):
    monkeypatch.setenv("SKOS_RUNTIME", "docker")
    monkeypatch.setattr(runtime.shutil, "which", lambda b: "/usr/bin/docker")
    assert runtime.detect() == "docker"
