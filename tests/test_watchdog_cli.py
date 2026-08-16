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
"""
from typer.testing import CliRunner

from skos.cli import app
from skos.watchdog.cursor import read_cursor
from skos.watchdog.publish import latest_dir

runner = CliRunner()


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "skcapstone"))
    monkeypatch.setenv("SKFLEET_ROOT", str(tmp_path / "skcapstone" / "fleet"))
    monkeypatch.delenv("SK_WATCHDOG_GIT_REPOS", raising=False)
    from skos.watchdog import headline as hl
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    from skos.watchdog import deliver as dl
    monkeypatch.setattr(dl, "default_sender", lambda text: True)
    _isolate_private_sources(tmp_path, monkeypatch)


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


def test_dry_run_prints_markdown_and_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    res = runner.invoke(app, ["watchdog", "digest", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "skwatchdog digest" in res.output
    assert "--dry-run" in res.output
    assert not latest_dir().joinpath("digest.json").exists()


def test_real_run_publishes_and_reports(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    res = runner.invoke(app, ["watchdog", "digest", "--date", "2026-08-10"])
    assert res.exit_code == 0, res.output
    assert "published:" in res.output
    assert latest_dir().joinpath("digest.json").exists()
    # every registered Phase-1 source has an advanced cursor after a landed digest
    assert read_cursor("fleet") is not None


def test_the_private_chat_and_mail_sources_run_in_a_real_digest_and_stay_quiet(
        tmp_path, monkeypatch):
    """WD-6's three sources are on the real registry and take part in a real
    digest run. With nothing configured and nothing to read they contribute
    no events and still report ok: an unconfigured box is not a broken box,
    and a broken box would have to say so out loud."""
    import json
    _isolate(tmp_path, monkeypatch)
    res = runner.invoke(app, ["watchdog", "digest", "--date", "2026-08-10"])
    assert res.exit_code == 0, res.output
    per_source = json.loads(latest_dir().joinpath("digest.json").read_text())["per_source"]
    for name in ("chat.skchat", "chat.telegram", "email"):
        assert per_source[name]["ok"] is True, name
        assert per_source[name]["events"] == 0, name


def test_no_send_skips_dm(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from skos.watchdog import deliver as dl
    calls = []
    monkeypatch.setattr(dl, "default_sender", lambda text: calls.append(text) or True)
    res = runner.invoke(app, ["watchdog", "digest", "--no-send"])
    assert res.exit_code == 0, res.output
    assert "skipped" in res.output
    assert calls == []
