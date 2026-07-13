from skos.autopilot.adapters.base import BaseCliAdapter
from skos.autopilot.sandbox import Sandbox, LaunchSpec, AuthMount
from skos.autopilot.types import AssessBrief, RepoSpec, TaskBrief


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


def _repo(**kw):
    base = dict(name="r", path="/tmp/r", base_branch="main", integration_branch="int",
                test_cmd="pytest", ci="none")
    base.update(kw)
    return RepoSpec(**base)


def _task_brief(repo):
    return TaskBrief(task_id="t1", repo=repo, worktree="/tmp/wt", title="t",
                     description="d", acceptance=[], prior_feedback=None, round=0)


def test_run_task_crash_is_not_ok(monkeypatch):
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "spawn",
        lambda spec, **kw: {"result": "boom", "exit_code": 1, "is_error": True})
    a = _Fake(sb, egress_hosts=[])
    result = a.run_task(_task_brief(_repo()))
    assert result.ok is False


def test_run_task_clean_json_exit_zero_is_ok(monkeypatch):
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "spawn", lambda spec, **kw: {"result": {"x": 1}})
    a = _Fake(sb, egress_hosts=[])
    result = a.run_task(_task_brief(_repo()))
    assert result.ok is True


def test_run_raw_uses_per_repo_sandbox_image_override(monkeypatch):
    seen = {}
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "spawn",
        lambda spec, **kw: seen.setdefault("spec", spec) and {"result": {}})
    a = _Fake(sb, egress_hosts=[])
    a._run_raw("instr", "data", worktree="/tmp/wt", repo=_repo(sandbox_image="repo-img:9"))
    assert seen["spec"].image == "repo-img:9"


def test_run_raw_falls_back_to_adapter_image_when_repo_image_is_none(monkeypatch):
    seen = {}
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "spawn",
        lambda spec, **kw: seen.setdefault("spec", spec) and {"result": {}})
    a = _Fake(sb, egress_hosts=[])
    a._run_raw("instr", "data", worktree="/tmp/wt", repo=_repo(sandbox_image=None))
    assert seen["spec"].image == "sandbox-fake:1"


def test_extract_json_tolerates_fences_and_prose():
    from skos.autopilot.adapters.base import extract_json
    assert extract_json('{"score": 5}') == {"score": 5}
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert extract_json('answer: {"verdict":"valid"} done') == {"verdict": "valid"}
    assert extract_json('no json') is None
    assert extract_json(None) is None


def test_claude_parse_extracts_from_result_string():
    from skos.autopilot.adapters.claude_code import ClaudeCodeAdapter
    from skos.autopilot.sandbox import Sandbox
    a = ClaudeCodeAdapter(["Read"], sandbox=Sandbox())
    raw = {"type": "result", "result": '{"score":5,"passed":true,"notes":"ok"}', "is_error": False}
    assert a._parse(raw) == {"score": 5, "passed": True, "notes": "ok"}
