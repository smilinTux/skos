"""OpenCodeAdapter: opencode (opencode.ai) on the shared BaseCliAdapter. Installed
sovereign fallback; can target a local model. `_parse` is defensive; validate the
real `opencode run` output shape before enabling opencode as a live harness."""
from __future__ import annotations

import json

from .base import BaseCliAdapter, parse_event_stream


class OpenCodeAdapter(BaseCliAdapter):
    name = "opencode"

    def __init__(self, sandbox=None, model=None, base_url=None, egress_hosts=None,
                 live_execution: bool = False, image=None):
        from ..sandbox import Sandbox
        self.model = model
        self.base_url = base_url
        self.image = image or "sandbox-opencode:1"
        super().__init__(sandbox or Sandbox(live_execution=live_execution),
                         egress_hosts=egress_hosts, live_execution=live_execution)

    def capabilities(self):
        return {"session_resume": True, "structured_output": "json",
                "sandbox": True, "tool_restrictions": True}

    def _argv(self, prompt: str) -> list[str]:
        # `--format json` emits the raw NDJSON event stream (confirmed from
        # `opencode run --help`); without it opencode prints formatted text.
        argv = ["opencode", "run", prompt, "--format", "json", "--pure"]
        if self.model:
            argv += ["--model", self.model]     # provider/model form, e.g. nvidia/x
        return argv

    def _image(self) -> str:
        return self.image

    def _auth_mounts(self):
        return []

    def _auth_env(self):
        if not self.base_url:
            return {}
        return {"OPENAI_BASE_URL": self.base_url, "OPENAI_API_KEY": "sk-local",
                "OPENCODE_MODEL": self.model or ""}

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
