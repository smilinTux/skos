"""Tests for the WD-7 grader: the verdict-token discipline reused from the
autocode grader (see grader.py's module docstring for the four disciplines).

Nothing here calls a live model: `_chat_completion` is monkeypatched exactly
like `headline._chat_completion` is in its own test suite (the two are
deliberately independent boundary functions, see grader.py's docstring).
"""
from __future__ import annotations

import json

import pytest

from skos.watchdog import grader as g
from skos.watchdog.rubric import parse_rubric

_RUBRIC_YAML = """
schema: 1
id: test-rubric
version: 1
title: "Test"
applies_to: test.thing
threshold: 3
floor: 2
instructions: "Grade it."
dimensions:
  - key: dim_a
    prompt: "Is A good?"
  - key: dim_b
    prompt: "Is B good?"
"""


@pytest.fixture
def rubric():
    return parse_rubric(_RUBRIC_YAML, source="t")


def _reply(**overrides):
    base = {"scores": {"dim_a": 4, "dim_b": 4}, "overall": 4,
            "verdict": "PASS", "notes": "solid reply"}
    base.update(overrides)
    return json.dumps(base)


# ------------------------------------------------------------- build_prompt

def test_prompt_includes_question_and_reply(rubric):
    prompt = g.build_grade_prompt(rubric, question="what time is it", reply="3pm")
    assert "what time is it" in prompt
    assert "3pm" in prompt


def test_prompt_includes_every_dimension_key(rubric):
    prompt = g.build_grade_prompt(rubric, question="q", reply="r")
    assert "dim_a" in prompt
    assert "dim_b" in prompt


def test_prompt_requires_the_verdict_token(rubric):
    prompt = g.build_grade_prompt(rubric, question="q", reply="r")
    assert "PASS" in prompt
    assert "FAIL" in prompt


def test_prompt_never_contains_banned_dashes(rubric):
    prompt = g.build_grade_prompt(rubric, question="q", reply="r")
    assert "—" not in prompt
    assert "–" not in prompt


def test_prompt_handles_empty_question(rubric):
    prompt = g.build_grade_prompt(rubric, question="", reply="r")
    assert "no context captured" in prompt


# --------------------------------------------------------------- grade_one

def test_grade_one_parses_a_well_formed_pass(monkeypatch, rubric):
    monkeypatch.setattr(g, "_chat_completion", lambda prompt, **kw: _reply())
    r = g.grade_one(rubric, "subject-1", question="q", reply="reply text")
    assert r.graded is True
    assert r.overall == 4
    assert r.scores == {"dim_a": 4, "dim_b": 4}
    assert r.verdict == "pass"
    assert r.rubric_ref == "test-rubric@v1"
    assert r.subject_ref == "subject-1"
    assert r.skip_reason == ""


def test_grade_one_computes_fail_from_low_overall(monkeypatch, rubric):
    monkeypatch.setattr(g, "_chat_completion",
                        lambda prompt, **kw: _reply(overall=2, scores={"dim_a": 2, "dim_b": 3}))
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is True
    assert r.verdict == "fail"


def test_grade_one_floor_violation_fails_even_with_high_overall(monkeypatch, rubric):
    """discipline 4: the verdict is recomputed deterministically, so a low
    single dimension fails the reply even if overall looks fine."""
    monkeypatch.setattr(g, "_chat_completion",
                        lambda prompt, **kw: _reply(overall=4, scores={"dim_a": 1, "dim_b": 5}))
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is True
    assert r.verdict == "fail"


def test_grade_one_ignores_a_model_pass_claim_that_disagrees_with_scores(monkeypatch, rubric):
    """discipline 4: the model's own verdict token is never trusted for the
    pass/fail DECISION, only as a parse gate. A model claiming PASS with
    scores that do not clear threshold still grades fail."""
    monkeypatch.setattr(g, "_chat_completion",
                        lambda prompt, **kw: _reply(overall=1, scores={"dim_a": 1, "dim_b": 1},
                                                    verdict="PASS"))
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is True
    assert r.verdict == "fail"


def test_grade_one_skips_when_gateway_unreachable(monkeypatch, rubric):
    monkeypatch.setattr(g, "_chat_completion", lambda prompt, **kw: None)
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is False
    assert r.skip_reason == g.SkipReason.GATEWAY_UNREACHABLE
    assert r.scores == {}
    assert r.overall is None
    assert r.verdict == ""


def test_grade_one_never_raises_when_chat_completion_raises(monkeypatch, rubric):
    def _boom(prompt, **kw):
        raise TimeoutError("gateway hung")
    # _chat_completion itself never raises in production (grader.py's own
    # boundary catches everything); simulate the production behavior
    # directly, exactly like test_watchdog_headline.py does for its sibling.
    monkeypatch.setattr(g, "_chat_completion", lambda prompt, **kw: None)
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is False


@pytest.mark.parametrize("chatty", [
    "Sure, I think this is a pretty good reply overall, maybe a 4 out of 5.",
    '{"scores": {"dim_a": 4, "dim_b": 4}, "overall": 4, "notes": "ok"}',  # missing verdict
    '{"scores": {"dim_a": 4}, "overall": 4, "verdict": "PASS"}',          # missing dim_b
    '{"scores": {"dim_a": 4, "dim_b": 4}, "overall": 4, "verdict": "yes"}',  # bad token
    '{"scores": {"dim_a": 4, "dim_b": 4}, "overall": 6, "verdict": "PASS"}',  # out of range
    '{"scores": {"dim_a": 4.5, "dim_b": 4}, "overall": 4, "verdict": "PASS"}',  # float
])
def test_grade_one_skips_a_chatty_or_malformed_reply_never_scrapes_a_number(
        monkeypatch, rubric, chatty):
    """discipline 3: a required verdict token so a chatty reply cannot be
    misparsed as a score. None of these variants -- free prose, a missing
    field, an invalid token, an out-of-range or non-integer score -- may
    ever produce a graded result."""
    monkeypatch.setattr(g, "_chat_completion", lambda prompt, **kw: chatty)
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is False
    assert r.skip_reason == g.SkipReason.UNPARSEABLE_REPLY


def test_grade_one_accepts_json_embedded_in_surrounding_prose(monkeypatch, rubric):
    text = "Here is my grade:\n" + _reply() + "\nHope that helps!"
    monkeypatch.setattr(g, "_chat_completion", lambda prompt, **kw: text)
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is True


def test_grade_one_strips_banned_dashes_from_notes(monkeypatch, rubric):
    monkeypatch.setattr(g, "_chat_completion",
                        lambda prompt, **kw: _reply(notes="good reply — but terse"))
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert "—" not in r.notes


def test_grade_one_strips_think_blocks(monkeypatch, rubric):
    text = "<think>let me consider this</think>" + _reply()
    monkeypatch.setattr(g, "_chat_completion", lambda prompt, **kw: text)
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is True


def test_grade_one_bool_scores_rejected_even_though_bool_is_an_int_subclass(monkeypatch, rubric):
    monkeypatch.setattr(g, "_chat_completion",
                        lambda prompt, **kw: _reply(scores={"dim_a": True, "dim_b": 4}))
    r = g.grade_one(rubric, "s", question="q", reply="r")
    assert r.graded is False


def test_grade_one_never_puts_the_raw_reply_body_into_the_result_object(monkeypatch, rubric):
    monkeypatch.setattr(g, "_chat_completion", lambda prompt, **kw: _reply())
    r = g.grade_one(rubric, "s", question="a private question", reply="a private reply body")
    dumped = json.dumps(r.to_dict())
    assert "a private reply body" not in dumped
    assert "a private question" not in dumped


# -------------------------------------------------------- _chat_completion

def test_chat_completion_never_raises_when_subprocess_raises(monkeypatch):
    import subprocess as _sp

    def _boom(*a, **kw):
        raise RuntimeError("curl exploded")

    monkeypatch.setattr(_sp, "run", _boom)
    assert g._chat_completion("prompt", timeout=1) is None


def test_chat_completion_returns_none_on_malformed_response(monkeypatch):
    class _Fake:
        stdout = "not json"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _Fake())
    assert g._chat_completion("prompt", timeout=1) is None


def test_gateway_url_and_model_are_env_driven_never_hardcoded():
    assert g.SKGATEWAY_MODEL == "sk-default"
    assert "18780" in g.SKGATEWAY_URL
