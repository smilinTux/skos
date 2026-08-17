"""`skos watchdog digest` CLI wiring: the by-hand command the WD-3 card adds.
No schedule is touched here -- WD-4 owns the 07:45 cutover; this only proves
the command itself runs end to end against the REAL Phase-1 adapters, fully
isolated from any real fleet/coordination/ITIL state on this machine via env
overrides, and against a fully isolated watchdog state root.

The WD-6 chat/email sources are the exception, and are STUBBED here rather
than merely env-isolated. Those three read Chef's private correspondence
(skchat threads, the Telegram window, the 4-C Gmail lanes), and the card that
built them forbids a test from touching any of it. Env isolation alone is not
enough for them: `skchat.history.ChatHistory` resolves its own store
independently of `SKCAPSTONE_HOME` (a real store WAS read here before this
stub existed), and `secret_env`'s operator-env lookup is process-cached, so a
mail account or Telegram chat id can survive an env override. Stub the read,
and there is nothing left to get lucky about.

WD-12's `sites` source is the same shape of exception for a different reason:
it is the one adapter that opens a real network connection. See
`_isolate_network_sources` below for what that cost before it was closed.

Card 04ad64d7's `systemd_tier_a` is the third exception, for a third reason:
env isolation cannot reach it at all. It reads this machine's live systemd,
which has no env override and no store to redirect, so it is stubbed at its
one seam. See `_isolate_systemd_source`.

Isolation is applied by ONE autouse fixture rather than a call at the top of
each test. A per-test call is a thing a new test can forget, and forgetting it
is not a visible failure -- it is a test that quietly reaches live state. That
is exactly how `sites` came to reach the live internet from this file.
"""
import pytest
from typer.testing import CliRunner

from skos.cli import app
from skos.watchdog.cursor import read_cursor
from skos.watchdog.publish import latest_dir

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Close every source's reach before any test in this module drives the
    real CLI against the real adapter registry."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "skcapstone"))
    monkeypatch.setenv("SKFLEET_ROOT", str(tmp_path / "skcapstone" / "fleet"))
    monkeypatch.delenv("SK_WATCHDOG_GIT_REPOS", raising=False)
    from skos.watchdog import headline as hl
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    from skos.watchdog import deliver as dl
    monkeypatch.setattr(dl, "default_sender", lambda text: True)
    _isolate_private_sources(tmp_path, monkeypatch)
    _isolate_network_sources(monkeypatch)
    _isolate_systemd_source(monkeypatch)


#: The fake systemd view every test in this module runs the real
#: `systemd_tier_a` adapter against: two Tier A units in the user scope, one of
#: them enabled and STOPPED. That second one is the whole point of card
#: 04ad64d7, and it is what makes the end-to-end acceptance test below able to
#: assert that a stopped Tier A service reaches a human, without stopping a
#: live service to find out.
STOPPED_TIER_A_UNIT = "skgateway.service"


def _fake_systemd_units(scope):
    from skos.watchdog.adapters.systemd_tier_a import UnitState
    if scope != "user":
        return []
    return [
        UnitState(unit=STOPPED_TIER_A_UNIT, scope="user", unit_file_state="enabled",
                  active_state="inactive", sub_state="dead", type="simple", tier_a=True),
        UnitState(unit="sknoded.service", scope="user", unit_file_state="enabled",
                  active_state="active", sub_state="running", type="simple", tier_a=True),
        # deliberately off, and deliberately NOT a finding
        UnitState(unit="shadowcopy-monitor.service", scope="user",
                  unit_file_state="disabled", active_state="inactive", sub_state="dead",
                  type="simple", tier_a=True),
    ]


def _isolate_systemd_source(monkeypatch):
    """Card 04ad64d7's `systemd_tier_a` reads this machine's LIVE systemd.

    Unlike every other source here there is no env var to point elsewhere and
    no store to redirect: the unit state of the box the test runs on is the
    input. Left open, these tests would shell out to the real `systemctl`,
    read whatever this developer happens to have running, and report a real
    stopped service as a finding in a test digest, which is both a live read
    and a result that changes depending on whose box ran it.

    So the adapter's one seam (`default_unit_reader`, the only place that
    module talks to systemd) is replaced with a fixed fake view, and
    `subprocess.run` inside the module is nailed shut behind it so a future
    refactor that grows a second path out to the shell fails here loudly
    instead of quietly going live.
    """
    from skos.watchdog.adapters import systemd_tier_a as ta
    monkeypatch.delenv("SKWATCHDOG_SYSTEMD_SCOPES", raising=False)
    monkeypatch.setattr(ta, "default_unit_reader", _fake_systemd_units)
    monkeypatch.setattr(ta.subprocess, "run", lambda *a, **kw: (
        _ for _ in ()).throw(AssertionError("the CLI test reached the real systemctl")))


def _isolate_network_sources(monkeypatch):
    """`sites` (WD-12) is the ONE adapter that opens a network connection, and
    it sits on the same registry every test in this module drives end to end.

    Left open, a box that has `SKWATCHDOG_SITES` configured makes each test
    here check the operator's real domains over the real internet, and spend
    the adapter's whole `SITES_RUN_BUDGET_S` (90s, by design: the digest must
    publish on time even if the internet is down) doing it. Measured on a box
    with three unreachable sites configured: 92-98s per test, 383s for this
    one file, against 2.9s with nothing configured. Almost none of it was CPU.

    Belt: `SKWATCHDOG_SITES`, the env the site list resolves through -- the
    same treatment the three private sources get above, and the entry WD-12
    did not add. Braces: `_open`, the single seam `adapters/sites.py` names in
    its own docstring as "the ONLY place this module opens a real network
    connection". The retry backoff is pinned to zero so no series of attempts
    can ever put this suite to sleep on the wall clock.
    """
    from skos.watchdog.adapters import sites as st
    monkeypatch.delenv("SKWATCHDOG_SITES", raising=False)
    monkeypatch.setattr(st, "_open", lambda url, method, timeout: (
        _ for _ in ()).throw(AssertionError("the CLI test reached the live network")))
    monkeypatch.setattr(st, "SITES_RETRY_DELAY_S", 0.0)


def _isolate_private_sources(tmp_path, monkeypatch):
    """No live mailbox, no live Telegram window, no live skchat store (module
    docstring). Belt: the env every one of them resolves through. Braces: the
    read boundaries themselves, stubbed so nothing can reach out at all."""
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "no-operator.env"))
    for var in ("GTD_TG_CHAT", "SKWATCHDOG_TG_CHATS", "GTD_MAIL_ACCOUNTS"):
        monkeypatch.delenv(var, raising=False)
    from skos.watchdog.adapters import chat_skchat as cs
    from skos.watchdog.adapters import chat_telegram as ct
    from skos.watchdog.adapters import email as em
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [])
    monkeypatch.setattr(cs, "_load_thread_meta", lambda: {})
    monkeypatch.setattr(ct, "configured_chats", lambda: [])
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: (
        _ for _ in ()).throw(AssertionError("the CLI test reached live Telegram")))
    monkeypatch.setattr(em, "_accounts", lambda: [])
    monkeypatch.setattr(em, "_search_thread_ids", lambda account, query, maxn: (
        _ for _ in ()).throw(AssertionError("the CLI test reached a live mailbox")))


def test_dry_run_prints_markdown_and_writes_nothing():
    res = runner.invoke(app, ["watchdog", "digest", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "skwatchdog digest" in res.output
    assert "--dry-run" in res.output
    assert not latest_dir().joinpath("digest.json").exists()


def test_real_run_publishes_and_reports():
    res = runner.invoke(app, ["watchdog", "digest", "--date", "2026-08-10"])
    assert res.exit_code == 0, res.output
    assert "published:" in res.output
    assert latest_dir().joinpath("digest.json").exists()
    # every registered Phase-1 source has an advanced cursor after a landed digest
    assert read_cursor("fleet") is not None


def test_the_private_chat_and_mail_sources_run_in_a_real_digest_and_stay_quiet():
    """WD-6's three sources are on the real registry and take part in a real
    digest run. With nothing configured and nothing to read they contribute
    no events and still report ok: an unconfigured box is not a broken box,
    and a broken box would have to say so out loud."""
    import json
    res = runner.invoke(app, ["watchdog", "digest", "--date", "2026-08-10"])
    assert res.exit_code == 0, res.output
    per_source = json.loads(latest_dir().joinpath("digest.json").read_text())["per_source"]
    for name in ("chat.skchat", "chat.telegram", "email"):
        assert per_source[name]["ok"] is True, name
        assert per_source[name]["events"] == 0, name


def test_the_network_reaching_sites_source_runs_in_a_real_digest_and_stays_quiet():
    """WD-12's `sites` is on the real registry and takes part in a real digest
    run here too, unconfigured and therefore silent.

    This assertion is what makes `_isolate_network_sources` LOUD. `_open` is
    stubbed to raise, but `port.collect_safe` is deliberately fail-safe: it
    folds ANY exception out of an adapter into a single SourceUnavailable line
    rather than letting it take the digest down. So a leak back to the network
    would not fail a test that only checks the CLI's exit code -- it would
    publish a degraded digest and pass. Reading `ok` for this source straight
    out of the published digest.json is what turns that silent degrade into a
    red test."""
    import json
    res = runner.invoke(app, ["watchdog", "digest", "--date", "2026-08-10"])
    assert res.exit_code == 0, res.output
    per_source = json.loads(latest_dir().joinpath("digest.json").read_text())["per_source"]
    assert per_source["sites"]["ok"] is True
    assert per_source["sites"]["events"] == 0


def test_a_stopped_tier_a_service_reaches_a_human_end_to_end(tmp_path, monkeypatch):
    """Card 04ad64d7's own acceptance criterion: stop a Tier A service on
    purpose and confirm a human is actually told.

    The service is stopped AT THE SYSTEMD BOUNDARY (the `_fake_systemd_units`
    view above says `skgateway.service` is enabled and inactive) rather than
    by stopping a live one, and everything downstream of that boundary is the
    real thing: the real adapter registry, the real digest assembly, the real
    renderer, the real publish step, the real GTD sink and the real delivery
    formatting. Each link is asserted individually, because the alarm silently
    no-opping is exactly the failure this card exists to fix, and a chain is
    only proven where it was actually pulled.

    Asserted the whole way down:
        systemd state -> WatchdogEvent -> digest.json -> published Markdown
        -> the DM body handed to the delivery path -> a tracked GTD item

    The one link this cannot exercise is the last hop off this box: the real
    `hermes send` subprocess, and Telegram behind it. That is stubbed here on
    purpose (the suite must not message Chef), so what this test proves is
    that a correct, complete, deep-linked message is HANDED to the delivery
    path, not that Telegram delivered it.
    """
    import json
    from skos.watchdog import deliver as dl

    monkeypatch.setenv("SKWATCHDOG_GTD", "1")
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    sent = []
    monkeypatch.setattr(dl, "default_sender", lambda text: sent.append(text) or True)

    res = runner.invoke(app, ["watchdog", "digest", "--date", "2026-08-16"])
    assert res.exit_code == 0, res.output

    # 1. the source read OK and produced the finding (not a degraded source:
    #    collect_safe would fold a broken adapter into a quiet notable line,
    #    so checking `ok` is what stops a silent degrade from passing here)
    published = json.loads(latest_dir().joinpath("digest.json").read_text())
    assert published["per_source"]["systemd_tier_a"]["ok"] is True

    # 2. it is a PROBLEM in the digest, with its deep link intact
    problems = [p for p in published["problems"] if p["source"] == "systemd_tier_a"]
    assert len(problems) == 1, published["problems"]
    finding = problems[0]
    assert finding["kind"] == "TierAUnitDown"
    assert STOPPED_TIER_A_UNIT in finding["summary"]
    assert finding["link"]["uri"].endswith(f"/systemd/user/{STOPPED_TIER_A_UNIT}")

    # 3. the deliberately-disabled Tier A unit is nowhere in any of it
    blob = json.dumps(published)
    assert "shadowcopy-monitor" not in blob

    # 4. it is in the published Markdown a human reads
    md = latest_dir().joinpath("digest.md").read_text()
    assert STOPPED_TIER_A_UNIT in md

    # 5. it is in the DM body actually handed to the delivery path
    assert len(sent) == 1, "no DM was handed to the delivery path at all"
    assert STOPPED_TIER_A_UNIT in sent[0]
    assert "Problems" in sent[0]

    # 6. and it became tracked work in the unified GTD, keyed on the finding's
    #    own coordinates rather than on today's date
    from skos.gtd_ingest import gtd_dir
    items = json.loads((gtd_dir() / "next-actions.json").read_text())
    refs = [i.get("source_ref") for i in items]
    assert f"systemd_tier_a:TierAUnitDown:{STOPPED_TIER_A_UNIT}@user" in refs, refs


def test_no_send_skips_dm(monkeypatch):
    from skos.watchdog import deliver as dl
    calls = []
    monkeypatch.setattr(dl, "default_sender", lambda text: calls.append(text) or True)
    res = runner.invoke(app, ["watchdog", "digest", "--no-send"])
    assert res.exit_code == 0, res.output
    assert "skipped" in res.output
    assert calls == []
