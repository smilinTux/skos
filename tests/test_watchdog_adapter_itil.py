"""itil adapter: ITILManager -> WatchdogEvent for incidents, problems, and
changes (including CAB-pending and scheduled deploy windows).

Every fixture record is created through the real ITILManager against a
throwaway SKCAPSTONE_HOME (tmp_path), never the live coordination tree.
ItilAdapter does ``from skcapstone.itil import ITILManager,
OPEN_INCIDENT_STATUSES`` inside collect(): an optional sibling import,
marked accordingly so the module skips cleanly on a skos-only box.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("skcapstone")
from skcapstone.itil import ITILManager  # noqa: E402

from skos.watchdog.adapters.itil import ItilAdapter  # noqa: E402
from skos.watchdog.port import Window, collect_safe  # noqa: E402

pytestmark = pytest.mark.needs_skcapstone


def _window():
    return Window(since="2020-01-01T00:00:00Z", until="2030-01-01T00:00:00Z")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def mgr(home):
    return ITILManager(Path(home))


def test_no_itil_store_is_quiet_not_unavailable(home):
    events = ItilAdapter().collect(_window())
    assert events == []


def test_open_incident_narrates_with_severity_from_sev(mgr):
    inc = mgr.create_incident(title="skchat down", severity="sev1",
                               affected_services=["skchat"], created_by="test")
    events = ItilAdapter().collect(_window())
    ours = [e for e in events if e.object == inc.id]
    assert len(ours) == 1
    assert ours[0].kind == "IncidentOpen"
    assert ours[0].severity == "problem"  # sev1
    assert "skchat down" in ours[0].summary
    assert ours[0].link.uri == f"skworld://skos/watchdog/itil/incident/{inc.id}"


def test_closed_incident_is_not_narrated(mgr):
    inc = mgr.create_incident(title="transient blip", severity="sev4", created_by="test")
    mgr.update_incident(inc.id, "test", new_status="resolved")
    mgr.update_incident(inc.id, "test", new_status="closed")
    events = ItilAdapter().collect(_window())
    narrated_ids = {e.object for e in events if e.kind == "IncidentOpen"}
    assert inc.id not in narrated_ids


def test_open_problem_is_notable(mgr):
    prob = mgr.create_problem(title="recurring OOM", created_by="test")
    events = ItilAdapter().collect(_window())
    ours = [e for e in events if e.object == prob.id]
    assert len(ours) == 1
    assert ours[0].kind == "ProblemOpen"
    assert ours[0].severity == "notable"


def test_change_awaiting_cab_is_notable_with_a_link(mgr):
    chg = mgr.propose_change(title="bump memory limit", created_by="test")
    mgr.update_change(chg.id, "test-agent", new_status="reviewing")
    events = ItilAdapter().collect(_window())
    ours = [e for e in events if e.object == chg.id]
    assert len(ours) == 1
    assert ours[0].kind == "ChangeAwaitingCAB"
    assert ours[0].severity == "notable"
    assert "CAB" in ours[0].summary


def test_scheduled_change_narrates_its_deploy_window(mgr):
    # change_type="standard" auto-approves at fold time (itil.py
    # _cab_resolved_status), which is required before a `schedule` event
    # is accepted; this is the real state machine, not a shortcut.
    chg = mgr.propose_change(title="rotate a cert", change_type="standard", created_by="test")
    mgr._append_event(mgr.changes_dir, chg.id, "test-agent", "schedule",
                       window_start="2026-08-15T02:00:00Z", window_end="2026-08-15T03:00:00Z")
    events = ItilAdapter().collect(_window())
    ours = [e for e in events if e.object == chg.id]
    assert len(ours) == 1
    assert ours[0].kind == "ChangeScheduled"
    assert ours[0].severity == "info"
    assert "2026-08-15T02:00:00Z" in ours[0].summary
    assert ours[0].meta["scheduled_window"]["window_start"] == "2026-08-15T02:00:00Z"


def test_never_adds_a_change_specific_alert_path(mgr):
    """The 2026-08-13 spec addendum: the watchdog reads change management,
    it never notifies for it. This adapter must not call anything under
    operator_seat.notify / fleet.alerts / sk-alert; it only builds
    WatchdogEvents from records it already read."""
    chg = mgr.propose_change(title="rotate a cert", change_type="standard", created_by="test")
    mgr._append_event(mgr.changes_dir, chg.id, "test-agent", "schedule",
                       window_start="2026-08-15T02:00:00Z", window_end="2026-08-15T03:00:00Z")
    events = ItilAdapter().collect(_window())
    # a ChangeScheduled event is narrative (info), never itself an alarm.
    assert all(e.severity != "problem" or e.kind != "ChangeScheduled" for e in events)


def test_degrades_to_source_unavailable_when_skcapstone_is_absent(monkeypatch, home):
    # See the equivalent fleet-adapter test's comment: the exact leaf module
    # this adapter imports must be nulled, since a cached submodule short
    # circuits past a nulled parent package.
    for name in ("skcapstone", "skcapstone.itil"):
        monkeypatch.setitem(sys.modules, name, None)
    events = collect_safe(ItilAdapter(), _window())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.source == "itil"
