import json

import pytest

from skos.autopilot.journal import RunJournal, runs_dir


@pytest.fixture(autouse=True)
def isolated_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_AUTOPILOT_RUNS_DIR", str(tmp_path / "runs"))
    yield


def test_append_and_read_back():
    j = RunJournal.open("run-1")
    j.set_item("t1", "claimed", claimed_at="2026-07-12T00:00:00Z")
    j.set_item("t1", "implementing")
    j.add_score("t1", 1, 3)
    j.add_score("t1", 2, 5)
    j.add_cost(1200, 0.02)
    on_disk = json.loads((runs_dir() / "run-1.json").read_text())
    assert on_disk["items"]["t1"]["state"] == "implementing"
    assert on_disk["items"]["t1"]["claimed_at"] == "2026-07-12T00:00:00Z"
    assert [s["score"] for s in on_disk["items"]["t1"]["scores"]] == [3, 5]
    assert on_disk["tokens"] == 1200 and on_disk["cost_usd"] == 0.02


def test_resume_state_skips_terminal():
    j = RunJournal.open("run-2")
    j.set_item("done", "finalized")
    j.set_item("gone", "escalated")
    j.set_item("mid", "round_2")
    reopened = RunJournal.open("run-2")               # simulate a resumed run
    assert reopened.is_terminal("done") and reopened.is_terminal("gone")
    assert reopened.is_terminal("mid") is False
    assert reopened.resumable_items() == ["mid"]
    assert reopened.state("mid") == "round_2"
    assert reopened.state("never") is None
