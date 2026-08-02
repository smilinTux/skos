"""Tests for skos.itil_incidents: fold-on-read open-incident state."""
import json

import pytest

from skos.itil_incidents import itil_dir, load_open_incidents


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_ITIL_DIR", str(tmp_path / "itil"))
    yield


def _write_events(events):
    path = itil_dir() / "incidents.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_no_log_returns_empty():
    assert load_open_incidents() == []


def test_open_incident_folds_latest_status():
    _write_events([
        {"incident_id": "inc-1", "ts": "2026-08-01T00:00:00Z", "title": "disk full",
         "status": "detected", "service": "skmem-pg", "severity": "high"},
        {"incident_id": "inc-1", "ts": "2026-08-01T00:05:00Z", "status": "investigating"},
    ])
    open_incidents = load_open_incidents()
    assert len(open_incidents) == 1
    inc = open_incidents[0]
    assert inc.id == "inc-1"
    assert inc.status == "investigating"          # latest event wins
    assert inc.title == "disk full"                # unpatched fields persist
    assert inc.service == "skmem-pg"
    assert inc.created_at == "2026-08-01T00:00:00Z"
    assert inc.updated_at == "2026-08-01T00:05:00Z"


def test_resolved_incident_excluded():
    _write_events([
        {"incident_id": "inc-2", "ts": "2026-08-01T00:00:00Z", "title": "cron failed",
         "status": "detected"},
        {"incident_id": "inc-2", "ts": "2026-08-01T01:00:00Z", "status": "resolved"},
    ])
    assert load_open_incidents() == []


def test_escalated_incident_still_open():
    _write_events([
        {"incident_id": "inc-3", "ts": "2026-08-01T00:00:00Z", "title": "wedged bridge",
         "status": "detected"},
        {"incident_id": "inc-3", "ts": "2026-08-01T02:00:00Z", "status": "escalated"},
    ])
    open_incidents = load_open_incidents()
    assert [i.id for i in open_incidents] == ["inc-3"]
    assert open_incidents[0].status == "escalated"


def test_mixed_open_and_closed_sorted_by_created_at():
    _write_events([
        {"incident_id": "inc-b", "ts": "2026-08-01T05:00:00Z", "title": "b", "status": "detected"},
        {"incident_id": "inc-a", "ts": "2026-08-01T01:00:00Z", "title": "a", "status": "detected"},
        {"incident_id": "inc-c", "ts": "2026-08-01T02:00:00Z", "title": "c", "status": "resolved"},
    ])
    assert [i.id for i in load_open_incidents()] == ["inc-a", "inc-b"]


def test_corrupt_line_skipped_not_raised():
    path = itil_dir() / "incidents.jsonl"
    path.write_text(
        '{"incident_id": "inc-1", "ts": "2026-08-01T00:00:00Z", "title": "ok", "status": "detected"}\n'
        "not-json\n",
        encoding="utf-8",
    )
    open_incidents = load_open_incidents()
    assert [i.id for i in open_incidents] == ["inc-1"]


def test_missing_incident_id_skipped():
    _write_events([{"ts": "2026-08-01T00:00:00Z", "status": "detected"}])
    assert load_open_incidents() == []
