"""The WD-7 grader: reuses the autocode grader's DISCIPLINE, not its code.

Spec section 7: "reuse the autocode grader PATTERN, not its code. The
autocode grader scores a diff against acceptance criteria inside a sandbox;
conversations are a different artifact. What transfers is the discipline: an
independent pass (the grader never sees the generator's chain of thought), a
1-to-5 integer scale, a required verdict token so a chatty reply cannot be
misparsed as a score, and a deterministic threshold." The reference shape
lives at `skharness/src/skharness/autocode/adapters/base.py::grade()`
(`GradeBrief` in, `GateResult` out, a strict-JSON reply required, a literal
`<promise>COMPLETE</promise>` token gating anything past "the model claims a
5"); its diff-scoring content does not apply here, so nothing is imported
from it. The four disciplines this module actually copies:

  1. INDEPENDENT PASS. `grade_one` sees only `question` + `reply`: nothing
     about how the reply was composed, no chain of thought, no prior turns
     beyond the one line of context needed to judge relevance. Mirrors the
     autocode grader's own "fresh, no shared context with run_task" grade
     call (skharness engineering.py).
  2. 1-TO-5 INTEGER SCALE. Every dimension AND the overall score must be an
     integer 1..5; anything else (a float, a string, an out-of-range value)
     fails to parse.
  3. A REQUIRED VERDICT TOKEN. The reply must include a literal "PASS" or
     "FAIL" `verdict` field. This is not decoration: only a reply
     disciplined enough to produce that exact token is trusted to have
     produced real scores at all, exactly as the autocode grader only
     trusts a `<promise>COMPLETE</promise>` token it required explicitly,
     never a score scraped from free prose.
  4. A DETERMINISTIC THRESHOLD. The returned `verdict` is never the model's
     own token taken on faith -- it is recomputed in code from
     `rubric.threshold` / `rubric.floor` against the PARSED scores, so a
     model that claims PASS while actually scoring below threshold cannot
     smuggle a pass through. The token is required anyway as the parse
     gate described in (3); the pass/fail DECISION is always ours.

Never fabricates. `grade_one` never raises and never invents a score: any
failure (skgateway unreachable, timeout, malformed reply, missing/invalid
verdict token, an out-of-range or missing dimension score) returns
`GradeResult(graded=False, skip_reason=...)`. A missing grade is fine; an
invented one is a lie in a document Chef trusts (card hard rule).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from .render import strip_banned_dashes
from .rubric import Rubric

#: Endpoint/model, env-driven exactly like headline.py / gtd_triage.py /
#: adapters/order.py -- NEVER a hardcoded concrete model.
SKGATEWAY_URL = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")
SKGATEWAY_MODEL = os.environ.get("SKGATEWAY_MODEL", "sk-default")  # auto-router -> ornith

#: Wall-clock budget for ONE grade call. Short on purpose: callers that
#: grade many replies in one run (the grading adapter) also carry their own
#: total-run budget on top of this, so a hung skgateway (inc-4b9f8e5e:
#: ornith answers /v1/models but can hang on /v1/chat/completions) can never
#: turn "grading is skipped" into "the digest itself is late".
DEFAULT_TIMEOUT_S = 20.0

_VALID_VERDICT_TOKENS = ("PASS", "FAIL")


class SkipReason:
    """The only two ways a grade is skipped -- never a third, silent one."""
    GATEWAY_UNREACHABLE = "gateway_unreachable"
    UNPARSEABLE_REPLY = "unparseable_reply"


@dataclass
class GradeResult:
    """The grade for ONE subject against ONE rubric.

    `graded=False` means "skip this, no score exists" -- `scores` and
    `overall` are then always empty/None, never a guessed value. `verdict`
    is `"pass"` / `"fail"` only when `graded` is True; empty string
    otherwise. `rubric_ref` (`"<id>@v<version>"`) is always set, even on a
    skip, so a caller can still report WHICH rubric the (failed) attempt was
    against.
    """
    graded: bool
    rubric_ref: str
    subject_ref: str
    scores: dict = field(default_factory=dict)
    overall: Optional[int] = None
    verdict: str = ""
    notes: str = ""
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "graded": self.graded, "rubric_ref": self.rubric_ref,
            "subject_ref": self.subject_ref, "scores": dict(self.scores),
            "overall": self.overall, "verdict": self.verdict,
            "notes": self.notes, "skip_reason": self.skip_reason,
        }


def build_grade_prompt(rubric: Rubric, *, question: str, reply: str) -> str:
    """Compose the grading prompt. Pure and deterministic; the model sees
    exactly `question` + `reply` and the rubric's own dimension prompts --
    the "independent pass" boundary (module docstring, discipline 1)."""
    dims_text = "\n".join(f'- "{d.key}": {d.prompt.strip()}' for d in rubric.dimensions)
    keys_example = ", ".join(f'"{k}": N' for k in rubric.dimension_keys())
    parts = [
        rubric.instructions.strip() or "You are an independent grader.",
        "",
        f"What it was replying to (context, may be empty):\n{question or '(no context captured)'}",
        "",
        f"The reply being graded:\n{reply}",
        "",
        "Score each dimension 1 to 5, integers only:",
        dims_text,
        "",
        "Reply with STRICT JSON ONLY, no prose outside the JSON object, no "
        "markdown code fences, using exactly this shape: "
        f'{{"scores": {{{keys_example}}}, "overall": N, "verdict": "PASS" '
        'or "FAIL", "notes": "one short sentence in your own words, no '
        'quoting the reply verbatim"}. '
        "The verdict field must be exactly the literal string PASS or FAIL, "
        "uppercase, nothing else added. Never use em dashes or en dashes "
        "anywhere in notes; use commas or periods instead.",
    ]
    return "\n".join(parts)


def _chat_completion(prompt: str, *, timeout: float = DEFAULT_TIMEOUT_S,
                      url: str = SKGATEWAY_URL, model: str = SKGATEWAY_MODEL) -> Optional[str]:
    """One plain-text chat call to skgateway. Returns the reply text, or
    None on ANY failure (timeout, non-zero curl exit, unparseable response,
    empty content) -- never raises. Its own boundary function, deliberately
    NOT shared with headline.py's `_chat_completion`, so a test can
    monkeypatch grading behavior without also touching the headline
    renderer's seam (they happen to look alike; they are independent call
    sites by design, matching gtd_triage._chat_json / adapters.order's own
    per-module convention)."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.0,
    })
    try:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", str(timeout), f"{url}/chat/completions",
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=timeout + 10)
        content = json.loads(r.stdout)["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        return content or None
    except Exception:
        return None


def _extract_json_object(text: str) -> Optional[dict]:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_grade_reply(text: str, rubric: Rubric) -> Optional[dict]:
    """Strict parse (module docstring, disciplines 2 + 3): EVERY dimension
    key present as an in-range integer, an in-range integer overall, and the
    literal verdict token PASS or FAIL. Anything else -- a missing field, a
    float or out-of-range score, free prose instead of the token, extra
    chatty text with no valid JSON object anywhere in it -- returns None.
    The caller treats None exactly like a down gateway: skip, never guess.
    """
    data = _extract_json_object(text)
    if data is None:
        return None

    scores_raw = data.get("scores")
    if not isinstance(scores_raw, dict):
        return None
    scores: dict[str, int] = {}
    for key in rubric.dimension_keys():
        v = scores_raw.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 5):
            return None
        scores[key] = v

    overall = data.get("overall")
    if isinstance(overall, bool) or not isinstance(overall, int) or not (1 <= overall <= 5):
        return None

    verdict_token = data.get("verdict")
    if verdict_token not in _VALID_VERDICT_TOKENS:
        return None

    notes = str(data.get("notes") or "")[:280]
    return {"scores": scores, "overall": overall, "verdict_token": verdict_token, "notes": notes}


def grade_one(rubric: Rubric, subject_ref: str, *, question: str, reply: str,
              timeout: float = DEFAULT_TIMEOUT_S,
              url: str = SKGATEWAY_URL, model: str = SKGATEWAY_MODEL) -> GradeResult:
    """Grade one subject against `rubric`. See module docstring for the
    four disciplines this enforces. Never raises, never fabricates."""
    prompt = build_grade_prompt(rubric, question=question, reply=reply)
    text = _chat_completion(prompt, timeout=timeout, url=url, model=model)
    if not text:
        return GradeResult(graded=False, rubric_ref=rubric.rubric_ref,
                           subject_ref=subject_ref, skip_reason=SkipReason.GATEWAY_UNREACHABLE)

    parsed = _parse_grade_reply(text, rubric)
    if parsed is None:
        return GradeResult(graded=False, rubric_ref=rubric.rubric_ref,
                           subject_ref=subject_ref, skip_reason=SkipReason.UNPARSEABLE_REPLY)

    # Discipline 4: the verdict is ALWAYS recomputed from the parsed scores
    # against the rubric's own threshold/floor, never trusted verbatim from
    # the model's own PASS/FAIL token (which only gated whether we trust the
    # scores enough to parse them at all, see _parse_grade_reply).
    passes = parsed["overall"] >= rubric.threshold and min(parsed["scores"].values()) >= rubric.floor
    return GradeResult(
        graded=True, rubric_ref=rubric.rubric_ref, subject_ref=subject_ref,
        scores=parsed["scores"], overall=parsed["overall"],
        verdict="pass" if passes else "fail",
        notes=strip_banned_dashes(parsed["notes"]), skip_reason="")


__all__ = [
    "GradeResult", "SkipReason", "build_grade_prompt", "grade_one",
    "DEFAULT_TIMEOUT_S", "SKGATEWAY_URL", "SKGATEWAY_MODEL",
]
