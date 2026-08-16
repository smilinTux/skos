"""Tests for the WD-6 chat.telegram adapter (skos.watchdog.adapters.chat_telegram).

NOTHING HERE READS A LIVE TELEGRAM WINDOW (card hard rule). The one real read
boundary, `_poll`, is monkeypatched in every test by an autouse fixture, no
test resolves a real chat id (the operator env file is pointed at a
non-existent path), and `test_no_test_shells_out_to_skcapstone` proves the
subprocess is never spawned.
"""
from __future__ import annotations

import pytest

from skos.watchdog.adapters import chat_telegram as ct
from skos.watchdog.adapters.chat_telegram import ChatTelegramAdapter
from skos.watchdog.port import Window, collect_safe


#: The real read boundary, captured before the autouse fixture replaces it.
#: The one test that exercises the boundary itself calls this and stubs
#: `subprocess.run`, so `skcapstone telegram poll` is still never spawned.
_REAL_POLL = ct._poll


def _window(since="2026-08-09T22:00:00Z", until="2026-08-10T12:00:00Z"):
    return Window(since=since, until=until)


def _table(*rows):
    """`skcapstone telegram poll`'s rich table: ['', ID, Date, Sender, Text, '']."""
    return "".join(f"│ {mid} │ 2026-08-10 │ {sender} │ {text} │\n"
                   for mid, sender, text in rows)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    # Never resolve a real chat id from the process env or the operator env
    # file, and never spawn the real poll.
    monkeypatch.delenv("GTD_TG_CHAT", raising=False)
    monkeypatch.delenv("SKWATCHDOG_TG_CHATS", raising=False)
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "does-not-exist.env"))
    monkeypatch.setattr(ct, "OPERATOR_TG_NAME", "Chef")
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: (
        _ for _ in ()).throw(AssertionError("a test reached the live poll")))
    yield


# ----------------------------------------------------------- configuration --

def test_no_configured_chat_is_a_quiet_empty_run():
    assert ChatTelegramAdapter().collect(_window()) == []


def test_multiple_chats_come_from_skwatchdog_tg_chats(monkeypatch):
    monkeypatch.setenv("SKWATCHDOG_TG_CHATS", " a , b ")
    assert ct.configured_chats() == ["a", "b"]


def test_the_gtd_capture_chat_is_the_single_chat_fallback(monkeypatch):
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    assert ct.configured_chats() == ["chef-dm"]


# ------------------------------------------------------------- narration ----

def test_chat_activity_is_info_when_the_operator_spoke_last(monkeypatch):
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: _table(
        ("1", "Jaime", "are we still on for friday"),
        ("2", "Chef", "yes"),
    ))
    events = ChatTelegramAdapter().collect(_window())
    assert len(events) == 1
    ev = events[0]
    assert ev.source == "chat.telegram"
    assert ev.kind == "ChatActivity"
    assert ev.severity == "info"
    assert ev.object == "chef-dm"
    assert "2 new Telegram message(s) in chef-dm" in ev.summary
    assert "you spoke last" in ev.summary
    assert ev.link.uri == "skworld://skcomms/telegram/chef-dm"
    assert ev.ref == "chat.telegram:chef-dm:2026-08-10"


def test_a_chat_awaiting_the_operator_is_notable(monkeypatch):
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: _table(
        ("1", "Chef", "on my way"),
        ("2", "Jaime", "call me when you land"),
    ))
    ev = ChatTelegramAdapter().collect(_window())[0]
    assert ev.severity == "notable"
    assert ev.meta["awaiting_operator"] is True
    assert "waiting on you" in ev.summary
    assert "from Jaime" in ev.summary


def test_nothing_from_telegram_is_ever_a_problem(monkeypatch):
    """A `problem` files a GTD item (WD-8) and can escalate to a card (WD-9);
    no volume of unanswered Telegram is a fault."""
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: _table(
        *[(str(i), "Jaime", f"msg {i}") for i in range(30)]))
    events = ChatTelegramAdapter().collect(_window())
    assert [e.severity for e in events] == ["notable"]


# --------------------------------------------------------------- privacy ----

def test_message_text_is_dropped_at_the_read_boundary(monkeypatch):
    secret = "the thing chef would not want published"
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: _table(
        ("1", "Jaime", secret)))
    rows = ct._window_rows("chef-dm", "2026-08-09", 10)
    assert rows == [{"msg_id": "1", "sender": "Jaime"}]
    assert secret not in str(rows)


def test_no_message_text_reaches_a_published_event(monkeypatch):
    secret = "the thing chef would not want published"
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: _table(
        ("1", "Jaime", secret), ("2", "Chef", "understood")))
    events = ChatTelegramAdapter().collect(_window())
    dumped = str([e.to_dict() for e in events])
    assert secret not in dumped
    assert "understood" not in dumped


# -------------------------------------------------------------- read path ---

def test_the_poll_is_read_only_and_windowed_by_day(monkeypatch):
    """`--since` is day granularity, so the window read is a deliberate
    over-read at the day boundary, never an under-read."""
    seen = {}

    class _R:
        stdout = ""

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        return _R()

    monkeypatch.setattr(ct.subprocess, "run", _fake_run)
    monkeypatch.setattr(ct, "SKCAP_BIN", "skcapstone")
    _REAL_POLL("chef-dm", "2026-08-09", 100)
    assert seen["cmd"] == ["skcapstone", "telegram", "poll", "chef-dm",
                           "--limit", "100", "--since", "2026-08-09"]
    assert seen["kw"]["check"] is True
    # read-only: poll only, no send/read/delete verb anywhere in the command
    assert not ({"send", "read", "delete", "mark"} & set(seen["cmd"]))


def test_collect_passes_the_window_start_day_to_the_poll(monkeypatch):
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    seen = {}

    def _poll(chat, since_day, limit):
        seen["since_day"] = since_day
        return ""

    monkeypatch.setattr(ct, "_poll", _poll)
    ChatTelegramAdapter().collect(_window())
    assert seen["since_day"] == "2026-08-09"


# ------------------------------------------------------------- fail safe ----

def test_one_failing_chat_gaps_alone_and_never_blocks_the_other(monkeypatch):
    monkeypatch.setenv("SKWATCHDOG_TG_CHATS", "good,bad")

    def _poll(chat, since_day, limit):
        if chat == "bad":
            raise RuntimeError("skcapstone binary not found")
        return _table(("1", "Jaime", "hello"))

    monkeypatch.setattr(ct, "_poll", _poll)
    events = ChatTelegramAdapter().collect(_window())
    gaps = [e for e in events if e.kind == "SourceUnavailable"]
    activity = [e for e in events if e.kind == "ChatActivity"]
    assert len(gaps) == 1
    assert gaps[0].source == "chat.telegram:bad"
    assert gaps[0].severity == "notable"
    assert len(activity) == 1


def test_every_chat_failing_marks_the_whole_source_unavailable(monkeypatch):
    """Never a healthy source that happened to read nothing: a total failure
    has to reach collect_safe so per_source["chat.telegram"].ok goes false."""
    monkeypatch.setenv("SKWATCHDOG_TG_CHATS", "a,b")

    def _boom(chat, since_day, limit):
        raise RuntimeError("telegram unreachable")

    monkeypatch.setattr(ct, "_poll", _boom)
    events = collect_safe(ChatTelegramAdapter(), _window())
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"
    assert events[0].source == "chat.telegram"


def test_no_test_shells_out_to_skcapstone(monkeypatch):
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")

    def _no_subprocess(*a, **kw):
        raise AssertionError("a test spawned a real subprocess")

    monkeypatch.setattr(ct.subprocess, "run", _no_subprocess)
    monkeypatch.setattr(ct, "_poll", lambda chat, since_day, limit: "")
    assert ChatTelegramAdapter().collect(_window()) == []


# -------------------------------------------------------------- registry ----

def test_chat_telegram_is_registered_on_the_watchdog_source_port():
    from skos.watchdog.port import registry
    assert registry.lookup("watchdog-source", "chat.telegram") is ChatTelegramAdapter


def test_load_all_registers_chat_telegram():
    from skos.watchdog.adapters import load_all
    assert "chat.telegram" in load_all()
