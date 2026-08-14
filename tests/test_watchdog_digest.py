"""Digest assembly: bucketing, dedupe, and the exact key shape card C-9's
Dart parser (skcode_digest.dart) reads: date, headline, problems, notable,
info_counts. This test module is the contract check the WD-1 card requires
before shipping: a changed key here silently breaks a merged consumer.
"""
from skos.watchdog.digest import assemble_digest, render_headline
from skos.watchdog.events import WatchdogEvent, WatchdogLink
from skos.watchdog.port import Window


def _ev(source, severity, ref, ts="2026-08-10T06:00:00Z", **kw):
    base = dict(ts=ts, source=source, kind="Thing", object="obj", severity=severity,
                summary=f"summary for {ref}", ref=ref)
    base.update(kw)
    return WatchdogEvent(**base)


def test_digest_has_exactly_the_keys_c9_parses():
    d = assemble_digest([])
    # SkcodeDigest.fromJson (skworld-app skcode_client) reads: date, headline,
    # problems, notable, info_counts. window/per_source are extra spec fields
    # C-9 does not read; both may be present, neither may be missing, and none
    # of the five required keys may be renamed.
    required = {"date", "headline", "problems", "notable", "info_counts"}
    assert required <= set(d.keys())
    assert isinstance(d["date"], str)
    assert isinstance(d["headline"], str)
    assert isinstance(d["problems"], list)
    assert isinstance(d["notable"], list)
    assert isinstance(d["info_counts"], dict)


def test_empty_digest_is_well_formed():
    d = assemble_digest([], date="2026-08-10")
    assert d["date"] == "2026-08-10"
    assert d["problems"] == []
    assert d["notable"] == []
    assert d["info_counts"] == {}
    assert d["headline"] == "No events since the last digest."


def test_events_bucket_by_severity():
    events = [
        _ev("fleet", "problem", "p1"),
        _ev("scheduler", "notable", "n1"),
        _ev("git", "info", "i1"),
        _ev("git", "info", "i2"),
    ]
    d = assemble_digest(events, date="2026-08-10")
    assert len(d["problems"]) == 1 and d["problems"][0]["ref"] == "p1"
    assert len(d["notable"]) == 1 and d["notable"][0]["ref"] == "n1"
    assert d["info_counts"] == {"git": 2}


def test_problem_and_notable_entries_are_full_event_dicts_with_link():
    ev = _ev("fleet", "problem", "p1",
              link=WatchdogLink(uri="skworld://skchat/x", http="https://atlas.skworld.io/"))
    d = assemble_digest([ev], date="2026-08-10")
    row = d["problems"][0]
    assert row["link"] == {"uri": "skworld://skchat/x", "http": "https://atlas.skworld.io/"}
    assert row["ref"] == "p1"
    assert row["source"] == "fleet"


def test_dedupe_by_ref_keeps_first_occurrence():
    e1 = _ev("fleet", "problem", "dup", ts="2026-08-10T01:00:00Z", summary="first")
    e2 = _ev("fleet", "problem", "dup", ts="2026-08-10T02:00:00Z", summary="second")
    d = assemble_digest([e1, e2], date="2026-08-10")
    assert len(d["problems"]) == 1
    assert d["problems"][0]["summary"] == "first"


def test_events_without_ref_are_never_collapsed():
    e1 = _ev("fleet", "problem", "", ts="2026-08-10T01:00:00Z")
    e2 = _ev("fleet", "problem", "", ts="2026-08-10T02:00:00Z")
    d = assemble_digest([e1, e2], date="2026-08-10")
    assert len(d["problems"]) == 2


def test_problems_and_notable_are_sorted_by_timestamp():
    late = _ev("fleet", "problem", "late", ts="2026-08-10T09:00:00Z")
    early = _ev("fleet", "problem", "early", ts="2026-08-10T01:00:00Z")
    d = assemble_digest([late, early], date="2026-08-10")
    assert [r["ref"] for r in d["problems"]] == ["early", "late"]


def test_date_defaults_from_window_when_not_given():
    w = Window(since="2026-08-09T00:00:00Z", until="2026-08-10T07:45:00Z")
    d = assemble_digest([], window=w)
    assert d["date"] == "2026-08-10"
    assert d["window"] == {"from": "2026-08-09T00:00:00Z", "to": "2026-08-10T07:45:00Z"}


def test_date_defaults_from_latest_event_when_no_window_given():
    e = _ev("fleet", "info", "i1", ts="2026-08-11T03:00:00Z")
    d = assemble_digest([e])
    assert d["date"] == "2026-08-11"


def test_per_source_passes_through_when_supplied():
    per_source = {"fleet": {"ok": True, "events": 1, "cursor": "2026-08-10T06:00:00Z"}}
    d = assemble_digest([_ev("fleet", "info", "i1")], date="2026-08-10", per_source=per_source)
    assert d["per_source"] == per_source


def test_per_source_defaults_to_empty_dict():
    d = assemble_digest([], date="2026-08-10")
    assert d["per_source"] == {}


def test_headline_reflects_counts():
    events = [_ev("fleet", "problem", "p1"), _ev("fleet", "problem", "p2"),
              _ev("git", "notable", "n1"), _ev("git", "info", "i1")]
    d = assemble_digest(events, date="2026-08-10")
    headline = d["headline"]
    assert "2 problems" in headline
    assert "1 notable item" in headline
    assert "1 quiet info event" in headline


def test_render_headline_never_raises_on_empty_input():
    assert render_headline([], [], {}) == "No events since the last digest."


def test_source_unavailable_events_surface_in_digest_as_notable():
    from skos.watchdog.events import source_unavailable
    ev = source_unavailable("scheduler", ts="2026-08-10T06:00:00Z", error="timeout")
    d = assemble_digest([ev], date="2026-08-10")
    assert len(d["notable"]) == 1
    assert d["notable"][0]["kind"] == "SourceUnavailable"
