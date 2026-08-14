"""atlas adapter: Atlas's published brief.md + parked decisions -> WatchdogEvent.

Both reads are plain files (no skcapstone/skcoord import at all -- the one
Phase-1 adapter with zero optional-sibling dependency), pointed at a
throwaway SKFLEET_ROOT for every test; none ever touches the real
~/.skcapstone/fleet.
"""
import json

import pytest

from skos.watchdog.adapters.atlas import AtlasAdapter
from skos.watchdog.port import Window, collect_safe


def _window(since="2026-08-10T00:00:00Z", until="2026-08-10T06:00:00Z"):
    return Window(since=since, until=until)


@pytest.fixture
def fleet_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SKFLEET_ROOT", str(tmp_path))
    return tmp_path


def _write_brief(fleet_root, text):
    d = fleet_root / "atlas" / "brief"
    d.mkdir(parents=True, exist_ok=True)
    (d / "brief.md").write_text(text, encoding="utf-8")


def _write_decision(fleet_root, decision_id, **fields):
    d = fleet_root / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": decision_id, "created": "2026-08-10T05:00:00Z", "status": "pending",
           "options": [{"action": "restart_service", "rationale": "crash loop"}],
           "chosen": None, "resolved_by": None, "resolved_at": None}
    rec.update(fields)
    (d / f"{decision_id}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_no_brief_or_decisions_is_quiet_not_unavailable(fleet_root):
    assert AtlasAdapter().collect(_window()) == []


def test_all_quiet_brief_is_info(fleet_root):
    _write_brief(fleet_root, "**All quiet** - nothing firing.\n")
    events = AtlasAdapter().collect(_window())
    assert len(events) == 1
    assert events[0].kind == "AtlasQuiet"
    assert events[0].severity == "info"


def test_frozen_brief_is_notable(fleet_root):
    _write_brief(fleet_root, "**FROZEN** - Atlas is standing down.\n")
    events = AtlasAdapter().collect(_window())
    assert events[0].kind == "AtlasFrozen"
    assert events[0].severity == "notable"


def test_firing_and_stale_counts_are_parsed(fleet_root):
    _write_brief(fleet_root, "**2 firing**, 1 stale.\n")
    events = AtlasAdapter().collect(_window())
    assert events[0].kind == "AtlasFiring"
    assert events[0].meta == {"firing": 2, "stale": 1, "frozen": False}
    assert events[0].link.http == "https://atlas.skworld.io/"


def test_pending_decision_carries_a_decide_command(fleet_root):
    _write_decision(fleet_root, "dec-1")
    events = AtlasAdapter().collect(_window())
    decisions = [e for e in events if e.kind == "AtlasPendingDecision"]
    assert len(decisions) == 1
    ev = decisions[0]
    assert ev.severity == "notable"
    assert "skoperator decide dec-1 --approve" in ev.summary
    assert ev.meta["decide_cmd"].startswith("skoperator decide dec-1")


def test_resolved_decision_is_not_narrated(fleet_root):
    _write_decision(fleet_root, "dec-2", status="approved")
    events = AtlasAdapter().collect(_window())
    assert not any(e.object == "dec-2" for e in events)


def test_multi_option_decision_shows_a_choice_flag(fleet_root):
    _write_decision(fleet_root, "dec-3", options=[
        {"action": "restart_service", "rationale": "a"},
        {"action": "replay_errors", "rationale": "b"},
    ])
    events = AtlasAdapter().collect(_window())
    ev = [e for e in events if e.object == "dec-3"][0]
    assert "--choice N" in ev.meta["decide_cmd"]


def test_corrupt_decision_file_degrades_inline_without_blanking_the_rest(fleet_root):
    d = fleet_root / "decisions"
    d.mkdir(parents=True)
    (d / "bad.json").write_bytes(b"\xff\xfe\x00bad")
    _write_decision(fleet_root, "dec-4")
    events = AtlasAdapter().collect(_window())
    kinds = {e.kind for e in events}
    assert "SourceUnavailable" in kinds
    assert "AtlasPendingDecision" in kinds  # the good record still comes through


def test_degrades_to_source_unavailable_when_brief_is_unreadable_binary(fleet_root):
    d = fleet_root / "atlas" / "brief"
    d.mkdir(parents=True)
    (d / "brief.md").write_bytes(b"\xff\xfe\x00garbage")
    events = collect_safe(AtlasAdapter(), _window())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.source == "atlas"
