"""OpenCodeAdapter: opencode (opencode.ai) on the shared BaseCliAdapter. Installed
sovereign fallback; can target a local model. `_parse` is defensive; validate the
real `opencode run` output shape before enabling opencode as a live harness.

NOTE: `opencode run "<msg>"` (message as a positional arg) silently produces zero
output; `echo "<msg>" | opencode run` (message on stdin) works and returns the
NDJSON stream. So `_argv` drops the prompt positional and `_stdin_for` feeds it
via stdin instead (see Sandbox/LaunchSpec.stdin). Also, opencode's first assistant
text chunk is the model's direct JSON reply; it then agentic-loops with further
chunks, so `parse_event_stream` takes the FIRST valid-JSON reply, not the last.
This is NOT a skgateway bug: opencode reaches skgateway and the model answers
correctly. Remaining follow-ups for a live sandbox opencode-on-skgateway run:
inject opencode.json (custom openai-compatible provider) + auth.json via
config_files (mirror PiAdapter), and tame opencode's agentic over-run on simple
assess/grade prompts (it produced ~87KB and ran long; the correct reply is the
first chunk)."""
from __future__ import annotations

import json

from .base import BaseCliAdapter, parse_event_stream


class OpenCodeAdapter(BaseCliAdapter):
    name = "opencode"

    _DEFAULT_MAX_TOKENS = 131072            # generous ceiling (ornith is uncapped)

    def __init__(self, sandbox=None, model=None, base_url=None, egress_hosts=None,
                 live_execution: bool = False, image=None, max_tokens=None):
        from ..sandbox import Sandbox
        self.model = model
        self.base_url = base_url
        self.image = image or "sandbox-opencode:1"
        self.max_tokens = int(max_tokens) if max_tokens else self._DEFAULT_MAX_TOKENS
        super().__init__(sandbox or Sandbox(live_execution=live_execution),
                         egress_hosts=egress_hosts, live_execution=live_execution)

    def capabilities(self):
        return {"session_resume": True, "structured_output": "json",
                "sandbox": True, "tool_restrictions": True}

    def _argv(self, prompt: str) -> list[str]:
        # `--format json` emits the raw NDJSON event stream (confirmed from
        # `opencode run --help`); without it opencode prints formatted text. The
        # prompt is NOT a positional arg here: `opencode run "<msg>"` silently
        # no-ops, so it is fed via stdin instead (see _stdin_for).
        argv = ["opencode", "run", "--format", "json", "--pure"]
        if self.model:
            # skgateway routing goes through the injected `skgw` custom provider
            # (see _config_files); otherwise a built-in provider/model id as-is.
            model = f"skgw/{self.model}" if self.base_url else self.model
            argv += ["--model", model]
        return argv

    def _stdin_for(self, prompt: str) -> str | None:
        return prompt

    def _image(self) -> str:
        return self.image

    def _auth_mounts(self):
        return []

    def _auth_env(self):
        # point opencode at the injected config (custom skgw provider); OPENAI_BASE_URL
        # does NOT work for opencode. Empty when no local endpoint is configured.
        return {"OPENCODE_CONFIG": "/cfg/opencode.json"} if self.base_url else {}

    def _config_files(self):
        if not self.base_url:
            return {}
        cfg = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "skgw": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "skgateway",
                    "options": {"baseURL": self.base_url, "apiKey": "sk-local"},
                    "models": {self.model: {
                        "name": self.model,
                        "limit": {"context": self.max_tokens, "output": self.max_tokens}}},
                }
            },
        }
        # config's options.apiKey authenticates the custom provider (verified live);
        # do NOT also mount ~/.local/share/opencode/auth.json: that nested mount makes
        # docker create ~/.local root-owned, and opencode then EACCES on mkdir
        # ~/.local/state. HOME stays a clean writable tmpfs so opencode makes its dirs.
        return {"/cfg/opencode.json": json.dumps(cfg)}

    def _parse(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}
        if any(k in raw for k in ("verdict", "score", "passed")):
            return raw
        body = raw.get("result")
        if isinstance(body, dict):
            return body
        if isinstance(body, str):
            # opencode `--format json` is an NDJSON event stream; Sandbox.spawn
            # could not json.loads it whole, so it arrives as result=<stream>.
            obj = parse_event_stream(body)
            if obj:
                return obj
            try:                                   # single-object fallback
                single = json.loads(body)
                if isinstance(single, dict):
                    return single
            except (json.JSONDecodeError, TypeError):
                pass
        return {}
