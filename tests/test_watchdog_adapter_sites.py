"""Tests for the WD-12 sites adapter (skos.watchdog.adapters.sites).

NOTHING HERE MAKES A REAL NETWORK REQUEST (card hard rule). The one real
read boundary, `_open`, is monkeypatched by an autouse fixture whose default
raises, `time.sleep` is stubbed so retry backoff never actually waits, and
`test_no_test_invokes_the_real_urlopen` proves the stdlib entry point itself
is never reached even when every higher-level seam is exercised.
"""
from __future__ import annotations

import time

import pytest
from urllib.error import HTTPError

from skos.watchdog.adapters import sites
from skos.watchdog.adapters.sites import SitesAdapter, SitesCheckError
from skos.watchdog.port import Window, collect_safe

#: The real config reader, captured before the autouse fixture replaces it,
#: mirroring test_watchdog_adapter_email.py's `_REAL_SEARCH` technique.
_REAL_SITES = sites._sites


def _window(since="2026-08-09T12:00:00Z", until="2026-08-10T12:00:00Z"):
    return Window(since=since, until=until)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "does-not-exist.env"))
    monkeypatch.delenv("SKWATCHDOG_SITES", raising=False)
    monkeypatch.setattr(sites, "_sites", lambda: [])
    monkeypatch.setattr(sites, "_open", lambda url, method, timeout: (
        _ for _ in ()).throw(AssertionError(f"a test reached the network: {method} {url}")))
    monkeypatch.setattr(sites.time, "sleep", lambda s: None)
    yield


# ---------------------------------------------------------- configuration ---

def test_no_configured_sites_is_a_quiet_empty_run():
    assert SitesAdapter().collect(_window()) == []


def test_sites_config_resolves_from_env_comma_separated_and_dedupes(monkeypatch):
    monkeypatch.setenv(
        "SKWATCHDOG_SITES",
        " https://a.example/, https://b.example/ ,https://a.example/",
    )
    assert _REAL_SITES() == ["https://a.example/", "https://b.example/"]


def test_sites_config_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("SKWATCHDOG_SITES", raising=False)
    assert _REAL_SITES() == []


# -------------------------------------------------------- retry mechanics ---

def test_check_with_retries_recovers_from_a_single_blip(monkeypatch):
    monkeypatch.setattr(sites.time, "monotonic", lambda: 0.0)
    results = iter([(False, "HTTP 500"), (True, "HTTP 200")])
    monkeypatch.setattr(sites, "_check_once", lambda url, timeout: next(results))
    status, detail = sites._check_with_retries("https://x.example/", deadline=1000.0)
    assert status == "ok"
    assert detail == "HTTP 200"


def test_check_with_retries_declares_down_only_after_every_attempt_fails(monkeypatch):
    monkeypatch.setattr(sites.time, "monotonic", lambda: 0.0)
    calls = []

    def _fake(url, timeout):
        calls.append(url)
        return False, "HTTP 503"

    monkeypatch.setattr(sites, "_check_once", _fake)
    status, detail = sites._check_with_retries("https://x.example/", deadline=1000.0)
    assert status == "down"
    assert detail == "HTTP 503"
    assert len(calls) == sites.SITES_RETRIES


def test_check_with_retries_is_inconclusive_when_budget_runs_out_mid_series(monkeypatch):
    # First loop iteration's deadline check passes (0.0 < 10.0), attempt fails;
    # second iteration's deadline check fails (20.0 >= 10.0) -> inconclusive,
    # never "down", because the series never actually finished.
    seq = iter([0.0, 20.0, 20.0, 20.0])
    monkeypatch.setattr(sites.time, "monotonic", lambda: next(seq))
    monkeypatch.setattr(sites, "_check_once", lambda url, timeout: (False, "HTTP 500"))
    status, detail = sites._check_with_retries("https://x.example/", deadline=10.0)
    assert status == "inconclusive"


# ------------------------------------------------------------ reachability --

def test_a_healthy_site_produces_no_problem_and_a_summary_line(monkeypatch):
    monkeypatch.setattr(sites, "_sites", lambda: ["https://ok.example/"])
    monkeypatch.setattr(sites, "_check_with_retries", lambda url, deadline: ("ok", "HTTP 200"))
    monkeypatch.setattr(SitesAdapter, "_check_links", lambda self, url, deadline: [])
    events = SitesAdapter().collect(_window())
    assert [e.kind for e in events] == ["SitesHealthy"]
    assert events[0].severity == "info"
    assert events[0].summary == "1 site(s) checked, all reachable, no broken links found."


def test_an_unreachable_site_is_a_problem_event(monkeypatch):
    monkeypatch.setattr(sites, "_sites", lambda: ["https://down.example/"])
    monkeypatch.setattr(sites, "_check_with_retries",
                         lambda url, deadline: ("down", "HTTP 503"))
    events = SitesAdapter().collect(_window())
    down = [e for e in events if e.kind == "SiteUnreachable"]
    assert len(down) == 1
    ev = down[0]
    assert ev.source == "sites"
    assert ev.object == "down.example"
    assert ev.severity == "problem"
    assert ev.summary == f"down.example was unreachable after {sites.SITES_RETRIES} attempts (HTTP 503)."
    assert ev.link.http == "https://down.example/"
    assert ev.meta["attempts"] == sites.SITES_RETRIES
    assert ev.ref == "sites:unreachable:down.example:2026-08-10"


def test_below_network_fault_threshold_every_down_site_still_gets_its_own_problem(monkeypatch):
    urls = ["https://a.example/", "https://b.example/"]
    assert len(urls) < sites._NETWORK_FAULT_MIN_SITES
    monkeypatch.setattr(sites, "_sites", lambda: urls)
    monkeypatch.setattr(sites, "_check_with_retries",
                         lambda url, deadline: ("down", "HTTP 500"))
    events = SitesAdapter().collect(_window())
    down = [e for e in events if e.kind == "SiteUnreachable"]
    assert len(down) == 2
    assert not [e for e in events if e.kind == "SourceUnavailable"]


def test_every_site_down_at_or_above_threshold_reads_as_our_network_not_theirs(monkeypatch):
    urls = [f"https://s{i}.example/" for i in range(sites._NETWORK_FAULT_MIN_SITES)]
    monkeypatch.setattr(sites, "_sites", lambda: urls)
    monkeypatch.setattr(sites, "_check_with_retries",
                         lambda url, deadline: ("down", "HTTP 000 connect failed"))
    with pytest.raises(SitesCheckError):
        SitesAdapter().collect(_window())
    # collect_safe folds it into exactly one SourceUnavailable, never N problems
    events = collect_safe(SitesAdapter(), _window())
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"
    assert events[0].source == "sites"
    assert events[0].severity == "notable"


def test_a_partial_down_below_full_failure_does_not_raise(monkeypatch):
    urls = [f"https://s{i}.example/" for i in range(sites._NETWORK_FAULT_MIN_SITES + 1)]
    monkeypatch.setattr(sites, "_sites", lambda: urls)

    def _fake(url, deadline):
        return ("down", "HTTP 500") if url != urls[0] else ("ok", "HTTP 200")

    monkeypatch.setattr(sites, "_check_with_retries", _fake)
    monkeypatch.setattr(SitesAdapter, "_check_links", lambda self, url, deadline: [])
    events = SitesAdapter().collect(_window())  # must not raise
    assert len([e for e in events if e.kind == "SiteUnreachable"]) == len(urls) - 1


# ------------------------------------------------------------- run budget ---

def test_budget_spent_before_any_site_is_checked_is_a_visible_gap_never_a_pass(monkeypatch):
    monkeypatch.setattr(sites, "_sites", lambda: ["https://a.example/", "https://b.example/"])
    monkeypatch.setattr(sites, "SITES_RUN_BUDGET_S", -1.0)
    events = SitesAdapter().collect(_window())
    assert [e.kind for e in events] == ["SiteCheckBudgetSpent"]
    assert events[0].severity == "notable"
    assert events[0].meta["skipped"] == 2
    # never a false all-clear: no SitesHealthy line when nothing was actually checked
    assert not [e for e in events if e.kind == "SitesHealthy"]


# --------------------------------------------------------------- read path --

def test_head_is_tried_first_and_falls_back_to_get_on_405(monkeypatch):
    calls = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_open(url, method, timeout):
        calls.append(method)
        if method == "HEAD":
            raise HTTPError(url, 405, "Method Not Allowed", None, None)
        return _Resp()

    monkeypatch.setattr(sites, "_open", _fake_open)
    ok, detail = sites._check_once("https://x.example/", 5.0)
    assert ok is True
    assert detail == "HTTP 200"
    assert calls == ["HEAD", "GET"]


def test_a_non_405_http_error_is_reported_without_a_get_fallback(monkeypatch):
    calls = []

    def _fake_open(url, method, timeout):
        calls.append(method)
        raise HTTPError(url, 404, "Not Found", None, None)

    monkeypatch.setattr(sites, "_open", _fake_open)
    ok, detail = sites._check_once("https://x.example/", 5.0)
    assert ok is False
    assert detail == "HTTP 404"
    assert calls == ["HEAD"]  # a real 404 answers the question; no need to retry via GET


def test_a_connection_failure_is_reported_not_raised(monkeypatch):
    from urllib.error import URLError

    monkeypatch.setattr(sites, "_open", lambda url, method, timeout: (
        _ for _ in ()).throw(URLError("no route to host")))
    ok, detail = sites._check_once("https://x.example/", 5.0)
    assert ok is False
    assert "no route to host" in detail


# ------------------------------------------------------------ broken links --

def test_link_extractor_keeps_only_absolute_http_links():
    html = (
        '<a href="/relative">rel</a>'
        '<a href="https://good.example/">good</a>'
        '<a href="mailto:a@b.com">mail</a>'
        '<a href="tel:+15551234">tel</a>'
        '<a href="#top">anchor</a>'
        '<a href="javascript:void(0)">js</a>'
    )
    links = sites._extract_links(html, "https://site.example/", cap=20)
    assert links == ["https://site.example/relative", "https://good.example/"]


def test_link_extractor_respects_the_cap():
    html = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(sites.MAX_LINKS_PER_SITE + 5))
    links = sites._extract_links(html, "https://site.example/", cap=sites.MAX_LINKS_PER_SITE)
    assert len(links) == sites.MAX_LINKS_PER_SITE


def test_broken_links_on_a_reachable_site_are_notable_never_a_problem(monkeypatch):
    monkeypatch.setattr(sites, "_sites", lambda: ["https://site.example/"])
    monkeypatch.setattr(sites, "_check_with_retries", lambda url, deadline: ("ok", "HTTP 200"))
    html = '<a href="/broken">x</a><a href="https://good.example/">y</a>'
    monkeypatch.setattr(sites, "_fetch_body", lambda url, timeout, max_bytes: html)

    def _fake_check_once(url, timeout):
        if "broken" in url:
            return False, "HTTP 404"
        return True, "HTTP 200"

    monkeypatch.setattr(sites, "_check_once", _fake_check_once)
    events = SitesAdapter().collect(_window())
    broken = [e for e in events if e.kind == "BrokenLinksFound"]
    assert len(broken) == 1
    ev = broken[0]
    assert ev.severity == "notable"
    assert ev.object == "site.example"
    assert ev.summary == "1 broken link(s) found on site.example."
    assert ev.meta["broken"] == ["https://site.example/broken"]
    assert not [e for e in events if e.severity == "problem"]


def test_a_page_fetch_failure_during_link_discovery_is_swallowed_quietly(monkeypatch):
    """Reachability was already confirmed by the retry series above; a
    failure fetching the body for link discovery is not a second finding."""
    from urllib.error import URLError

    monkeypatch.setattr(sites, "_sites", lambda: ["https://site.example/"])
    monkeypatch.setattr(sites, "_check_with_retries", lambda url, deadline: ("ok", "HTTP 200"))
    monkeypatch.setattr(sites, "_fetch_body", lambda url, timeout, max_bytes: (
        _ for _ in ()).throw(URLError("reset by peer")))
    events = SitesAdapter().collect(_window())
    assert [e.kind for e in events] == ["SitesHealthy"]


# --------------------------------------------------------------- registry ---

def test_sites_is_registered_on_the_watchdog_source_port():
    from skos.watchdog.port import registry
    assert registry.lookup("watchdog-source", "sites") is SitesAdapter


def test_load_all_registers_sites():
    from skos.watchdog.adapters import load_all
    assert "sites" in load_all()


# ------------------------------------------------------- no real network ----

def test_no_test_invokes_the_real_urlopen(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("a test spawned a real HTTP connection")

    monkeypatch.setattr(sites, "urlopen", _boom)
    monkeypatch.setattr(sites, "_sites", lambda: ["https://ok.example/"])
    monkeypatch.setattr(sites, "_check_with_retries", lambda url, deadline: ("ok", "HTTP 200"))
    monkeypatch.setattr(SitesAdapter, "_check_links", lambda self, url, deadline: [])
    events = SitesAdapter().collect(_window())
    assert events and events[0].kind == "SitesHealthy"
