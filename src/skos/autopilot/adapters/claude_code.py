"""ClaudeCodeAdapter on the shared BaseCliAdapter + Docker Sandbox. Keeps the
deny-by-default tool firewall (fail closed) from claude_code.py; the spawn is the
harness-agnostic Sandbox."""
from __future__ import annotations

from ..claude_code import ForbiddenToolError, is_forbidden
from ..sandbox import AuthMount, Sandbox
from .base import BaseCliAdapter, extract_json


class ClaudeCodeAdapter(BaseCliAdapter):
    name = "claude-code"

    def __init__(self, allowed_tools, mcp_endpoints=None, live_execution: bool = False,
                 sandbox=None, image=None, max_turns: int = 50):
        for t in allowed_tools:
            if is_forbidden(t):
                raise ForbiddenToolError(
                    f"tool {t!r} is denied by the autopilot firewall (fail closed)")
        self.allowed_tools = list(allowed_tools)
        self.image = image or "sandbox-claude:1"
        # cap the agentic loop so a round returns within the sandbox run_timeout
        self.max_turns = int(max_turns)
        egress = list(mcp_endpoints or [])
        if "api.anthropic.com" not in egress:
            egress.append("api.anthropic.com")
        super().__init__(sandbox or Sandbox(live_execution=live_execution),
                         egress_hosts=egress, live_execution=live_execution)

    def capabilities(self):
        return {"session_resume": True, "structured_output": "json",
                "sandbox": True, "tool_restrictions": True}

    def _argv(self, prompt: str) -> list[str]:
        # Bound the agentic loop. Without --max-turns, `claude -p` runs unbounded
        # turns: on a focused TDD task it writes the correct code early, then keeps
        # exploring/re-verifying until the sandbox run_timeout (1800s) KILLS it at
        # exit 124 — before it emits its final JSON, so the orchestrator never gets
        # control to grade/commit/PR. A bounded round always RETURNS; the Ralph loop
        # (fresh session, re-reads disk each round) continues any unfinished work.
        return ["claude", "-p", prompt, "--dangerously-skip-permissions",
                "--max-turns", str(self.max_turns),
                "--output-format", "json", "--allowedTools", ",".join(self.allowed_tools)]

    def _image(self) -> str:
        return self.image

    def _auth_mounts(self):
        return [AuthMount("~/.claude/.credentials.json",
                          "/home/sbx/.claude/.credentials.json")]

    def _auth_env(self):
        return {}

    def _parse(self, raw: dict) -> dict:
        # claude-code --output-format json wraps the model reply as a STRING in
        # `result`; the assess/grade answer is JSON inside that string.
        inner = raw.get("result")
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str):
            obj = extract_json(inner)
            if obj is not None:
                return obj
        return raw if isinstance(raw, dict) else {}
