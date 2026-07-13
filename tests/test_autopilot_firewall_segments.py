"""Locks N4: is_forbidden must fail closed on a whole-server or malformed/empty
MCP tool segment (mcp__server__ dodges the old count("__")==1 check and the
bare-name match, so it used to fail open)."""
from skos.autopilot.claude_code import is_forbidden


def test_whole_server_grant_is_forbidden():
    assert is_forbidden("mcp__github")


def test_trailing_empty_tool_segment_is_forbidden():
    assert is_forbidden("mcp__github__")


def test_benign_named_tool_is_allowed():
    assert is_forbidden("mcp__github__list_issues") is False


def test_known_forbidden_bare_tool_stays_forbidden():
    assert is_forbidden("mcp__skcapstone__capauth_secret_get")
