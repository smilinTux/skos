import importlib.util
import ipaddress
import os
import socket
import sys
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


# --------------------------------------------------------------------------- #
# Cost directory and wallet isolation (S31, card 1bb4d4b0)                    #
# --------------------------------------------------------------------------- #
# Copied from skharness/tests/conftest.py to prevent skos tests from writing
# to the live cost ledger and joule wallet.
# See docs/S29-cost-ledger-leak-attribution.md for details.


@pytest.fixture(autouse=True)
def _isolate_cost_dir(tmp_path_factory, monkeypatch):
    """Point the autopilot cost ledger AND the settlement journal at a throwaway
    dir for EVERY test. The gated settle path consults the settlement journal for
    its double-settle guard and appends to it on a real settlement, so without
    this a finalize test would read and write the live, Syncthing-synced
    ~/.skcapstone/autopilot-cost tree."""
    from skharness.autocode import joules  # noqa: F401
    from skharness.autocode import ledger_correction  # noqa: F401
    from skharness.autocode import wallet_correction  # noqa: F401
    
    # We need to import these to access their path constants, but we don't want
    # to actually use the skharness modules in a way that would trigger imports
    # during test collection when skharness might not be available.
    # Instead, we'll use the same approach as skharness conftest.
    
    # Point the cost directory to a temporary directory
    cd = tmp_path_factory.mktemp("autopilot-cost")
    monkeypatch.setenv("SKAI_COST_DIR", str(cd))


@pytest.fixture(autouse=True)
def _isolate_joule_wallet(tmp_path_factory, monkeypatch):
    """Point the JOULE WALLET at a throwaway skcapstone root for EVERY test.

    joules.settle() mints and spends against JouleWallet(agent), which resolves
    the operator's real ~/.skcapstone home when nothing overrides it. settle() is
    the twin-gate pass path and the finalize tests exercise the pass path, so the
    suite minted well formed joules into the live ledger for weeks: 1,433 rows
    carrying the fixture description 'autocode task_complete t1' and 107,475
    joules, in a wallet the joule economy reads as real. The rows are individually
    valid and indistinguishable from genuine ones except by that string.

    SKAI_COST_DIR above closed the settlement JOURNAL half of this. This closes
    the WALLET half, and _usage_home rides the same override so the cost
    telemetry under {home}/usage is covered too.

    On by default rather than opt-in: opt-in isolation fails exactly when someone
    forgets, which is the case that matters. Per-file fixtures that pass an
    explicit home= still win, same precedence as SKAI_COST_DIR.
    """
    from skharness.autocode import joules
    
    root = tmp_path_factory.mktemp("joule-wallet")
    monkeypatch.setenv(joules.WALLET_HOME_ENV, str(root))


# Session-scoped production-store guard for skos (similar to skharness's S29)
# This ensures we catch if skos tests somehow write to production stores
_LIVE_LEDGER = Path("~/.skcapstone/autopilot-cost/ledger.jsonl").expanduser()
_LIVE_WALLET = Path("~/.skcapstone/agents/lumina/wallet/transactions.jsonl").expanduser()


def _wallet_fixture_rows(path: Path) -> int:
    """Count wallet rows carrying the fixture mint signature. READ ONLY, and
    never raises: a guard that can break the suite it guards is a liability."""
    if not path.exists():
        return 0
    total = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json
                    row = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(row, dict)
                        and str(row.get("description", "")).strip()
                        == "autocode task_complete t1"):  # This is the fixture description from skharness
                    total += 1
    except OSError:
        return total
    return total


def _ledger_fixture_rows(path: Path) -> int:
    """Count ledger rows carrying the fixture mint signature."""
    if not path.exists():
        return 0
    # We'd need to import ledger_correction from skharness, but to avoid
    # import issues during collection, we'll do a simple string match for
    # the fixture signature. This is less precise but safe.
    if not path.exists():
        return 0
    total = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if "autocode task_complete t1" in line:
                    total += 1
    except OSError:
        return total
    return total


def _production_fixture_counts() -> dict:
    try:
        return {
            "cost ledger": (str(_LIVE_LEDGER), _ledger_fixture_rows(_LIVE_LEDGER)),
            "joule wallet": (str(_LIVE_WALLET), _wallet_fixture_rows(_LIVE_WALLET)),
        }
    except Exception:  # noqa: BLE001 -- never let the guard break the suite
        return {}


def pytest_sessionstart(session):
    session.config._s31_store_baseline = _production_fixture_counts()


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_s31_store_baseline", None)
    if not before:
        return
    after = _production_fixture_counts()
    moved = [
        (name, path, before[name][1], count)
        for name, (path, count) in after.items()
        if name in before and count > before[name][1]
    ]
    if not moved:
        return

    banner = [
        "",
        "=" * 78,
        "PRODUCTION STORE CORRUPTED DURING SKOS TEST SESSION",
        "=" * 78,
    ]
    for name, path, was, now in moved:
        banner.append(f"  {name}: fixture rows {was} -> {now}  (+{now - was})")
        banner.append(f"    {path}")
    banner += [
        "",
        "  These stores are append-only and Syncthing-synced. A fixture row",
        "  written into them cannot be taken back; it can only be corrected",
        "  beside them (skharness.autocode.ledger_correction,",
        "  skharness.autocode.wallet_correction).",
        "",
        "  If this suite wrote them, an isolation fixture in tests/conftest.py",
        "  regressed or was missing. The skos test suite must isolate its writes",
        "  to the cost ledger and joule wallet to prevent corrupting production",
        "  data. See docs/S29-cost-ledger-leak-attribution.md.",
        "=" * 78,
        "",
    ]
    print("\n".join(banner), file=sys.stderr)
    # Only ever raises the status; a real test failure must not be downgraded
    if not exitstatus:
        session.exitstatus = 1


# --------------------------------------------------------------------------- #
# Cost directory and wallet isolation (S31, card 1bb4d4b0)                    #
# --------------------------------------------------------------------------- #
# Add isolation fixtures to prevent skos tests from writing to the live cost
# ledger and joule wallet. These mirror the fixtures in skharness/tests/conftest.py.
# See docs/S29-cost-ledger-leak-attribution.md for details.

_HAVE_SKHARNESS = False
try:
    import skharness  # noqa: F401
    _HAVE_SKHARNESS = True
except Exception:
    pass  # skharness not available; isolation fixtures will be no-ops

if _HAVE_SKHARNESS:
    from skharness.autocode import joules
    from skharness.autocode import ledger_correction  # noqa: F401
    from skharness.autocode import wallet_correction  # noqa: F401

    @pytest.fixture(autouse=True)
    def _isolate_cost_dir(tmp_path_factory, monkeypatch):
        """Point the autopilot cost ledger AND the settlement journal at a throwaway
        dir for EVERY test."""
        cd = tmp_path_factory.mktemp("autopilot-cost")
        monkeypatch.setenv("SKAI_COST_DIR", str(cd))

    @pytest.fixture(autouse=True)
    def _isolate_joule_wallet(tmp_path_factory, monkeypatch):
        """Point the JOULE WALLET at a throwaway skcapstone root for EVERY test."""
        root = tmp_path_factory.mktemp("joule-wallet")
        monkeypatch.setenv(joules.WALLET_HOME_ENV, str(root))


    # Session-scoped production-store guard for skos (similar to skharness's S29)
    # This ensures we catch if skos tests somehow write to production stores
    _LIVE_LEDGER = Path("~/.skcapstone/autopilot-cost/ledger.jsonl").expanduser()
    _LIVE_WALLET = Path("~/.skcapstone/agents/lumina/wallet/transactions.jsonl").expanduser()


    def _wallet_fixture_rows(path: Path) -> int:
        """Count wallet rows carrying the fixture mint signature. READ ONLY, and
        never raises: a guard that can break the suite it guards is a liability."""
        if not path.exists():
            return 0
        total = 0
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if (isinstance(row, dict)
                            and str(row.get("description", "")).strip()
                            == "autocode task_complete t1"):  # This is the fixture description from skharness
                        total += 1
        except OSError:
            return total
        return total


    def _ledger_fixture_rows(path: Path) -> int:
        """Count ledger rows carrying the fixture mint signature."""
        if not path.exists():
            return 0
        total = 0
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if "autocode task_complete t1" in line:
                        total += 1
        except OSError:
            return total
        return total


    def _production_fixture_counts() -> dict:
        try:
            return {
                "cost ledger": (str(_LIVE_LEDGER), _ledger_fixture_rows(_LIVE_LEDGER)),
                "joule wallet": (str(_LIVE_WALLET), _wallet_fixture_rows(_LIVE_WALLET)),
            }
        except Exception:  # noqa: BLE001 -- never let the guard break the suite
            return {}


    def pytest_sessionstart(session):
        session.config._s31_store_baseline = _production_fixture_counts()


    def pytest_sessionfinish(session, exitstatus):
        before = getattr(session.config, "_s31_store_baseline", None)
        if not before:
            return
        after = _production_fixture_counts()
        moved = [
            (name, path, before[name][1], count)
            for name, (path, count) in after.items()
            if name in before and count > before[name][1]
        ]
        if not moved:
            return

        banner = [
            "",
            "=" * 78,
            "PRODUCTION STORE CORRUPTED DURING SKOS TEST SESSION",
            "=" * 78,
        ]
        for name, path, was, now in moved:
            banner.append(f"  {name}: fixture rows {was} -> {now}  (+{now - was})")
            banner.append(f"    {path}")
        banner += [
            "",
            "  These stores are append-only and Syncthing-synced. A fixture row",
            "  written into them cannot be taken back; it can only be corrected",
            "  beside them (skharness.autocode.ledger_correction,",
            "  skharness.autocode.wallet_correction).",
            "",
            "  If this suite wrote them, an isolation fixture in tests/conftest.py",
            "  regressed or was missing. The skos test suite must isolate its writes",
            "  to the cost ledger and joule wallet to prevent corrupting production",
            "  data. See docs/S29-cost-ledger-leak-attribution.md.",
            "=" * 78,
            "",
        ]
        print("\n".join(banner), file=sys.stderr)
        # Only ever raises the status; a real test failure must not be downgraded
        if not exitstatus:
            session.exitstatus = 1

else:
    # skharness is not available; provide no-op fixtures to avoid breaking the test suite
    # This allows the skos test suite to run in environments where only skos is installed
    # (though autopilot-related tests would likely fail or be skipped due to missing dependency)
    @pytest.fixture(autouse=True)
    def _isolate_cost_dir(tmp_path_factory, monkeypatch):
        pass

    @pytest.fixture(autouse=True)
    def _isolate_joule_wallet(tmp_path_factory, monkeypatch):
        pass
