"""Tests for the WD-7 grading adapter (skos.watchdog.adapters.grading).

Nothing here reads a live message store or calls a live model (card hard
rule): `_load_skchat_messages` / `_load_telegram_rows` / `grader.grade_one`
are monkeypatched at their module boundary, exactly matching the pattern
already used by `test_watchdog_adapter_itil.py` (lazy sibling import) and
`test_watchdog_headline.py` (the model-call boundary).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from skos.watchdog import grader as grader_mod
from skos.watchdog.adapters import grading as gr
from skos.watchdog.adapters.grading import GradingAdapter
from skos.watchdog.port import Window, collect_safe
from skos.watchdog.rubric import Rubric, RubricDimension


def _window(since="2026-08-10T00:00:00Z", until="2026-08-10T12:00:00Z"):
    return Window(since=since, until=until)


def _skchat_row(msg_id, sender, content, ts, thread_id="thread-1"):
    return {"id": msg_id, "sender": sender, "content": content,
            "timestamp": datetime.fromisoformat(ts.replace("Z", "+00:00")),
            "thread_id": thread_id}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    # Never resolve a real chat id / real skcapstone binary from the
    # process env or the operator env file during tests.
    monkeypatch.delenv("GTD_TG_CHAT", raising=False)
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "does-not-exist.env"))
    # Empty both channels by default; individual tests monkeypatch one.
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [])
    monkeypatch.setattr(gr, "_load_telegram_rows",
                        lambda chat, limit: (_ for _ in ()).throw(AssertionError(
                            "telegram should not be read when no chat id is configured")))
    yield


@pytest.fixture
def rubric():
    return Rubric(
        id="lumina-replies", version=1, title="t", applies_to="lumina.replies",
        instructions="Grade it.", threshold=3, floor=2,
        dimensions=(
            RubricDimension(key="answered_the_question", prompt="p"),
            RubricDimension(key="factually_grounded", prompt="p"),
            RubricDimension(key="tone_matches_soul", prompt="p"),
            RubricDimension(key="no_banned_punctuation", prompt="p"),
            RubricDimension(key="action_captured_if_any", prompt="p"),
        ))


def _pass_result(subject_ref, rubric_ref="lumina-replies@v1"):
    return grader_mod.GradeResult(
        graded=True, rubric_ref=rubric_ref, subject_ref=subject_ref,
        scores={"answered_the_question": 5, "factually_grounded": 5,
                "tone_matches_soul": 5, "no_banned_punctuation": 5,
                "action_captured_if_any": 5},
        overall=5, verdict="pass", notes="good reply")


def _fail_result(subject_ref, rubric_ref="lumina-replies@v1"):
    return grader_mod.GradeResult(
        graded=True, rubric_ref=rubric_ref, subject_ref=subject_ref,
        scores={"answered_the_question": 1, "factually_grounded": 5,
                "tone_matches_soul": 5, "no_banned_punctuation": 5,
                "action_captured_if_any": 5},
        overall=3, verdict="fail", notes="dodged the question")


def _skip_result(subject_ref, reason=grader_mod.SkipReason.GATEWAY_UNREACHABLE):
    return grader_mod.GradeResult(graded=False, rubric_ref="lumina-replies@v1",
                                  subject_ref=subject_ref, skip_reason=reason)


# --------------------------------------------------------------- skchat ----

def test_skchat_reply_is_graded_and_emitted(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m2", "capauth:lumina@skworld.io", "here is my answer",
                    "2026-08-10T06:00:00Z"),
        _skchat_row("m1", "capauth:chef@skworld.io", "what is the status",
                    "2026-08-10T05:59:00Z"),
    ])
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: _pass_result(subject_ref))

    events = GradingAdapter().collect(_window())
    ours = [e for e in events if e.kind == "ReplyGraded"]
    assert len(ours) == 1
    ev = ours[0]
    assert ev.source == "grading"
    assert ev.severity == "info"  # a pass is quiet, folds into info_counts only
    assert ev.object == "skchat:thread-1:m2"
    assert ev.link.uri == "skworld://skchat/thread/thread-1"
    assert "here is my answer" not in ev.summary
    assert "here is my answer" not in str(ev.meta)


def test_skchat_reply_that_fails_grade_is_notable(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m1", "capauth:lumina@skworld.io", "a dodgy answer",
                    "2026-08-10T06:00:00Z"),
    ])
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: _fail_result(subject_ref))

    events = GradingAdapter().collect(_window())
    ours = [e for e in events if e.kind == "ReplyGraded"]
    assert len(ours) == 1
    assert ours[0].severity == "notable"
    assert "3/5" in ours[0].summary
    assert "fail" in ours[0].summary
    assert "answered_the_question" in ours[0].summary  # low-dimension called out


def test_messages_from_the_other_party_are_never_graded(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m1", "capauth:chef@skworld.io", "a question", "2026-08-10T06:00:00Z"),
    ])
    called = []
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda *a, **kw: called.append(1) or _pass_result("x"))
    GradingAdapter().collect(_window())
    assert called == []


def test_messages_outside_the_window_are_not_graded(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m1", "capauth:lumina@skworld.io", "old reply", "2020-01-01T00:00:00Z"),
    ])
    called = []
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda *a, **kw: called.append(1) or _pass_result("x"))
    GradingAdapter().collect(_window())
    assert called == []


def test_prior_message_from_other_party_becomes_question_context(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m2", "capauth:lumina@skworld.io", "the answer", "2026-08-10T06:01:00Z"),
        _skchat_row("m1", "capauth:chef@skworld.io", "the real question", "2026-08-10T06:00:00Z"),
    ])
    captured = {}

    def _fake_grade(rubric, subject_ref, *, question, reply, **kw):
        captured["question"] = question
        captured["reply"] = reply
        return _pass_result(subject_ref)

    monkeypatch.setattr(grader_mod, "grade_one", _fake_grade)
    GradingAdapter().collect(_window())
    assert captured["question"] == "the real question"
    assert captured["reply"] == "the answer"


def test_skchat_channel_caps_at_max_graded_per_channel(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "MAX_GRADED_PER_CHANNEL", 2)
    rows = [_skchat_row(f"m{i}", "capauth:lumina@skworld.io", f"reply {i}",
                        f"2026-08-10T06:0{i}:00Z") for i in range(5)]
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: list(reversed(rows)))
    calls = []
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: calls.append(subject_ref) or
                        _pass_result(subject_ref))
    GradingAdapter().collect(_window())
    assert len(calls) == 2


# ------------------------------------------------------------- telegram ----

def test_telegram_reply_is_graded_when_chat_is_configured(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    table = (
        "│ 1 │ date │ Chef │ what time is it │\n"
        "│ 2 │ date │ Lumina │ 3pm │\n"
    )
    monkeypatch.setattr(gr, "_load_telegram_rows", lambda chat, limit: gr._parse_telegram_table(table))
    captured = {}

    def _fake_grade(rubric, subject_ref, *, question, reply, **kw):
        captured["question"] = question
        captured["reply"] = reply
        return _pass_result(subject_ref)

    monkeypatch.setattr(grader_mod, "grade_one", _fake_grade)
    events = GradingAdapter().collect(_window())
    ours = [e for e in events if e.kind == "ReplyGraded" and e.meta["channel"] == "telegram"]
    assert len(ours) == 1
    assert captured["question"] == "what time is it"
    assert captured["reply"] == "3pm"
    assert "3pm" not in ours[0].summary


def test_telegram_is_skipped_quietly_when_no_chat_is_configured(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    # autouse fixture already leaves GTD_TG_CHAT unset and asserts
    # _load_telegram_rows is never called; a clean collect() proves it.
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [])
    events = GradingAdapter().collect(_window())
    assert events == []


# ---------------------------------------------------- per-channel gaps ----

def test_skchat_failure_does_not_block_telegram_grading(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")

    def _boom(since_dt, limit):
        raise RuntimeError("skchat store corrupt")

    monkeypatch.setattr(gr, "_load_skchat_messages", _boom)
    table = "│ 1 │ date │ Lumina │ hello │\n"
    monkeypatch.setattr(gr, "_load_telegram_rows", lambda chat, limit: gr._parse_telegram_table(table))
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: _pass_result(subject_ref))

    events = GradingAdapter().collect(_window())
    gaps = [e for e in events if e.kind == "SourceUnavailable"]
    graded = [e for e in events if e.kind == "ReplyGraded"]
    assert len(gaps) == 1
    assert gaps[0].source == "grading.skchat"
    assert len(graded) == 1  # telegram still graded


def test_telegram_failure_does_not_block_skchat_grading(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setenv("GTD_TG_CHAT", "chef-dm")
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m1", "capauth:lumina@skworld.io", "reply", "2026-08-10T06:00:00Z"),
    ])

    def _boom(chat, limit):
        raise RuntimeError("skcapstone binary not found")

    monkeypatch.setattr(gr, "_load_telegram_rows", _boom)
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: _pass_result(subject_ref))

    events = GradingAdapter().collect(_window())
    gaps = [e for e in events if e.kind == "SourceUnavailable"]
    graded = [e for e in events if e.kind == "ReplyGraded"]
    assert len(gaps) == 1
    assert gaps[0].source == "grading.telegram"
    assert len(graded) == 1


def test_missing_rubric_degrades_the_whole_source_via_collect_safe(monkeypatch):
    from skos.watchdog.rubric import RubricError

    def _boom(*a, **kw):
        raise RubricError("no such rubric")

    monkeypatch.setattr(gr, "load_rubric", _boom)
    events = collect_safe(GradingAdapter(), _window())
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"
    assert events[0].source == "grading"


# ------------------------------------------------- honest degrade, no fake

def test_skipped_grades_produce_a_gap_event_never_a_fabricated_score(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m1", "capauth:lumina@skworld.io", "reply", "2026-08-10T06:00:00Z"),
    ])
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: _skip_result(subject_ref))

    events = GradingAdapter().collect(_window())
    assert not [e for e in events if e.kind == "ReplyGraded"]
    gaps = [e for e in events if e.kind == "GradingGap"]
    assert len(gaps) == 1
    assert gaps[0].severity == "notable"
    assert gaps[0].meta["skipped"] == 1
    assert gaps[0].meta["budget_exhausted"] is False
    assert "no score was fabricated" in gaps[0].summary


def test_gap_event_never_reveals_which_specific_reply_failed_to_grade(monkeypatch, rubric):
    """Only a roll-up count, never the subject_ref of the skipped item, so
    a gap line cannot be used to reconstruct which private conversation
    failed to grade."""
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m1", "capauth:lumina@skworld.io", "reply", "2026-08-10T06:00:00Z"),
    ])
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: _skip_result(subject_ref))
    events = GradingAdapter().collect(_window())
    gap = [e for e in events if e.kind == "GradingGap"][0]
    assert "m1" not in gap.summary
    assert "m1" not in str(gap.meta)


def test_time_budget_exhaustion_skips_the_rest_without_blocking(monkeypatch, rubric):
    monkeypatch.setattr(gr, "load_rubric", lambda *a, **kw: rubric)
    monkeypatch.setattr(gr, "GRADE_RUN_BUDGET_S", 0.0)  # already exhausted before the first call
    monkeypatch.setattr(gr, "_load_skchat_messages", lambda since_dt, limit: [
        _skchat_row("m1", "capauth:lumina@skworld.io", "reply", "2026-08-10T06:00:00Z"),
    ])
    called = []
    monkeypatch.setattr(grader_mod, "grade_one",
                        lambda rubric, subject_ref, **kw: called.append(1) or _pass_result(subject_ref))

    events = GradingAdapter().collect(_window())
    assert called == []  # never even attempted once the budget is already spent
    gap = [e for e in events if e.kind == "GradingGap"][0]
    assert gap.meta["budget_exhausted"] is True
    assert "time budget" in gap.summary


# ----------------------------------------------------------- registry ----

def test_grading_is_registered_on_the_watchdog_source_port():
    from skos.watchdog.port import registry
    assert registry.lookup("watchdog-source", "grading") is GradingAdapter


def test_load_all_registers_grading():
    from skos.watchdog.adapters import load_all
    assert "grading" in load_all()
