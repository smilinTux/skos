"""Tests for skos.coldstart - the empty-store guard + node sentinel (card f15d086d).

The dangerous case: a node that HAS been initialized (marker present) comes back
with an EMPTY store (wiped / not-yet-restored / not-yet-synced). Emitting then
would replicate empty state fleet-wide. The guard must refuse. A genuinely fresh
node (no marker) is allowed to write; a populated store is allowed and stamps the
marker so a *later* wipe-to-empty is caught.
"""
import json

import pytest
from typer.testing import CliRunner

from skos import coldstart as cs
from skos.cli import app
from skos.gtd_ingest import GtdCapture, capture, gtd_dir, upsert

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Isolate BOTH the GTD store and the node sentinel per test.

    (conftest's autouse fixture already points SKOS_STATE_DIR at a throwaway dir;
    we re-isolate here to keep this file self-describing and override-free.)"""
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv("SKOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("SKOS_COLDSTART_MARKER", raising=False)
    monkeypatch.delenv("SKOS_ALLOW_EMPTY_STORE", raising=False)
    yield


# ── marker + store primitives ────────────────────────────────────────────────

def test_marker_absent_then_stamped():
    assert cs.is_initialized() is False
    p = cs.mark_initialized()
    assert p.exists()
    assert cs.is_initialized() is True
    assert "host=" in p.read_text()


def test_marker_not_under_synced_store():
    """The sentinel must live OUTSIDE the Syncthing-synced GTD store dir, so it
    never replicates with the data it guards."""
    p = cs.marker_path().resolve()
    store = gtd_dir().resolve()
    assert store not in p.parents and p != store


def test_store_is_empty_and_count():
    assert cs.store_is_empty() is True
    assert cs.store_item_count() == 0


# ── the guard decision matrix ────────────────────────────────────────────────

def test_guard_trips_when_initialized_and_empty():
    """initialized marker + empty store -> the dangerous cold-start case: REFUSE."""
    cs.mark_initialized()
    assert cs.evaluate().would_trip is True
    with pytest.raises(cs.ColdStartGuardError):
        cs.guard_store("emit")


def test_capture_refuses_on_initialized_empty_store():
    """The real emit path (capture) is guarded, not just guard_store()."""
    cs.mark_initialized()
    with pytest.raises(cs.ColdStartGuardError):
        capture(GtdCapture(text="x", source="cron", source_ref="c-1", status="inbox"))
    # nothing was written -> no empty state to replicate
    assert cs.store_item_count() == 0


def test_upsert_refuses_on_initialized_empty_store():
    cs.mark_initialized()
    with pytest.raises(cs.ColdStartGuardError):
        upsert(GtdCapture(text="o", source="order", source_ref="o-1", status="waiting"))


def test_populated_store_proceeds_and_stamps_marker():
    """No marker + populated store -> allowed, and the node gets stamped so a
    LATER wipe-to-empty is recognized as dangerous."""
    assert cs.is_initialized() is False
    iid = capture(GtdCapture(text="real work", source="email",
                             source_ref="thread-1", status="next"))
    assert iid
    # a second emit observes the populated store and stamps the marker
    capture(GtdCapture(text="more", source="email", source_ref="thread-2", status="next"))
    assert cs.is_initialized() is True


def test_fresh_init_allowed_no_marker():
    """No marker + empty store -> genuine fresh init: writing is allowed."""
    assert cs.is_initialized() is False
    assert cs.evaluate().would_trip is False
    iid = capture(GtdCapture(text="first ever", source="manual",
                             source_ref="m-1", status="inbox"))
    assert iid
    items = json.loads((gtd_dir() / "inbox.json").read_text())
    assert len(items) == 1


def test_override_allows_emit_on_initialized_empty(monkeypatch):
    """SKOS_ALLOW_EMPTY_STORE bypasses the guard for a deliberate fresh init."""
    cs.mark_initialized()
    monkeypatch.setenv("SKOS_ALLOW_EMPTY_STORE", "1")
    assert cs.evaluate().would_trip is False
    iid = capture(GtdCapture(text="intentional", source="manual",
                             source_ref="m-2", status="inbox"))
    assert iid


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_check_exit1_when_guard_would_trip():
    cs.mark_initialized()
    res = runner.invoke(app, ["coldstart", "check"])
    assert res.exit_code == 1
    assert "GUARD WOULD TRIP" in res.stdout


def test_cli_check_ok_on_fresh_node():
    res = runner.invoke(app, ["coldstart", "check", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["would_trip"] is False and data["store_empty"] is True


def test_cli_init_refuses_empty_store_without_force():
    res = runner.invoke(app, ["coldstart", "init"])
    assert res.exit_code == 1
    assert cs.is_initialized() is False


def test_cli_init_force_stamps_empty_node():
    res = runner.invoke(app, ["coldstart", "init", "--force"])
    assert res.exit_code == 0
    assert cs.is_initialized() is True
