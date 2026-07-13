"""Autopilot queue_decision: single decision-write path shared by orchestrator and executor."""
import json
import pytest

from skos.autopilot import digest


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))


def test_queue_decision_writes_source_autopilot():
    from skos.gtd_ingest import gtd_dir
    digest.queue_decision("Merge PR?", {"yes": "y"}, "task-x", "high", qid="q1")
    items = json.loads((gtd_dir() / "waiting-for.json").read_text())
    assert items[0]["source"] == "autopilot" and items[0]["source_ref"] == "autopilot:q1"


def test_queue_decision_upsert_fallback(monkeypatch):
    import skos.gtd_ingest as gi
    monkeypatch.setattr(gi, "capture", lambda c: None)              # simulate dup
    called = {}
    monkeypatch.setattr(gi, "upsert", lambda c: called.setdefault("u", ("id", "unchanged")))
    out = digest.queue_decision("P", {}, "t", "high", qid="q2")
    assert called["u"][0] == "id"
