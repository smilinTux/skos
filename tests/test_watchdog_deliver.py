"""DM delivery (WD-3): the existing Hermes path (sk-status report's), reused
via an injectable sender so no test ever shells out."""
from skos.watchdog import deliver as dl


def _digest(**overrides):
    base = {
        "date": "2026-08-10", "headline": "Quiet day.",
        "problems": [], "notable": [], "info_counts": {},
    }
    base.update(overrides)
    return base


def _event(summary="fire", link_http="https://atlas.skworld.io/", **kw):
    base = {"summary": summary, "source": "fleet", "kind": "K", "object": "o",
            "link": {"uri": "skworld://a", "http": link_http}}
    base.update(kw)
    return base


def test_format_dm_includes_headline_and_date():
    body = dl.format_dm(_digest(headline="Two problems, one notable."))
    assert "2026-08-10" in body
    assert "Two problems, one notable." in body


def test_format_dm_includes_deep_links_for_problems():
    body = dl.format_dm(_digest(problems=[_event(summary="crash loop")]))
    assert "crash loop" in body
    assert "https://atlas.skworld.io/" in body


def test_format_dm_caps_lines_per_bucket():
    problems = [_event(summary=f"issue {i}") for i in range(20)]
    body = dl.format_dm(_digest(problems=problems))
    assert "issue 0" in body
    assert "more." in body


def test_format_dm_never_contains_banned_dashes():
    body = dl.format_dm(_digest(headline="skchat — flapped",
                                problems=[_event(summary="crash – four times")]))
    assert "—" not in body
    assert "–" not in body


def test_format_dm_reports_nothing_firing_when_quiet():
    body = dl.format_dm(_digest())
    assert "Nothing firing or notable." in body


def test_send_digest_dm_uses_injected_sender():
    sent = []
    ok = dl.send_digest_dm(_digest(headline="hi"), sender=lambda text: sent.append(text) or True)
    assert ok is True
    assert len(sent) == 1
    assert "hi" in sent[0]


def test_send_digest_dm_returns_false_when_sender_fails():
    ok = dl.send_digest_dm(_digest(), sender=lambda text: False)
    assert ok is False


def test_default_sender_never_raises_when_hermes_is_absent(monkeypatch):
    """No live DM in tests: force the real subprocess path to fail (as it
    would with no `hermes` binary on PATH) and prove it degrades to False,
    never an exception."""
    import subprocess

    def _raise_missing(*a, **kw):
        raise FileNotFoundError("hermes: not found")

    monkeypatch.setattr(subprocess, "run", _raise_missing)
    assert dl.default_sender("test message") is False
