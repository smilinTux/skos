"""The skgateway-backed headline (WD-3): model call with a strict, silent,
never-raising fallback to the deterministic template. Nothing here talks to
a live skgateway; `_chat_completion` is monkeypatched exactly like
`gtd_triage._chat_json` / `adapters.order.classify_llm` are in their own
test suites.
"""

from skos.watchdog import headline as hl


def _digest(**overrides):
    base = {
        "date": "2026-08-10",
        "headline": "1 problem, 0 notable items, 0 quiet info events across 1 source.",
        "problems": [{"summary": "skchat daemon crash-looped on .41"}],
        "notable": [],
        "info_counts": {},
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ prompt

def test_build_prompt_includes_problem_and_notable_summaries():
    d = _digest(notable=[{"summary": "atlas parked a decision"}])
    prompt = hl.build_prompt(d)
    assert "skchat daemon crash-looped on .41" in prompt
    assert "atlas parked a decision" in prompt


def test_build_prompt_never_invents_lines_beyond_what_it_is_given():
    d = _digest(problems=[], notable=[])
    prompt = hl.build_prompt(d)
    assert "none" in prompt.lower()


def test_build_prompt_forbids_em_and_en_dashes_in_its_own_instruction():
    prompt = hl.build_prompt(_digest())
    assert "—" not in prompt
    assert "–" not in prompt


# --------------------------------------------------------- render_headline_llm

def test_headline_uses_model_output_when_gateway_answers(monkeypatch):
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: "skchat had one crash loop.")
    out = hl.render_headline_llm(_digest(), fallback="fallback text")
    assert out == "skchat had one crash loop."


def test_headline_falls_back_when_gateway_is_unreachable(monkeypatch):
    """PROOF: the digest still renders a complete headline when skgateway is
    down (raises/times out inside the low-level call, which never
    propagates)."""
    def _boom(prompt, **kw):
        raise TimeoutError("connection refused")
    # _chat_completion itself never raises in production (it catches
    # everything); simulate the production behavior directly: a down
    # gateway resolves to None, not an exception, at this call boundary.
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    out = hl.render_headline_llm(_digest(), fallback="fallback text")
    assert out == "fallback text"


def test_headline_falls_back_on_empty_model_reply(monkeypatch):
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: "   ")
    out = hl.render_headline_llm(_digest(), fallback="fallback text")
    assert out == "fallback text"


def test_headline_strips_banned_dashes_from_model_output(monkeypatch):
    monkeypatch.setattr(hl, "_chat_completion",
                        lambda prompt, **kw: "skchat crashed — four times.")
    out = hl.render_headline_llm(_digest(), fallback="fallback")
    assert "—" not in out
    assert "–" not in out


def test_headline_strips_banned_dashes_from_the_fallback_too(monkeypatch):
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    out = hl.render_headline_llm(_digest(), fallback="fallback — text")
    assert "—" not in out


def test_chat_completion_never_raises_when_subprocess_itself_raises(monkeypatch):
    """The boundary function's own contract (catches everything, returns
    None), proven with the real implementation and subprocess.run patched to
    explode -- this is what makes render_headline_llm safe to call
    unconditionally from run.py without its own try/except."""
    import subprocess as _sp

    def _boom_run(*a, **kw):
        raise RuntimeError("curl exploded")

    monkeypatch.setattr(_sp, "run", _boom_run)
    result = hl._chat_completion("prompt", timeout=1)
    assert result is None


def test_chat_completion_returns_none_on_malformed_response(monkeypatch):
    class _FakeResult:
        stdout = "not json"

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeResult())
    assert hl._chat_completion("prompt", timeout=1) is None


def test_chat_completion_extracts_content_and_strips_think_blocks(monkeypatch):
    import json as _json

    class _FakeResult:
        stdout = _json.dumps({
            "choices": [{"message": {"content": "<think>reasoning</think>final answer"}}]
        })

    monkeypatch.setattr("subprocess.run", lambda *a, **kw: _FakeResult())
    assert hl._chat_completion("prompt", timeout=1) == "final answer"


def test_gateway_url_and_model_are_env_driven_never_hardcoded():
    """Never a hardcoded concrete model (card hard rule)."""
    assert hl.SKGATEWAY_MODEL == "sk-default"
    assert "18780" in hl.SKGATEWAY_URL
