import pytest
from skos.autopilot.adapters.codex import CodexStubAdapter
from skos.autopilot.claude_code import HarnessUnavailable


def test_all_seam_methods_fail_closed():
    a = CodexStubAdapter()
    assert a.name == "codex"
    assert a.capabilities()["sandbox"] is False
    for call in (lambda: a.assess(None), lambda: a.run_task(None), lambda: a.grade(None)):
        with pytest.raises(HarnessUnavailable):
            call()
