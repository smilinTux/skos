from skos.autopilot.harness import (
    HarnessAdapter, ProviderCapabilities, warn_missing_capabilities,
)
from skos.autopilot.types import Verdict, HarnessResult, GateResult


class DummyAdapter:
    name = "dummy"

    def __init__(self, caps):
        self._caps = caps

    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    def assess(self, brief):
        return Verdict(verdict="valid", reason="ok")

    def run_task(self, brief):
        return HarnessResult(ok=True, artifact=None, tokens=0, cost_usd=0.0, raw={})

    def grade(self, brief):
        return GateResult(score=5, passed=True, notes="", artifact=None)


def _caps(**over):
    base = {"session_resume": True, "structured_output": "json",
            "sandbox": True, "tool_restrictions": True}
    base.update(over)
    return base


def test_protocol_shape_satisfied():
    a = DummyAdapter(_caps())
    assert isinstance(a, HarnessAdapter)          # runtime_checkable structural match
    assert a.assess(None).verdict == "valid"
    assert a.grade(None).passed is True


def test_warn_when_capability_absent():
    a = DummyAdapter(_caps(session_resume=False, structured_output="none"))
    warnings = warn_missing_capabilities(
        a, {"session_resume": True, "structured_output": "schema", "sandbox": True})
    assert len(warnings) == 2                      # session_resume + structured_output
    assert any("session_resume" in w for w in warnings)
    assert any("structured_output" in w for w in warnings)


def test_no_warn_when_all_present():
    a = DummyAdapter(_caps())
    assert warn_missing_capabilities(a, {"sandbox": True, "tool_restrictions": True}) == []
