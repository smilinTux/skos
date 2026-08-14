"""scheduler adapter: the generalized staleness watchdog over the cron
run-ledger, REUSING skharness.jobs.read_job_runs (card C-8's median-gap
cadence rule) rather than inventing a second one.

SchedulerAdapter does ``from skharness.jobs import read_job_runs`` inside
collect() -- an optional sibling import, same as skos.autopilot. Every test
writes its own throwaway ledger file (SKOS_CRON_LEDGER env override); none
ever touches ~/.skcapstone/logs/cron-ledger.jsonl.
"""
import json
import sys

import pytest

from skos.watchdog.adapters.scheduler import SchedulerAdapter
from skos.watchdog.port import Window, collect_safe


def _window(since="2026-08-09T00:00:00Z", until="2026-08-10T03:00:00Z"):
    return Window(since=since, until=until)


def _write_ledger(path, records):
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    p = tmp_path / "cron-ledger.jsonl"
    monkeypatch.setenv("SKOS_CRON_LEDGER", str(p))
    return p


def test_missing_ledger_is_quiet_not_unavailable(isolated_ledger):
    # the ledger file itself is never created; skharness.jobs already treats
    # a missing ledger as "no jobs known yet" rather than an error.
    events = SchedulerAdapter().collect(_window())
    assert events == []


def test_failed_job_is_a_problem(isolated_ledger):
    _write_ledger(isolated_ledger, [
        {"job": "flaky-job", "host": "noroc2027", "start": "2026-08-09T02:00:00",
         "dur_s": 5, "exit": 1, "ok": False, "tail": "boom"},
    ])
    events = SchedulerAdapter().collect(_window())
    assert len(events) == 1
    ev = events[0]
    assert ev.source == "scheduler"
    assert ev.kind == "JobFailed"
    assert ev.severity == "problem"
    assert ev.object == "flaky-job"
    assert "flaky-job" in ev.summary


def test_stalled_job_is_a_problem_with_its_own_cadence(isolated_ledger):
    # ten runs roughly an hour apart, then silence for the rest of the window
    # -> cadence-inferred stale threshold is small, so the long gap flags.
    records = []
    for i in range(10):
        records.append({"job": "hourly-job", "host": "h", "dur_s": 1, "ok": True,
                         "start": f"2026-08-09T{i:02d}:00:00"})
    _write_ledger(isolated_ledger, records)
    events = SchedulerAdapter().collect(_window(until="2026-08-10T03:00:00Z"))
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "JobStalled"
    assert ev.severity == "problem"
    assert ev.object == "hourly-job"


def test_healthy_ledger_yields_one_quiet_summary_line(isolated_ledger):
    _write_ledger(isolated_ledger, [
        {"job": "daily-sync", "host": "h", "start": "2026-08-09T02:00:00", "ok": True, "dur_s": 3},
        {"job": "daily-sync", "host": "h", "start": "2026-08-10T02:00:00", "ok": True, "dur_s": 3},
    ])
    events = SchedulerAdapter().collect(_window(until="2026-08-10T03:00:00Z"))
    assert len(events) == 1
    assert events[0].kind == "SchedulerHealthy"
    assert events[0].severity == "info"


def test_malformed_ledger_lines_never_raise(isolated_ledger):
    with isolated_ledger.open("w", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write('{"job": "ok-job", "start": "2026-08-10T02:00:00", "ok": true}\n')
        f.write("\n")
    events = SchedulerAdapter().collect(_window(until="2026-08-10T03:00:00Z"))
    # the malformed line is skipped; the well-formed one still folds cleanly.
    assert events == [] or events[0].kind == "SchedulerHealthy"


def test_degrades_to_source_unavailable_when_skharness_is_absent(monkeypatch, isolated_ledger):
    # This adapter does `from skharness.jobs import read_job_runs` inside
    # collect(); simulating its absence proves the graceful degrade without
    # requiring an actual extras-less interpreter for this one test.
    monkeypatch.setitem(sys.modules, "skharness", None)
    monkeypatch.setitem(sys.modules, "skharness.jobs", None)
    events = collect_safe(SchedulerAdapter(), _window())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "SourceUnavailable"
    assert ev.source == "scheduler"
    assert ev.severity == "notable"
