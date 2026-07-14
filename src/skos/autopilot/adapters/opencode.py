"""OpenCodeAdapter: opencode (opencode.ai) on the shared BaseCliAdapter. Installed
sovereign fallback targeting a local model (skgateway) or a built-in provider.
Verified live end-to-end in the confined sandbox against skgateway/ornith-tiny.

Routing to skgateway is a custom `skgw` provider injected via config_files
(`/cfg/opencode.json`, pointed to by OPENCODE_CONFIG); options.apiKey authenticates
it (no auth.json mount -- a nested auth.json mount makes docker create ~/.local
root-owned and opencode then EACCES on mkdir ~/.local/state). The provider's
`"npm": "@ai-sdk/openai-compatible"` resolves against the copy BUNDLED in opencode's
binary -- no runtime download, so the confined sandbox needs no npm egress.

Two opencode quirks the adapter handles: (1) `opencode run "<msg>"` (prompt as a
positional arg) silently no-ops; the prompt must go on stdin, so `_argv` drops the
positional and `_stdin_for` feeds it (see LaunchSpec.stdin). This was the real cause
of the earlier "opencode doesn't work with skgateway" -- NOT a skgateway bug; opencode
never sent a request. (2) opencode's first assistant text chunk is the model's direct
JSON reply; on multi-word tasks it then agentic-loops with further chunks, so
`parse_event_stream` takes the FIRST valid-JSON reply, not the last. `_parse` is
defensive across the event-stream and single-object shapes."""
from __future__ import annotations

import json

from .base import BaseCliAdapter, parse_event_stream


class OpenCodeAdapter(BaseCliAdapter):
    name = "opencode"

    _DEFAULT_MAX_TOKENS = 131072            # generous ceiling (ornith is uncapped)
    _DEFAULT_RUN_TIMEOUT = 300              # opencode agent-loops; bound it (see below)

    def __init__(self, sandbox=None, model=None, base_url=None, egress_hosts=None,
                 live_execution: bool = False, image=None, max_tokens=None,
                 run_timeout=None):
        from ..sandbox import Sandbox
        self.model = model
        self.base_url = base_url
        self.image = image or "sandbox-opencode:1"
        self.max_tokens = int(max_tokens) if max_tokens else self._DEFAULT_MAX_TOKENS
        # opencode runs as an agent and keeps looping past its first (correct) answer,
        # especially against a heavy local thinking model. Bound the sandbox run so a
        # classification prompt can't burn 30 min; the direct answer is in the first
        # streamed event and survives the timeout (Sandbox preserves partial stdout,
        # _parse takes the first valid JSON). A caller can override for real coding
        # tasks where a longer agentic loop is wanted.
        rt = int(run_timeout) if run_timeout else self._DEFAULT_RUN_TIMEOUT
        super().__init__(sandbox or Sandbox(live_execution=live_execution, run_timeout=rt),
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
