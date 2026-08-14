"""fleet_events adapter: fleet/events.py::read() per node -> WatchdogEvent.

Every test here writes its own throwaway fleet tree under SKFLEET_ROOT
(tmp_path); none ever touches the real ~/.skcapstone/fleet. This module's
FleetEventsAdapter does ``from skcapstone.fleet.paths import default_paths``
inside collect(), so every test needs the optional sibling skcapstone
package installed; marked accordingly (skipped cleanly, not failed, on a box
that only has skos, mirroring tests/conftest.py's needs_skcapstone marker).
"""
import json
import sys

import pytest

from skos.watchdog.adapters.fleet_events import FleetEventsAdapter
from skos.watchdog.port import Window, collect_safe

pytestmark = pytest.mark.needs_skcapstone


def _window(since="2026-08-10T00:00:00Z", until="2026-08-10T06:00:00Z"):
    return Window(since=since, until=until)


def _write_event(fleet_root, node, record):
    d = fleet_root / "status" / node
    d.mkdir(parents=True, exist_ok=True)
    p = d / "events.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


@pytest.fixture(autouse=True)
def isolated_fleet(tmp_path, monkeypatch):
    root = tmp_path / "fleet"
    monkeypatch.setenv("SKFLEET_ROOT", str(root))
    return root


def test_no_fleet_tree_is_quiet_not_unavailable(isolated_fleet):
    events = FleetEventsAdapter().collect(_window())
    assert events == []


def test_crash_loop_reads_as_a_problem(isolated_fleet):
    _write_event(isolated_fleet, "dot41", {
        "ts": "2026-08-10T03:00:00Z", "node": "dot41", "kind": "service",
        "name": "skchat-daemon", "type": "Actuation", "reason": "CrashLooping",
        "message": "unit=skchat-daemon.service attempts=4", "count": 1,
    })
    events = FleetEventsAdapter().collect(_window())
    assert len(events) == 1
    ev = events[0]
    assert ev.source == "fleet"
    assert ev.severity == "problem"
    assert ev.kind == "CrashLooping"
    assert ev.object == "skchat-daemon@dot41"
    assert "dot41" in ev.summary
    assert ev.link.uri == "skworld://skos/watchdog/fleet/dot41"
    assert ev.ref.startswith("fleet:dot41:2026-08-10T03:00:00Z")


def test_successful_converge_action_reads_as_info(isolated_fleet):
    _write_event(isolated_fleet, "dot41", {
        "ts": "2026-08-10T03:00:00Z", "node": "dot41", "kind": "service",
        "name": "skchat-daemon", "type": "Actuation", "reason": "Restarted",
        "message": "unit=skchat-daemon.service attempt=1",
    })
    events = FleetEventsAdapter().collect(_window())
    assert events[0].severity == "info"


def test_unrecognized_reason_reads_as_notable_not_silently_dropped(isolated_fleet):
    _write_event(isolated_fleet, "dot41", {
        "ts": "2026-08-10T03:00:00Z", "node": "dot41", "kind": "service",
        "name": "x", "type": "Config", "reason": "SomethingNew",
    })
    events = FleetEventsAdapter().collect(_window())
    assert events[0].severity == "notable"


def test_event_outside_window_is_excluded(isolated_fleet):
    _write_event(isolated_fleet, "dot41", {
        "ts": "2020-01-01T00:00:00Z", "node": "dot41", "kind": "service",
        "name": "x", "type": "Actuation", "reason": "CrashLooping",
    })
    events = FleetEventsAdapter().collect(_window())
    assert events == []


def test_multiple_nodes_are_all_read(isolated_fleet):
    _write_event(isolated_fleet, "dot41", {
        "ts": "2026-08-10T03:00:00Z", "node": "dot41", "kind": "service",
        "name": "a", "type": "Actuation", "reason": "CrashLooping",
    })
    _write_event(isolated_fleet, "noroc2027", {
        "ts": "2026-08-10T04:00:00Z", "node": "noroc2027", "kind": "service",
        "name": "b", "type": "Actuation", "reason": "Started",
    })
    events = FleetEventsAdapter().collect(_window())
    nodes = {e.meta["node"] for e in events}
    assert nodes == {"dot41", "noroc2027"}


def test_degrades_to_source_unavailable_when_skcapstone_is_absent(monkeypatch, isolated_fleet):
    # Null the exact leaf modules this adapter imports, not just the
    # top-level package: once `skcapstone.fleet.paths` is cached in
    # sys.modules (as it will be after any earlier test in this process),
    # `from skcapstone.fleet.paths import default_paths` resolves straight
    # from that cache without re-checking the parent package's state, so
    # nulling only "skcapstone" would not reproduce a genuine absence.
    for name in ("skcapstone", "skcapstone.fleet", "skcapstone.fleet.paths",
                 "skcapstone.fleet.events"):
        monkeypatch.setitem(sys.modules, name, None)
    events = collect_safe(FleetEventsAdapter(), _window())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.source == "fleet"
    assert ev.severity == "notable"
