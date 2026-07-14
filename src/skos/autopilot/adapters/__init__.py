from ..harness import register_harness
from .claude_code import ClaudeCodeAdapter
from .pi import PiAdapter
from .opencode import OpenCodeAdapter
from .codex import CodexStubAdapter

__all__ = ["ClaudeCodeAdapter", "PiAdapter", "OpenCodeAdapter", "CodexStubAdapter"]


def _g(config, key, default=None):
    return getattr(config, key, default)


register_harness("claude-code", lambda c: ClaudeCodeAdapter(
    _g(c, "allowed_tools", []) or [], mcp_endpoints=_g(c, "mcp_endpoints"),
    live_execution=_g(c, "live_execution", False), image=_g(c, "sandbox_image")))
register_harness("pi", lambda c: PiAdapter(
    model=_g(c, "harness_model"), base_url=_g(c, "harness_base_url"),
    egress_hosts=_g(c, "mcp_endpoints") or [], live_execution=_g(c, "live_execution", False),
    image=_g(c, "sandbox_image"), max_tokens=_g(c, "harness_max_tokens")))
register_harness("opencode", lambda c: OpenCodeAdapter(
    model=_g(c, "harness_model"), base_url=_g(c, "harness_base_url"),
    egress_hosts=_g(c, "mcp_endpoints") or [], live_execution=_g(c, "live_execution", False),
    image=_g(c, "sandbox_image")))
register_harness("codex", lambda c: CodexStubAdapter())
