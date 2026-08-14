"""The calendar adapter must fetch EVERY page, not gog's default first 10.

Found 2026-08-14. `gog calendar events` defaults to `--max 10` and, on the
installed v0.12.0, prints no truncation hint at all (the hint landed upstream
2026-07-21, four months after that release). The adapter passed neither
`--max` nor `--all-pages`, so it silently saw the first 10 events and treated
that as the whole calendar.

Measured on the live account, 120-day window: **10 events vs 234**. The GTD
calendar ingest was working from 4% of the calendar and reporting success.

`--all-pages` is the fix rather than a big `--max`: a magic number just moves
the cliff to whoever has more events than the number, and it moves silently.
"""

from __future__ import annotations

import json

from skos.adapters import calendar as cal


def _fake_run(recorder):
    class _Result:
        stdout = json.dumps({"events": []})

    def _run(argv, **kw):
        recorder.append(argv)
        return _Result()

    return _run


def test_the_gog_call_fetches_every_page(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(cal.subprocess, "run", _fake_run(calls))
    monkeypatch.setattr(cal, "CAL_ACCOUNTS", ["someone@example.com"])

    cal.CalendarAdapter().poll()

    assert calls, "the adapter made no gog call"
    argv = calls[0]
    assert "--all-pages" in argv, (
        "without --all-pages gog returns only its default first page (10 events) "
        f"and says nothing about the rest: {argv}"
    )


def test_the_call_still_asks_for_json_and_all_calendars(monkeypatch):
    """Guard the flags the adapter already depended on."""
    calls: list[list[str]] = []
    monkeypatch.setattr(cal.subprocess, "run", _fake_run(calls))
    monkeypatch.setattr(cal, "CAL_ACCOUNTS", ["someone@example.com"])

    cal.CalendarAdapter().poll()

    argv = calls[0]
    for flag in ("-j", "--all", "--days"):
        assert flag in argv, f"{flag} went missing from {argv}"
