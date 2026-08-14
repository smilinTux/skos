"""coord_autocode adapter: coordination/tasks + card_events + the autopilot
run journal -> WatchdogEvent, WITHOUT falling into the coord status trap
(status lives in card_events, never in the immutable task file, and never
reliably in agents/<agent>.json either -- see the module docstring).

Zero import dependency on skcapstone/skcoord: everything here is plain JSON
/ JSONL on disk, so these tests need no optional-sibling marker and always
run.
"""
import json

import pytest

from skos.watchdog.adapters.coord_autocode import CoordAutocodeAdapter
from skos.watchdog.port import Window, collect_safe


def _window(since="2026-08-10T00:00:00+00:00", until="2026-08-10T06:00:00+00:00"):
    return Window(since=since, until=until)


@pytest.fixture
def coord_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path))
    root = tmp_path / "coordination"
    (root / "tasks").mkdir(parents=True)
    (root / "card_events").mkdir(parents=True)
    (root / "autopilot" / "runs").mkdir(parents=True)
    return root


def _write_task(root, task_id, **fields):
    data = {"id": task_id, "title": fields.pop("title", f"card {task_id}"), **fields}
    (root / "tasks" / f"{task_id}-slug.json").write_text(json.dumps(data), encoding="utf-8")


def _write_card_event(root, node, record):
    p = root / "card_events" / f"{node}.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def test_empty_coord_tree_is_quiet_not_unavailable(coord_root):
    assert CoordAutocodeAdapter().collect(_window()) == []


def test_card_opened_in_window_is_info(coord_root):
    _write_task(coord_root, "abc12345", title="New feature",
                created_at="2026-08-10T02:00:00+00:00", tags=["repo:skos"])
    events = CoordAutocodeAdapter().collect(_window())
    opened = [e for e in events if e.kind == "CardOpened"]
    assert len(opened) == 1
    assert opened[0].object == "abc12345"
    assert opened[0].severity == "info"
    assert "New feature" in opened[0].summary


def test_card_opened_outside_window_is_excluded(coord_root):
    _write_task(coord_root, "old11111", title="Ancient card",
                created_at="2020-01-01T00:00:00+00:00", tags=[])
    events = CoordAutocodeAdapter().collect(_window())
    assert not any(e.object == "old11111" for e in events)


def test_status_never_comes_from_the_task_file_or_agent_file(coord_root):
    """The trap: a task file has no status field, and neither does an
    agents/<agent>.json entry get read by this adapter at all. Completion
    is read exclusively from card_events move-to-done records."""
    _write_task(coord_root, "trap0001", title="Trap card",
                created_at="2026-08-10T01:00:00+00:00", tags=[])
    agents_dir = coord_root / "agents"
    agents_dir.mkdir()
    (agents_dir / "someone.json").write_text(
        json.dumps({"agent": "someone", "completed_tasks": ["trap0001"]}), encoding="utf-8")
    events = CoordAutocodeAdapter().collect(_window())
    # the agent file claims trap0001 is completed, but with no card_events
    # move-to-done record, this adapter must NOT report it as completed.
    assert not any(e.kind == "CardCompleted" and e.object == "trap0001" for e in events)


def test_card_completed_via_card_events_move_to_done(coord_root):
    _write_task(coord_root, "done0001", title="Finished card",
                created_at="2026-08-01T00:00:00+00:00", tags=[])
    _write_card_event(coord_root, "node1", {
        "card_id": "done0001", "action": "move", "column": "done",
        "ts": "2026-08-10T05:00:00+00:00",
    })
    events = CoordAutocodeAdapter().collect(_window())
    completed = [e for e in events if e.kind == "CardCompleted"]
    assert len(completed) == 1
    assert completed[0].object == "done0001"
    assert "Finished card" in completed[0].summary


def test_move_to_a_non_done_column_is_not_completion(coord_root):
    _write_card_event(coord_root, "node1", {
        "card_id": "x", "action": "move", "column": "review",
        "ts": "2026-08-10T05:00:00+00:00",
    })
    events = CoordAutocodeAdapter().collect(_window())
    assert not any(e.kind == "CardCompleted" for e in events)


def test_staged_children_awaiting_release_is_one_summary_line(coord_root):
    _write_task(coord_root, "staged01", title="Child A",
                created_at="2020-01-01T00:00:00+00:00", tags=["autopilot-staged"])
    _write_task(coord_root, "staged02", title="Child B",
                created_at="2020-01-01T00:00:00+00:00", tags=["autopilot-staged"])
    events = CoordAutocodeAdapter().collect(_window())
    staged = [e for e in events if e.kind == "StagedAwaitingRelease"]
    assert len(staged) == 1
    assert staged[0].meta["count"] == 2
    assert staged[0].severity == "notable"


def test_autopilot_run_in_window_is_narrated(coord_root):
    run = {"run_id": "run1", "updated_at": "2026-08-10T04:00:00+00:00",
           "items": {"a": {}, "b": {}}, "decisions": 1, "tokens": 500, "cost_usd": 0.02}
    (coord_root / "autopilot" / "runs" / "run1.json").write_text(json.dumps(run), encoding="utf-8")
    events = CoordAutocodeAdapter().collect(_window())
    runs = [e for e in events if e.kind == "AutopilotRun"]
    assert len(runs) == 1
    assert runs[0].object == "run1"
    assert "2 item" in runs[0].summary


def test_malformed_task_file_is_skipped_not_raised(coord_root):
    (coord_root / "tasks" / "bad.json").write_text("{not json", encoding="utf-8")
    _write_task(coord_root, "ok000001", title="Fine card",
                created_at="2026-08-10T02:00:00+00:00", tags=[])
    events = CoordAutocodeAdapter().collect(_window())
    assert any(e.object == "ok000001" for e in events)


def test_malformed_card_events_line_is_skipped_not_raised(coord_root):
    p = coord_root / "card_events" / "node1.jsonl"
    p.write_text("not json\n", encoding="utf-8")
    # must not raise
    events = CoordAutocodeAdapter().collect(_window())
    assert events == []


def test_degrades_to_source_unavailable_when_tasks_dir_is_unreadable(coord_root):
    tasks_dir = coord_root / "tasks"
    tasks_dir.chmod(0o000)
    try:
        events = collect_safe(CoordAutocodeAdapter(), _window())
    finally:
        tasks_dir.chmod(0o755)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.source == "coord_autocode"
