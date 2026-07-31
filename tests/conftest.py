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


@pytest.fixture(autouse=True)
def _isolate_coldstart_state(tmp_path, monkeypatch):
    """Point the cold-start node sentinel at a throwaway, per-test state dir.

    The marker (skos.coldstart) is a real, local, per-node file under
    ``~/.local/state/skos`` by default. Isolating it keeps the suite from
    reading a genuine marker on the dev box (which would trip the empty-store
    guard) and from writing one during tests. Tests that exercise the guard set
    their own marker/env explicitly."""
    monkeypatch.setenv("SKOS_STATE_DIR", str(tmp_path / "skos-state"))
    monkeypatch.delenv("SKOS_COLDSTART_MARKER", raising=False)
    monkeypatch.delenv("SKOS_ALLOW_EMPTY_STORE", raising=False)
    yield


@pytest.fixture(autouse=True)
def _hermetic_fleet(monkeypatch, tmp_path):
    """Point the fleet dispatch gate at an empty tree so orchestrator tests
    never consult the live ``~/.skcapstone/fleet``.

    ``skharness.autocode.orchestrator.run_once`` partitions selected tasks
    local-vs-off-node through ``fleet_dispatch`` (which reads the skcapstone
    fleet store when skcapstone is importable). Without isolation the suite
    reads the real fleet on the dev box, so tasks get partitioned off-node (or
    the placement write trips skcapstone's name validation) and never run,
    turning valid orchestrator tests red. An empty root keeps the gate inert
    (no admitted nodes) so every selected task runs locally, matching CI where
    skcapstone is absent. Mirrors skharness's own conftest."""
    monkeypatch.setenv("SKFLEET_ROOT", str(tmp_path / "fleet-hermetic"))


@pytest.fixture(autouse=True)
def _isolate_health(tmp_path_factory, monkeypatch):
    """Point harness health telemetry at a throwaway file for EVERY test.

    Adapter/engineering tests exercise ``_run``/``assess`` which record health
    events; without isolation those fake events land in the real
    ``~/.skcapstone`` health log and skew the adaptive retry budget (which reads
    that log) in production. Isolation keeps telemetry a pure observation of
    real runs. Mirrors skharness's own conftest."""
    hp = tmp_path_factory.mktemp("health") / "health.jsonl"
    monkeypatch.setenv("SKHARNESS_HEALTH_PATH", str(hp))


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
