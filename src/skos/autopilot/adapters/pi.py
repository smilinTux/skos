"""PiAdapter: pi (pi.dev) on the shared BaseCliAdapter. Sovereign target harness:
pi routes to a local model via skgateway, keeping all egress on-tailnet. Verified
live end-to-end in the confined sandbox against skgateway/ornith-tiny.

Routing recipe (all proven): inject /agent/models.json (skgw provider, api=
`openai-completions`, `compat.supportsDeveloperRole: false` -- REQUIRED for ornith
or it 400s) + PI_CODING_AGENT_DIR=/agent; `--model skgw/<model>` + `--api-key`;
`--no-session` (else the container-uid session mkdir EACCESes). pi IGNORES the
OPENAI_BASE_URL env (it hits real OpenAI), so routing MUST go through models.json.
The sandbox proxy forwards plain HTTP for allowlisted hosts, so the internal-net
container reaches the local http skgateway through it. pi's `--mode json` reply is
the assistant `message_end` event's content[].text; `_parse` handles that event
stream plus the single-object shape."""
from __future__ import annotations

import json

from .base import BaseCliAdapter, parse_event_stream


class PiAdapter(BaseCliAdapter):
    name = "pi"

    # ornith-big (Tyler's server) has no reasoning/output cap, so we give pi a
    # generous ceiling rather than a small one that a thinking model exhausts on
    # reasoning before emitting content. Overridable via config.harness_max_tokens.
    _DEFAULT_MAX_TOKENS = 131072

    def __init__(self, sandbox=None, model=None, base_url=None, egress_hosts=None,
                 live_execution: bool = False, image=None, max_tokens=None,
                 run_timeout=None):
        from ..sandbox import Sandbox
        self.model = model
        self.base_url = base_url
        self.image = image or "sandbox-pi:1"
        self.max_tokens = int(max_tokens) if max_tokens else self._DEFAULT_MAX_TOKENS
        # pi does one turn and terminates (measured ~3.6s for a classification prompt
        # against ornith-tiny), so unlike opencode it needs no aggressive cap -- it
        # keeps the sandbox default. run_timeout is exposed only so a caller can bound
        # a long coding run if wanted; None -> the Sandbox default.
        sb = sandbox
        if sb is None:
            kw = {"live_execution": live_execution}
            if run_timeout:
                kw["run_timeout"] = int(run_timeout)
            sb = Sandbox(**kw)
        super().__init__(sb, egress_hosts=egress_hosts, live_execution=live_execution)

    def capabilities(self):
        return {"session_resume": True, "structured_output": "json",
                "sandbox": True, "tool_restrictions": True}

    def _argv(self, prompt: str) -> list[str]:
        if not self.model:
            return ["pi", "-p", prompt, "--mode", "json", "--no-session"]
        return ["pi", "-p", prompt, "--mode", "json", "--no-session",
                "--model", f"skgw/{self.model}", "--api-key", "sk-local"]

    def _image(self) -> str:
        return self.image

    def _auth_mounts(self):
        return []                              # local skgateway: no external cred

    def _auth_env(self):
        # points pi at the injected config dir (models.json); do NOT set
        # OPENAI_BASE_URL, pi ignores it and hits real OpenAI instead.
        return {"PI_CODING_AGENT_DIR": "/agent"}

    def _config_files(self):
        if not self.base_url:
            return {}
        models = {
            "providers": {
                "skgw": {
                    "baseUrl": self.base_url,
                    "api": "openai-completions",
                    "apiKey": "sk-local",
                    "compat": {"supportsDeveloperRole": False},
                    "models": [{"id": self.model,
                                "limit": {"context": self.max_tokens,
                                          "output": self.max_tokens}}],
                }
            }
        }
        return {"/agent/models.json": json.dumps(models)}

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
