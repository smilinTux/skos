"""Tests for the WD-6 chat.skchat adapter (skos.watchdog.adapters.chat_skchat).

NOTHING HERE READS A LIVE SKCHAT STORE (card hard rule). The module's two
read boundaries, `_load_messages` and `_load_thread_meta`, are monkeypatched
in every test by an autouse fixture whose default raises if the real one is
somehow reached, and `test_no_test_touches_a_live_skchat_store` proves the
lazy `skchat` import is never taken. The one test that exercises the real
`_load_messages` body injects a fake `skchat.history` module into
`sys.modules` instead, so even that path never opens a store.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from skos.watchdog.adapters import chat_skchat as cs
from skos.watchdog.adapters.chat_skchat import ChatSkchatAdapter
from skos.watchdog.port import Window, collect_safe

OPERATOR = "capauth:chef@skworld.io"
OTHER = "capauth:lumina@skworld.io"

#: The real read boundary, captured before the autouse fixture replaces it.
#: The one test that exercises the boundary itself calls this against a FAKE
#: `skchat.history` module, so no store is ever opened.
_REAL_LOAD_MESSAGES = cs._load_messages


def _window(since="2026-08-10T00:00:00Z", until="2026-08-10T12:00:00Z"):
    return Window(since=since, until=until)


def _msg(mid, sender, ts, thread_id="thread-aaaabbbb", delivery_status="delivered"):
    return {"id": mid, "sender": sender, "thread_id": thread_id,
            "timestamp": datetime.fromisoformat(ts.replace("Z", "+00:00")),
            "delivery_status": delivery_status}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Both read boundaries are stubbed for every test. A test that wants
    real-looking data replaces `_load_messages`; nothing ever reaches skchat."""
    monkeypatch.setattr(cs, "OPERATOR_ID", OPERATOR)
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [])
    monkeypatch.setattr(cs, "_load_thread_meta", lambda: {})
    yield


# ------------------------------------------------------- thread activity ----

def test_thread_activity_is_info_when_the_operator_spoke_last(monkeypatch):
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg("m1", OTHER, "2026-08-10T06:00:00Z"),
        _msg("m2", OPERATOR, "2026-08-10T06:01:00Z"),
    ])
    events = ChatSkchatAdapter().collect(_window())
    assert len(events) == 1
    ev = events[0]
    assert ev.source == "chat.skchat"
    assert ev.kind == "ThreadActivity"
    assert ev.severity == "info"
    assert ev.object == "thread-aaaabbbb"
    assert "2 new skchat message(s)" in ev.summary
    assert "you spoke last" in ev.summary
    assert ev.link.uri == "skworld://skchat/thread/thread-aaaabbbb"
    assert ev.ref == "chat.skchat:thread-aaaabbbb:2026-08-10"


def test_thread_awaiting_the_operator_is_notable(monkeypatch):
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg("m1", OPERATOR, "2026-08-10T06:00:00Z"),
        _msg("m2", OTHER, "2026-08-10T06:01:00Z"),
    ])
    ev = ChatSkchatAdapter().collect(_window())[0]
    assert ev.severity == "notable"
    assert ev.meta["awaiting_operator"] is True
    assert "waiting on you" in ev.summary
    assert "from lumina" in ev.summary  # identity-level, short local part


def test_several_senders_collapse_to_a_count_not_a_list(monkeypatch):
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg("m1", "capauth:a@skworld.io", "2026-08-10T06:00:00Z"),
        _msg("m2", "capauth:b@skworld.io", "2026-08-10T06:01:00Z"),
    ])
    ev = ChatSkchatAdapter().collect(_window())[0]
    assert "2 people" in ev.summary


def test_messages_outside_the_window_are_not_counted(monkeypatch):
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg("m1", OTHER, "2020-01-01T00:00:00Z"),
        _msg("m2", OTHER, "2030-01-01T00:00:00Z"),
    ])
    assert ChatSkchatAdapter().collect(_window()) == []


def test_threads_are_itemized_up_to_the_cap_then_rolled_up(monkeypatch):
    monkeypatch.setattr(cs, "MAX_THREADS_REPORTED", 2)
    rows = [_msg(f"m{i}", OPERATOR, f"2026-08-10T06:0{i}:00Z", thread_id=f"t{i}")
            for i in range(5)]
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: rows)
    events = ChatSkchatAdapter().collect(_window())
    itemized = [e for e in events if e.kind == "ThreadActivity"]
    rollup = [e for e in events if e.kind == "ThreadActivityRollup"]
    assert len(itemized) == 2
    assert len(rollup) == 1
    assert rollup[0].severity == "info"
    assert rollup[0].meta == {"threads": 3, "messages": 3}


# ------------------------------------------------------ severity discipline --

def test_failed_delivery_is_the_only_problem_this_adapter_raises(monkeypatch):
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg("m1", OPERATOR, "2026-08-10T06:00:00Z", delivery_status="failed"),
        _msg("m2", OTHER, "2026-08-10T06:01:00Z"),
    ])
    events = ChatSkchatAdapter().collect(_window())
    problems = [e for e in events if e.severity == "problem"]
    assert len(problems) == 1
    assert problems[0].kind == "MessageDeliveryFailed"
    assert problems[0].meta["failed"] == 1
    assert "delivery status failed" in problems[0].summary


def test_an_unanswered_thread_is_never_a_problem(monkeypatch):
    """"Chef has not replied yet" is a workload, never tracked work: a
    `problem` files a GTD item (WD-8) and can escalate to a card (WD-9)."""
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg(f"m{i}", OTHER, f"2026-08-10T06:0{i}:00Z") for i in range(9)
    ])
    events = ChatSkchatAdapter().collect(_window())
    assert [e.severity for e in events] == ["notable"]


# --------------------------------------------------------------- privacy ----

def test_no_message_body_can_reach_an_event_because_none_is_ever_loaded(monkeypatch):
    """The structural guarantee: `_load_messages` projects a message down to
    id/sender/thread/timestamp/status, so `content` never enters this module.
    A fake `skchat.history` stands in for the real library; no store opens."""
    secret = "the private thing chef said"

    class _FakeMessage:
        id = "m1"
        sender = OTHER
        thread_id = "thread-aaaabbbb"
        timestamp = datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc)
        delivery_status = "delivered"
        content = secret

    class _FakeHistory:
        def load(self, since, limit):
            return [_FakeMessage()]

    fake = types.ModuleType("skchat.history")
    fake.ChatHistory = _FakeHistory
    monkeypatch.setitem(sys.modules, "skchat", types.ModuleType("skchat"))
    monkeypatch.setitem(sys.modules, "skchat.history", fake)

    rows = _REAL_LOAD_MESSAGES(datetime(2026, 8, 10, tzinfo=timezone.utc), 10)
    assert rows == [{"id": "m1", "sender": OTHER, "thread_id": "thread-aaaabbbb",
                     "timestamp": _FakeMessage.timestamp,
                     "delivery_status": "delivered"}]
    assert "content" not in rows[0]
    assert secret not in str(rows)

    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: rows)
    events = ChatSkchatAdapter().collect(_window())
    assert secret not in str([e.to_dict() for e in events])


def test_thread_titles_never_reach_an_event(monkeypatch):
    """A thread title is user-authored prose about the conversation, so the
    same judgement that bans email subject lines bans it here. Only the
    thread id and its participants carry identity."""
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg("m1", OTHER, "2026-08-10T06:00:00Z"),
    ])
    monkeypatch.setattr(cs, "_load_thread_meta", lambda: {
        "thread-aaaabbbb": {"participants": [OPERATOR, OTHER],
                            "title": "the divorce paperwork"},
    })
    ev = ChatSkchatAdapter().collect(_window())[0]
    assert "divorce" not in str(ev.to_dict())
    assert ev.meta["participants"] == [OPERATOR, OTHER]


# ------------------------------------------------------------- fail safe ----

def test_thread_metadata_failure_is_best_effort_and_does_not_lose_the_source(monkeypatch):
    monkeypatch.setattr(cs, "_load_messages", lambda since_dt, limit: [
        _msg("m1", OTHER, "2026-08-10T06:00:00Z"),
    ])

    def _boom():
        raise RuntimeError("thread index unreadable")

    monkeypatch.setattr(cs, "_load_thread_meta", _boom)
    events = ChatSkchatAdapter().collect(_window())
    assert len(events) == 1
    assert events[0].kind == "ThreadActivity"
    assert events[0].meta["participants"] == []


def test_an_unreadable_store_degrades_to_one_source_unavailable_line(monkeypatch):
    def _boom(since_dt, limit):
        raise ModuleNotFoundError("No module named 'skchat'")

    monkeypatch.setattr(cs, "_load_messages", _boom)
    events = collect_safe(ChatSkchatAdapter(), _window())
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"
    assert events[0].source == "chat.skchat"
    assert events[0].severity == "notable"


def test_no_test_touches_a_live_skchat_store(monkeypatch):
    """Belt and braces: with the lazy import poisoned, a full collect still
    succeeds, proving the real skchat read path is never taken."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _guard(name, *a, **kw):
        assert not name.startswith("skchat"), "a test reached the live skchat library"
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _guard)
    assert ChatSkchatAdapter().collect(_window()) == []


# -------------------------------------------------------------- registry ----

def test_chat_skchat_is_registered_on_the_watchdog_source_port():
    from skos.watchdog.port import registry
    assert registry.lookup("watchdog-source", "chat.skchat") is ChatSkchatAdapter


def test_load_all_registers_chat_skchat():
    from skos.watchdog.adapters import load_all
    assert "chat.skchat" in load_all()
