"""Tests for gtd-ingest pull adapters (calendar + telegram) — pure logic, no network."""
import pytest

from skos.adapters import telegram as T


def test_telegram_parser_extracts_id_and_text():
    tbl = (
        "┃ ID  ┃ Date ┃ Sender ┃ Text ┃\n"
        "│ 700 │ d │ dave │ normal message │\n"
        "│ 701 │ d │ dave │ todo: call Dave Rich │\n"
    )
    rows = T._parse_poll(tbl)
    assert ("700", "normal message") in rows
    assert ("701", "todo: call Dave Rich") in rows


def test_telegram_parser_joins_wrapped_continuation():
    tbl = (
        "│ 800 │ d │ dave │ task: file the │\n"
        "│     │   │      │ Q3 trust paperwork │\n"
    )
    rows = dict(T._parse_poll(tbl))
    assert rows["800"] == "task: file the Q3 trust paperwork"


@pytest.mark.parametrize("body,expected", [
    ("todo: call Dave", "call Dave"),
    ("Task - file paperwork", "file paperwork"),
    ("GTD: review contract", "review contract"),
    ("just a normal note", None),
    ("SCOTUS ruling summary", None),
])
def test_telegram_trigger_regex(body, expected):
    m = T._TRIG_RE.match(body)
    assert (m.group(1).strip() if m else None) == expected


def test_calendar_noise_filter_terms_present():
    from skos.adapters.calendar import _NOISE
    for term in ("dose", "power hour", "moon", "affirmation"):
        assert term in _NOISE


def test_adapters_register_on_gtd_ingest_port():
    from skos import adapters
    reg = adapters._adapters()
    assert "calendar" in reg and "telegram" in reg
    from skos.gtd_ingest import registry
    assert "calendar" in registry.available_for("gtd-ingest")
    assert "telegram" in registry.available_for("gtd-ingest")
