from skos.autopilot.types import (
    WorkItem, RepoSpec, AssessBrief, TaskBrief, GradeBrief,
    GateResult, Verdict, HarnessResult, DecisionItem,
)


def test_repospec_defaults():
    r = RepoSpec(name="skos", path="/x", base_branch="main",
                 integration_branch="autopilot", test_cmd="pytest -q",
                 ci="github-actions")
    assert r.coverage_cmd is None
    assert r.ci_poll_timeout == 1200
    assert r.automerge is False and r.auto_revert is False
    assert r.min_diff_coverage == 0.8


def test_verdict_optional_fields_default_none():
    v = Verdict(verdict="valid", reason="ok")
    assert v.updated_description is None and v.updated_acceptance is None


def test_all_contracts_construct():
    wi = WorkItem(kind="engineering", ref="t1", source="coord", repo="skos", payload={})
    rs = RepoSpec("skos", "/x", "main", "ap", "pytest", "none")
    ab = AssessBrief(task_id="t1", title="T", description="d", acceptance=["a"],
                     tags=["repo:skos"], repo="skos", codebase_context="ctx")
    tb = TaskBrief(task_id="t1", repo=rs, worktree="/wt", title="T", description="d",
                   acceptance=["a"], prior_feedback=None, round=1)
    gb = GradeBrief(task_id="t1", repo=rs, worktree="/wt", diff="+x",
                    acceptance=["a"], ci_status="green", diff_coverage=0.9)
    gr = GateResult(score=5, passed=True, notes="", artifact=None)
    hr = HarnessResult(ok=True, artifact="/wt", tokens=10, cost_usd=0.01, raw={})
    di = DecisionItem(qid="q1", prompt="?", options={"yes": 1}, action_ref=None, priority="high")
    assert wi.repo == "skos" and gr.passed and hr.tokens == 10 and di.qid == "q1"
    assert ab.tags == ["repo:skos"] and tb.round == 1 and gb.ci_status == "green"
