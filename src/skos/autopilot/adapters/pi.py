"""PiAdapter: pi (pi.dev) on the shared BaseCliAdapter. Sovereign default target:
pi routes to a local model via skgateway, keeping all egress on-tailnet.

NOTE: pi is not installed on this node, so `_parse` is defensive. Validate the
real `pi -p --mode json` output shape against a captured sample before enabling
pi as a live harness (E1 / first canary)."""
from __future__ import annotations

import json

from .base import BaseCliAdapter, parse_event_stream


class PiAdapter(BaseCliAdapter):
    name = "pi"

    def __init__(self, sandbox=None, model=None, base_url=None, egress_hosts=None,
                 live_execution: bool = False, image=None):
        from ..sandbox import Sandbox
        self.model = model
        self.base_url = base_url
        self.image = image or "sandbox-pi:1"
        super().__init__(sandbox or Sandbox(live_execution=live_execution),
                         egress_hosts=egress_hosts, live_execution=live_execution)

    def capabilities(self):
        return {"session_resume": True, "structured_output": "json",
                "sandbox": True, "tool_restrictions": True}

    def _argv(self, prompt: str) -> list[str]:
        return ["pi", "-p", prompt, "--mode", "json"]

    def _image(self) -> str:
        return self.image

    def _auth_mounts(self):
        return []                              # local skgateway: no external cred

    def _auth_env(self):
        if not self.base_url:
            return {}
        return {"OPENAI_BASE_URL": self.base_url, "OPENAI_API_KEY": "sk-local",
                "PI_MODEL": self.model or ""}

    def _parse(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            return {}
        # already the model reply dict
        if any(k in raw for k in ("verdict", "score", "passed")):
            return raw
        body = raw.get("result")
        if isinstance(body, dict):
            return body
        if isinstance(body, str):
            # pi `--mode json` is an event stream (same shape family as opencode);
            # Sandbox.spawn hands it over as result=<stream> when not a lone object.
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
