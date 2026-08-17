import importlib.util
import ipaddress
import os
import socket
from pathlib import Path

import pytest


_HAVE_SKCAPSTONE = importlib.util.find_spec("skcapstone") is not None
_HAVE_SKHARNESS = importlib.util.find_spec("skharness") is not None


# --------------------------------------------------------------------------
# No test opens a connection off this box.
#
# This is a hard suite-wide guard, not a convention, because the failure it
# prevents is invisible: an adapter that reaches the network from a test does
# not fail, it just gets slower, reads live state it was never meant to see,
# and behaves differently depending on whose box and whose config it runs on.
#
# It has already happened once. `watchdog/adapters/sites.py` (WD-12) is the
# one adapter that makes real HTTP requests, and it reads its target list from
# `SKWATCHDOG_SITES` via the operator env. tests/test_watchdog_cli.py drives
# the whole adapter registry end to end and did not clear that variable, so on
# any box where an operator had configured it, four CLI tests each checked the
# real domains over the real internet and spent the adapter's full 90s run
# budget doing it: measured at 92-98s per test, 383s for that one file, all of
# it blocked on the wire (1.3s of CPU across the whole run). The tests passed
# the entire time. Only the clock said anything was wrong.
#
# Loopback stays open (local servers, ASGI/TestClient plumbing, unix sockets);
# only a route off the machine is refused, and it is refused IMMEDIATELY so a
# leak costs a visible error instead of a connect timeout. Set
# SKOS_TESTS_ALLOW_NETWORK=1 to lift it deliberately for a session.
# --------------------------------------------------------------------------

_LOCAL_HOSTS = {"", "localhost", "localhost.localdomain", "0.0.0.0", "::", "<broadcast>"}


def _address_is_local(address) -> bool:
    """True for anything that cannot leave this machine. A non-tuple address is
    an AF_UNIX path (or something equally exotic), which is local by
    construction."""
    if not isinstance(address, tuple) or not address:
        return True
    host = str(address[0])
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False  # a hostname that is not plainly local: treat as off-box


def _install_outbound_network_guard() -> None:
    if os.environ.get("SKOS_TESTS_ALLOW_NETWORK") == "1":
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _refuse(address):
        raise RuntimeError(
            f"the test suite tried to open a network connection to {address!r}. "
            "Tests must not reach off this box: stub the module's network seam "
            "(and clear whatever env supplied the target). Set "
            "SKOS_TESTS_ALLOW_NETWORK=1 to lift this guard deliberately."
        )

    def guarded_connect(self, address):
        if not _address_is_local(address):
            _refuse(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if not _address_is_local(address):
            _refuse(address)
        return real_connect_ex(self, address)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex


_install_outbound_network_guard()


@pytest.fixture(autouse=True)
def _isolate_operator_env(tmp_path, monkeypatch):
    """Point the gitignored operator env file at a throwaway path for EVERY
    test, and drop skos.secret_env's process-level cache of it on both sides.

    `secret_env._file_values` is an `lru_cache(maxsize=1)` keyed on NOTHING, so
    the first call anywhere in a pytest process pins that whole process to
    whichever file was resolved at that instant. Two failures fall out of that,
    and this fixture closes both: a test that redirects `SKOS_SCHEDULE_ENV`
    after the cache is warm keeps silently reading the operator's REAL values
    (tests/test_watchdog_cli.py's own docstring records being bitten by exactly
    this), and a test that warms the cache from its own tmp file leaks those
    values forward into every test that runs after it.

    Tests that want their own operator env still just `monkeypatch.setenv` it;
    that runs after this fixture and wins, and the cache is clear when they do.
    """
    from skos import secret_env
    secret_env._file_values.cache_clear()
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "no-operator.env"))
    yield
    secret_env._file_values.cache_clear()


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
