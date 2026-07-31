import importlib.util
from pathlib import Path

import pytest


_HAVE_SKCAPSTONE = importlib.util.find_spec("skcapstone") is not None
_HAVE_SKHARNESS = importlib.util.find_spec("skharness") is not None


# skos.autopilot is a thin re-export shim over the OPTIONAL `skharness` package
# (the shared autocode engine, installed via the `autopilot` extra). skharness is
# a private sibling monorepo package that is NOT on PyPI, so a base install (and
# CI) does not have it. Every module that imports skos.autopilot / skharness
# therefore fails at IMPORT time when skharness is absent, which is a COLLECTION
# error pytest cannot turn into a clean skip after the fact. So when skharness is
# missing we ignore exactly those test modules at collection: identify them by
# scanning their source for the import (precise -- no over-catch of the unrelated
# test_adapter.py / test_adapters.py, and self-maintaining as new autopilot tests
# land). When skharness IS present (dev boxes, and the CI `test (autopilot extra)`
# job), nothing is ignored and the full suite runs.
if not _HAVE_SKHARNESS:
    _needs_skharness = ("skos.autopilot", "import skharness", "from skharness")
    collect_ignore = sorted(
        p.name
        for p in Path(__file__).parent.glob("test_*.py")
        if any(tok in p.read_text(encoding="utf-8") for tok in _needs_skharness)
    )


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
