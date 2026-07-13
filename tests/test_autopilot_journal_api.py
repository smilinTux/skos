import pytest
from skos.autopilot import journal


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_AUTOPILOT_RUNS_DIR", str(tmp_path / "runs"))


def test_read_write_run_roundtrip():
    journal.write_run("r1", {"run_id": "r1", "items": {"t1": {"state": "claimed"}}})
    assert journal.read_run("r1")["items"]["t1"]["state"] == "claimed"
    assert journal.read_run("absent") == {}


def test_handle_record_claim_and_worktree():
    h = journal.handle("r2")
    h.record_claim("t1", claimed_at="2026-07-12T00:00:00Z")
    h.set_worktree("t1", "/wt/t1")
    assert journal.read_run("r2")["items"]["t1"]["claimed_at"] == "2026-07-12T00:00:00Z"
    assert h.worktree_for("t1") == "/wt/t1"
    assert h.worktree_for("nope") is None


def test_render_helpers_do_not_raise():
    journal.write_run("r3", {"run_id": "r3", "phase": "report",
                             "items": {"t1": {"state": "finalized"}}, "decisions": 0})
    assert isinstance(journal.render_status(), str)
    assert isinstance(journal.render_run("r3"), str)
    assert isinstance(journal.render_list("runs"), list)
