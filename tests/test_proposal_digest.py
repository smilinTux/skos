"""generate_weekly_digest: 7-day funnel counts from proposal status transitions,
independent of the autopilot status path (skos.autopilot / skharness.autocode)."""
from datetime import datetime, timedelta, timezone

from skos.proposal_digest import FUNNEL_STAGES, generate_weekly_digest


def _t(proposal_id, status, when):
    return {"proposal_id": proposal_id, "status": status, "timestamp": when}


def test_zero_fills_categories_with_no_activity():
    report = generate_weekly_digest([])
    for stage in FUNNEL_STAGES:
        assert f"{stage}: 0" in report


def test_counts_scoped_to_last_7_days_excludes_older_transitions():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    transitions = [
        _t("p1", "submitted", now - timedelta(days=1)),
        _t("p2", "submitted", now - timedelta(days=6, hours=23)),
        _t("p3", "submitted", now - timedelta(days=8)),  # outside window, excluded
        _t("p4", "approved", now - timedelta(days=3)),
        _t("p5", "rejected", now - timedelta(days=10)),  # outside window, excluded
    ]

    report = generate_weekly_digest(transitions, reference_time=now)

    assert "submitted: 2" in report
    assert "approved: 1" in report
    assert "rejected: 0" in report
    assert "in_review: 0" in report


def test_accepts_iso_string_timestamps():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    transitions = [
        _t("p1", "in_review", "2026-08-01T00:00:00+00:00"),
        _t("p2", "in_review", "2026-07-20T00:00:00+00:00"),  # outside window
    ]

    report = generate_weekly_digest(transitions, reference_time=now)

    assert "in_review: 1" in report


def test_report_includes_window_bounds_and_is_human_readable():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    report = generate_weekly_digest([], reference_time=now)

    assert "2026-07-26" in report and "2026-08-02" in report
    assert "Weekly" in report
