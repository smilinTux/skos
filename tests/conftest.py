import importlib.util

import pytest


_HAVE_SKCAPSTONE = importlib.util.find_spec("skcapstone") is not None


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked `needs_skcapstone` when the optional sibling
    skcapstone package is not importable (e.g. in CI, which installs only skos).
    skcapstone is not a declared skos dependency, so its absence must not turn
    the suite red."""
    if _HAVE_SKCAPSTONE:
        return
    skip = pytest.mark.skip(reason="optional sibling skcapstone not installed")
    for item in items:
        if "needs_skcapstone" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Point SK_DATA_ROOT at a throwaway dir for every test."""
    root = tmp_path / "skdata"
    monkeypatch.setenv("SK_DATA_ROOT", str(root))
    monkeypatch.delenv("SKOS_PROFILE", raising=False)
    return root


@pytest.fixture
def vault_key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("SKOS_VAULT_KEY", Fernet.generate_key().decode())
