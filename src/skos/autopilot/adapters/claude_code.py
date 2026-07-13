"""ClaudeCodeAdapter on the shared BaseCliAdapter + Docker Sandbox. Keeps the
deny-by-default tool firewall (fail closed) from claude_code.py; the spawn is the
harness-agnostic Sandbox."""
from __future__ import annotations

from ..claude_code import ForbiddenToolError, is_forbidden
from ..sandbox import AuthMount, Sandbox
from .base import BaseCliAdapter


class ClaudeCodeAdapter(BaseCliAdapter):
    name = "claude-code"

    def __init__(self, allowed_tools, mcp_endpoints=None, live_execution: bool = False,
                 sandbox=None, image=None):
        for t in allowed_tools:
            if is_forbidden(t):
                raise ForbiddenToolError(
                    f"tool {t!r} is denied by the autopilot firewall (fail closed)")
        self.allowed_tools = list(allowed_tools)
        self.image = image or "sandbox-claude:1"
        super().__init__(sandbox or Sandbox(live_execution=live_execution),
                         egress_hosts=mcp_endpoints, live_execution=live_execution)

    def capabilities(self):
        return {"session_resume": True, "structured_output": "json",
                "sandbox": True, "tool_restrictions": True}

    def _argv(self, prompt: str) -> list[str]:
        return ["claude", "-p", prompt, "--dangerously-skip-permissions",
                "--output-format", "json", "--allowedTools", ",".join(self.allowed_tools)]

    def _image(self) -> str:
        return self.image

    def _auth_mounts(self):
        return [AuthMount("~/.claude/.credentials.json",
                          "/home/sbx/.claude/.credentials.json")]

    def _auth_env(self):
        return {}

    def _parse(self, raw: dict) -> dict:
        inner = raw.get("result")
        return inner if isinstance(inner, dict) else raw
