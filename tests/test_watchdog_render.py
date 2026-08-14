"""The Markdown renderer (WD-3): deep links surface, no banned dashes ever."""
from skos.watchdog.render import render_markdown, strip_banned_dashes, link_of


def _digest(**overrides):
    base = {
        "date": "2026-08-10",
        "headline": "Quiet day.",
        "problems": [],
        "notable": [],
        "info_counts": {},
        "per_source": {},
    }
    base.update(overrides)
    return base


def _event(severity="problem", **kw):
    base = {
        "ts": "2026-08-10T06:00:00Z", "source": "fleet", "kind": "Thing",
        "object": "obj", "severity": severity, "summary": "something happened",
        "link": {"uri": "skworld://skos/watchdog/x", "http": "https://atlas.skworld.io/"},
        "ref": "fleet:1",
    }
    base.update(kw)
    return base


# ------------------------------------------------------------- strip_banned_dashes

def test_strip_banned_dashes_replaces_spaced_em_dash_with_comma():
    assert "—" not in strip_banned_dashes("one — two")
    assert strip_banned_dashes("one — two") == "one, two"


def test_strip_banned_dashes_replaces_spaced_en_dash_with_comma():
    assert "–" not in strip_banned_dashes("one – two")


def test_strip_banned_dashes_replaces_bare_dash_with_hyphen():
    out = strip_banned_dashes("5–10 items")
    assert "–" not in out
    assert out == "5-10 items"


def test_strip_banned_dashes_leaves_plain_hyphens_alone():
    assert strip_banned_dashes("studs- thank you") == "studs- thank you"


def test_strip_banned_dashes_no_op_on_clean_text():
    assert strip_banned_dashes("nothing to see here") == "nothing to see here"


def test_strip_banned_dashes_handles_empty():
    assert strip_banned_dashes("") == ""


# ------------------------------------------------------------------- link_of

def test_link_of_prefers_http():
    ev = {"link": {"uri": "skworld://a", "http": "https://b"}}
    assert link_of(ev) == "https://b"


def test_link_of_falls_back_to_uri_when_http_empty():
    ev = {"link": {"uri": "skworld://a", "http": ""}}
    assert link_of(ev) == "skworld://a"


def test_link_of_empty_when_neither_present():
    assert link_of({"link": {}}) == ""
    assert link_of({}) == ""


# --------------------------------------------------------------- render_markdown

def test_render_markdown_includes_date_and_headline():
    d = _digest(date="2026-08-10", headline="Two problems today.")
    md = render_markdown(d)
    assert "2026-08-10" in md
    assert "Two problems today." in md


def test_every_problem_and_notable_line_is_clickable():
    """DEEP LINKS ARE THE POINT (WD-3 card): every problems/notable row must
    carry its link.http (or link.uri fallback) in the rendered text."""
    d = _digest(
        problems=[_event(severity="problem", summary="fire", ref="p1")],
        notable=[_event(severity="notable", summary="fyi", ref="n1",
                        link={"uri": "skworld://skos/watchdog/n1", "http": ""})],
    )
    md = render_markdown(d)
    assert "https://atlas.skworld.io/" in md   # problem's http link
    assert "skworld://skos/watchdog/n1" in md  # notable's uri fallback (http empty)


def test_render_markdown_never_contains_banned_dashes_even_from_source_summary():
    d = _digest(
        headline="skchat — daemon flapped",
        problems=[_event(summary="crash loop – 4 restarts")],
    )
    md = render_markdown(d)
    assert "—" not in md
    assert "–" not in md


def test_render_markdown_renders_empty_sections_honestly():
    md = render_markdown(_digest())
    assert "Problems" in md
    assert "Notable" in md
    assert "none" in md.lower()


def test_render_markdown_renders_info_counts_and_per_source():
    d = _digest(info_counts={"git": 3, "fleet": 1},
                per_source={"fleet": {"ok": True, "events": 1, "cursor": "2026-08-10T06:00:00Z"},
                            "scheduler": {"ok": False, "events": 1, "cursor": "2026-08-10T06:00:00Z"}})
    md = render_markdown(d)
    assert "4 info event" in md
    assert "git: 3" in md
    assert "fleet: 1" in md
    assert "DEGRADED" in md  # scheduler.ok == False


def test_render_markdown_never_raises_on_missing_keys():
    assert render_markdown({}) != ""


def test_render_markdown_is_deterministic():
    d = _digest(problems=[_event(ref="p1")])
    assert render_markdown(d) == render_markdown(d)
