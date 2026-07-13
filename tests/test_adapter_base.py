from skos.autopilot.adapters.base import BaseCliAdapter
from skos.autopilot.sandbox import Sandbox, LaunchSpec, AuthMount
from skos.autopilot.types import AssessBrief


class _Fake(BaseCliAdapter):
    name = "fake"
    def _argv(self, prompt): return ["fake", prompt]
    def _image(self): return "sandbox-fake:1"
    def _auth_mounts(self): return [AuthMount("/h/.cred", "/c/.cred")]
    def _auth_env(self): return {"BASE_URL": "http://gw.local"}
    def _parse(self, raw): return raw.get("result", raw)
    def capabilities(self): return {"session_resume": False, "structured_output": "json",
                                    "sandbox": True, "tool_restrictions": True}


def test_assess_builds_spec_and_delegates_to_sandbox(monkeypatch):
    seen = {}
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "spawn",
        lambda spec, **kw: seen.setdefault("spec", spec) and {"result": {"verdict": "valid", "reason": "ok"}})
    a = _Fake(sb, egress_hosts=["gw.local"])
    v = a.assess(AssessBrief(task_id="t1", title="t", description="d", acceptance=[],
                             tags=[], repo=None, codebase_context=""))
    assert v.verdict == "valid"
    spec = seen["spec"]
    assert isinstance(spec, LaunchSpec) and spec.image == "sandbox-fake:1"
    assert spec.argv[0] == "fake" and spec.auth_env["BASE_URL"] == "http://gw.local"
    assert spec.egress_hosts == ["gw.local"]
