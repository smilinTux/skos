"""The full WD-3 pipeline (run.py): collect -> assemble -> headline ->
render -> publish -> advance cursors -> DM.

Uses an ISOLATED `AdapterRegistry` with fake test-only sources (never the
shared `skos.watchdog.port.registry` the real WD-2 adapters register onto,
which other test modules populate on import) so these tests are fully
self-contained and never touch fleet/skcapstone/skgateway/hermes state.
"""
import json

import pytest

from skos.watchdog.cursor import read_cursor
from skos.watchdog.events import WatchdogEvent, WatchdogLink
from skos.watchdog.port import AdapterRegistry, WatchdogSourceAdapter
from skos.watchdog.publish import digests_dir, latest_dir
from skos.watchdog.run import collect_all, run_digest_and_deliver


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    yield


def _local_registry(*adapter_classes):
    reg = AdapterRegistry()
    for cls in adapter_classes:
        reg.register(cls)
    return reg


class _OkAdapter(WatchdogSourceAdapter):
    name = "ok-source"

    def collect(self, window):
        return [WatchdogEvent(
            ts=window.until, source=self.name, kind="Thing", object="obj",
            severity="problem", summary="something broke",
            link=WatchdogLink(uri="skworld://x", http="https://example.test/x"),
            ref="ok-source:1",
        )]


class _BrokenAdapter(WatchdogSourceAdapter):
    name = "broken-source"

    def collect(self, window):
        raise RuntimeError("simulated source outage")


class _QuietAdapter(WatchdogSourceAdapter):
    name = "quiet-source"

    def collect(self, window):
        return []


def _no_model(monkeypatch):
    """Force the headline path to skip any real network call."""
    from skos.watchdog import headline as hl
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)


def _no_dm(monkeypatch):
    from skos.watchdog import deliver as dl
    monkeypatch.setattr(dl, "default_sender", lambda text: True)


# ------------------------------------------------------------------ collect_all

def test_collect_all_folds_every_source_into_one_digest():
    reg = _local_registry(_OkAdapter, _QuietAdapter)
    digest, sources, run_until = collect_all(now="2026-08-10T07:45:00Z", registry=reg)
    assert set(sources) == {"ok-source", "quiet-source"}
    assert len(digest["problems"]) == 1
    assert digest["problems"][0]["ref"] == "ok-source:1"
    assert digest["per_source"]["ok-source"]["ok"] is True
    assert digest["per_source"]["quiet-source"]["events"] == 0


def test_broken_adapter_produces_a_noted_gap_not_an_exception():
    """Hard rule: 'Every adapter failure degrades to a noted gap in the
    digest, never a missing digest.'"""
    reg = _local_registry(_OkAdapter, _BrokenAdapter)
    digest, sources, _ = collect_all(now="2026-08-10T07:45:00Z", registry=reg)
    assert set(sources) == {"ok-source", "broken-source"}
    # the broken source surfaces as a notable SourceUnavailable line, not a raise
    unavailable = [e for e in digest["notable"] if e["kind"] == "SourceUnavailable"]
    assert len(unavailable) == 1
    assert unavailable[0]["source"] == "broken-source"
    assert "simulated source outage" in unavailable[0]["summary"]
    assert digest["per_source"]["broken-source"]["ok"] is False
    # the healthy source's own line is unaffected
    assert len(digest["problems"]) == 1


# ------------------------------------------------------------ run_digest_and_deliver

def test_dry_run_writes_nothing_advances_nothing_sends_nothing(monkeypatch):
    _no_model(monkeypatch)
    reg = _local_registry(_OkAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", dry_run=True, registry=reg)
    assert report["published"] is False
    assert report["sent"] is False
    assert read_cursor("ok-source") is None
    assert not digests_dir().joinpath("2026-08-10.json").exists()
    assert "something broke" in report["markdown"]


def test_full_run_publishes_advances_cursor_and_sends(monkeypatch):
    _no_model(monkeypatch)
    _no_dm(monkeypatch)
    reg = _local_registry(_OkAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", registry=reg)
    assert report["published"] is True
    assert report["sent"] is True
    assert read_cursor("ok-source") == "2026-08-10T07:45:00Z"
    assert latest_dir().joinpath("digest.json").exists()
    loaded = json.loads(latest_dir().joinpath("digest.json").read_text())
    assert loaded["problems"][0]["ref"] == "ok-source:1"


def test_no_send_still_publishes_and_advances_but_skips_dm(monkeypatch):
    _no_model(monkeypatch)
    from skos.watchdog import deliver as dl
    calls = []
    monkeypatch.setattr(dl, "default_sender", lambda text: calls.append(text) or True)
    reg = _local_registry(_OkAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", send=False, registry=reg)
    assert report["published"] is True
    assert report["sent"] is False
    assert calls == []
    assert read_cursor("ok-source") == "2026-08-10T07:45:00Z"


def test_cursor_advances_only_after_publish_lands(monkeypatch):
    """PROOF of the hard rule: 'Advance cursors only after a digest actually
    lands ... a crash before that must replay the same window.' Simulate a
    crash inside publish_digest and assert the cursor never moved."""
    _no_model(monkeypatch)
    import skos.watchdog.run as run_mod

    def _boom(*a, **kw):
        raise RuntimeError("simulated crash mid-publish")

    monkeypatch.setattr(run_mod, "publish_digest", _boom)
    reg = _local_registry(_OkAdapter)
    with pytest.raises(RuntimeError):
        run_digest_and_deliver(now="2026-08-10T07:45:00Z", registry=reg)
    assert read_cursor("ok-source") is None


def test_digest_still_renders_completely_when_skgateway_is_unreachable(monkeypatch):
    """PROOF: skgateway down/slow does not block the run; the headline
    degrades to the deterministic template and the digest still lands."""
    from skos.watchdog import headline as hl

    def _timeout(prompt, **kw):
        return None  # _chat_completion's own contract: never raises, None on any failure

    monkeypatch.setattr(hl, "_chat_completion", _timeout)
    _no_dm(monkeypatch)
    reg = _local_registry(_OkAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", registry=reg)
    assert report["published"] is True
    # the deterministic template fallback (digest.render_headline's shape)
    assert "problem" in report["digest"]["headline"]


def test_run_survives_a_source_going_down_and_still_publishes(monkeypatch):
    _no_model(monkeypatch)
    _no_dm(monkeypatch)
    reg = _local_registry(_OkAdapter, _BrokenAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", registry=reg)
    assert report["published"] is True
    assert read_cursor("broken-source") == "2026-08-10T07:45:00Z"
    unavailable = [e for e in report["digest"]["notable"] if e["kind"] == "SourceUnavailable"]
    assert len(unavailable) == 1


def test_date_override_is_applied_to_the_digest_and_the_filename(monkeypatch):
    _no_model(monkeypatch)
    _no_dm(monkeypatch)
    reg = _local_registry(_QuietAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", date="2026-08-09", registry=reg)
    assert report["digest"]["date"] == "2026-08-09"
    assert digests_dir().joinpath("2026-08-09.json").exists()
