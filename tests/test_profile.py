import pytest
from pathlib import Path
from skos import profile


def test_active_defaults_to_local(monkeypatch):
    monkeypatch.delenv("SKOS_PROFILE", raising=False)
    assert profile.active() is profile.Profile.LOCAL


def test_active_from_env(monkeypatch):
    monkeypatch.setenv("SKOS_PROFILE", "cluster")
    assert profile.active() is profile.Profile.CLUSTER


def test_active_invalid_raises(monkeypatch):
    monkeypatch.setenv("SKOS_PROFILE", "bogus")
    with pytest.raises(profile.ProfileError):
        profile.active()


def test_default_data_root_local(monkeypatch):
    monkeypatch.delenv("SKOS_PROFILE", raising=False)
    assert profile.default_data_root() == (Path.home() / "var" / "data" / "sk")


def test_default_data_root_cluster(monkeypatch):
    monkeypatch.setenv("SKOS_PROFILE", "cluster")
    assert profile.default_data_root() == Path("/var/data/sk")
