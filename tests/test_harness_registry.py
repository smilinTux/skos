import pytest
from types import SimpleNamespace
from skos.autopilot.harness import build_harness, HARNESSES
import skos.autopilot.adapters   # noqa: F401  triggers adapter registration


def _cfg(**kw):
    base = dict(harness="claude-code", allowed_tools=["Read"], mcp_endpoints=[],
                live_execution=False, harness_model=None, sandbox_image=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_registry_has_all_harnesses():
    for n in ("stub", "claude-code", "pi", "opencode", "codex"):
        assert n in HARNESSES


def test_build_by_name_and_default():
    assert build_harness(_cfg(harness="stub")).name == "stub"
    assert build_harness(_cfg(harness="pi")).name == "pi"
    assert build_harness(_cfg(harness="opencode")).name == "opencode"
    assert build_harness(_cfg(harness="codex")).name == "codex"
    assert build_harness(_cfg()).name == "claude-code"        # default from config.harness


def test_unknown_harness_fails_closed():
    with pytest.raises(ValueError):
        build_harness(_cfg(harness="nope"))
