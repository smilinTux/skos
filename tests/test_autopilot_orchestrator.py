"""Autopilot orchestrator: helpers, phases, run_once, dry-run, kill switch, caps, resume."""
from types import SimpleNamespace

import pytest

from skos.autopilot import orchestrator as orch
from skos.autopilot.orchestrator import Caps, CapLedger, kill_switch_active, stable_qid


def test_kill_switch_env(monkeypatch):
    monkeypatch.setenv("SKOS_AUTOPILOT_OFF", "1")
    assert kill_switch_active(enabled=True) is True


def test_kill_switch_disabled_flag(monkeypatch):
    monkeypatch.delenv("SKOS_AUTOPILOT_OFF", raising=False)
    assert kill_switch_active(enabled=False) is True
    assert kill_switch_active(enabled=True) is False


def test_cap_ledger_exceeded_on_tokens():
    led = CapLedger(Caps(max_tokens_per_run=100, max_usd_per_day=10.0))
    led.add(tokens=60, usd=1.0)
    assert led.exceeded() is False
    led.add(tokens=60, usd=1.0)
    assert led.exceeded() is True


def test_cap_ledger_exceeded_on_usd():
    led = CapLedger(Caps(max_tokens_per_run=10_000, max_usd_per_day=2.0))
    led.add(tokens=1, usd=2.5)
    assert led.exceeded() is True


def test_stable_qid_deterministic():
    a = stable_qid("Merge PR #12 for task X?", "task-x")
    b = stable_qid("Merge PR #12 for task X?", "task-x")
    c = stable_qid("Merge PR #12 for task X?", "task-y")
    assert a == b and a != c and len(a) == 12


import json
from unittest.mock import MagicMock

from skos.autopilot.types import Verdict


def _write_task(d, tid, **fields):
    t = {"id": tid, "title": tid, "description": "", "tags": [],
         "acceptance_criteria": [], "dependencies": [], "status": "open"}
    t.update(fields)
    (d / f"{tid}-x.json").write_text(json.dumps(t))
    return t


def _board(unblocked):
    b = MagicMock()
    b.unblocked_task_ids.return_value = set(unblocked)
    return b


def test_phase0_reclaims_then_computes_unblocked(tmp_path):
    _write_task(tmp_path, "t-1", tags=["repo:skos"], acceptance_criteria=["works"])
    _write_task(tmp_path, "t-2", tags=["repo:skos"])
    board = _board(["t-1"])
    harness = MagicMock()
    harness.assess.return_value = Verdict(verdict="valid", reason="")
    cands, decisions = orch.phase0_assess(board=board, harness=harness, tasks_dir=tmp_path,
                                          caps=Caps(), run_id="r1")
    board.release_stale_claims.assert_called_once_with("autopilot", 3600)
    assert [c.ref for c in cands] == ["t-1"]          # only unblocked assessed
    assert decisions == []


def test_phase0_applies_verdicts(tmp_path):
    _write_task(tmp_path, "stale", tags=["repo:skos"])
    _write_task(tmp_path, "dead", tags=["repo:skos"])
    _write_task(tmp_path, "ask", tags=["repo:skos"])
    board = _board(["stale", "dead", "ask"])
    harness = MagicMock()
    harness.assess.side_effect = [
        Verdict(verdict="needs_decision", reason="which repo?"),
        Verdict(verdict="obsolete", reason="superseded"),
        Verdict(verdict="stale", reason="drifted", updated_description="new",
                updated_acceptance=["a"]),
    ]
    cands, decisions = orch.phase0_assess(board=board, harness=harness, tasks_dir=tmp_path,
                                          caps=Caps(), run_id="r1")
    board.update_task.assert_called_once_with("stale", description="new",
                                              acceptance_criteria=["a"], run_id="r1")
    board.close_task_obsolete.assert_called_once_with("dead", "superseded", run_id="r1")
    assert {c.ref for c in cands} == {"stale"}         # stale rewritten stays actionable
    assert len(decisions) == 1 and decisions[0].action_ref == "ask"


def test_phase0_dry_run_writes_nothing(tmp_path):
    _write_task(tmp_path, "stale", tags=["repo:skos"])
    board = _board(["stale"])
    harness = MagicMock()
    harness.assess.return_value = Verdict(verdict="stale", reason="d", updated_description="n")
    orch.phase0_assess(board=board, harness=harness, tasks_dir=tmp_path,
                       caps=Caps(), run_id="r1", dry_run=True)
    board.update_task.assert_not_called()
    board.close_task_obsolete.assert_not_called()


def test_deepdive_spawn_caps_and_tags(tmp_path):
    board = MagicMock()
    board.create_task.side_effect = ["n1", "n2"]
    props = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
    made = orch.deepdive_spawn(board, props, caps=Caps(new_tasks_per_run=2), run_id="r1")
    assert made == ["n1", "n2"]                        # capped at 2
    for call in board.create_task.call_args_list:
        assert "autopilot-untriaged" in call.kwargs["tags"]


def test_deepdive_spawn_dry_run_no_writes():
    board = MagicMock()
    orch.deepdive_spawn(board, [{"title": "a"}], caps=Caps(), run_id="r1", dry_run=True)
    board.create_task.assert_not_called()


from skos.autopilot.executor import EXECUTORS
from skos.autopilot.types import WorkItem, GateResult, DecisionItem


class _Eng:
    kind = "engineering"
    def __init__(self, sel): self._sel = sel; self.escalate = MagicMock()
    def selectable(self, item): return self._sel
    def run(self, item, harness): return GateResult(5, True, "", None)
    def finalize(self, item, result): pass


@pytest.fixture
def clean_execs():
    saved = dict(EXECUTORS); EXECUTORS.clear()
    yield
    EXECUTORS.clear(); EXECUTORS.update(saved)


def _wi(ref, repo="skos", tags=None):
    return WorkItem(kind="engineering", ref=ref, source="coord", repo=repo,
                    payload={"id": ref, "tags": tags if tags is not None else [f"repo:{repo}"]})


def test_phase1_selects_only_selectable_in_scope(clean_execs):
    ex = _Eng(sel=True); EXECUTORS["engineering"] = ex
    decisions = []
    selected = orch.phase1_triage([_wi("t-1")], MagicMock(),
                                  repo_map={"skos": object()}, decisions=decisions)
    assert [i.ref for i, _ in selected] == ["t-1"] and decisions == []


def test_phase1_untriaged_never_selected(clean_execs):
    ex = _Eng(sel=True); EXECUTORS["engineering"] = ex
    decisions = []
    item = _wi("t-u", tags=["repo:skos", "autopilot-untriaged"])
    selected = orch.phase1_triage([item], MagicMock(),
                                  repo_map={"skos": object()}, decisions=decisions)
    assert selected == [] and decisions == []          # promoted by operator, not queued here


def test_phase1_unselectable_queues_without_escalate(clean_execs):
    ex = _Eng(sel=False); EXECUTORS["engineering"] = ex
    decisions = []
    selected = orch.phase1_triage([_wi("t-2")], MagicMock(),
                                  repo_map={"skos": object()}, decisions=decisions)
    assert selected == [] and len(decisions) == 1 and decisions[0].action_ref == "t-2"
    ex.escalate.assert_not_called()                     # escalate is only for mid-run gate fail


def test_phase1_unknown_repo_queues_decision(clean_execs):
    ex = _Eng(sel=True); EXECUTORS["engineering"] = ex
    decisions = []
    selected = orch.phase1_triage([_wi("t-3", repo="ghost")], MagicMock(),
                                  repo_map={"skos": object()}, decisions=decisions)
    assert selected == [] and len(decisions) == 1


def test_phase1_no_executor_queues_decision(clean_execs):
    decisions = []
    item = WorkItem(kind="research", ref="t-4", source="email", repo=None, payload={"id": "t-4", "tags": []})
    selected = orch.phase1_triage([item], MagicMock(), repo_map={}, decisions=decisions)
    assert selected == [] and len(decisions) == 1


@pytest.fixture(autouse=True)
def fake_journal(monkeypatch):
    writes = []
    ns = SimpleNamespace(read_run=lambda rid: {}, write_run=lambda rid, d: writes.append((rid, d)))
    monkeypatch.setattr(orch, "journal", ns)
    return writes


class _RunExec:
    kind = "engineering"
    def __init__(self, result):
        self._result = result
        self.run = MagicMock(return_value=result)
        self.finalize = MagicMock()
        self.escalate = MagicMock(return_value=DecisionItem(qid="e", prompt="stuck",
                                  options={}, action_ref="t", priority="high"))
    def selectable(self, item): return True


def test_phase2_finalizes_and_scores_on_pass():
    ex = _RunExec(GateResult(score=5, passed=True, notes="ok", artifact="pr#1"))
    board = MagicMock(); harness = SimpleNamespace(name="claude-code")
    decisions = []
    state = orch.phase2_swarm([(_wi("t-1"), ex)], harness=harness, board=board,
                              caps=Caps(), ledger=CapLedger(Caps()), decisions=decisions,
                              run_id="r1")
    ex.run.assert_called_once()
    ex.finalize.assert_called_once()
    board.score_task.assert_called_once()
    assert board.score_task.call_args.kwargs["score"] == 5
    assert state["t-1"]["state"] == "finalized" and decisions == []


def test_phase2_escalates_on_non_convergence():
    ex = _RunExec(GateResult(score=4, passed=False, notes="thin tests", artifact=None))
    board = MagicMock(); decisions = []
    state = orch.phase2_swarm([(_wi("t-2"), ex)], harness=SimpleNamespace(name="h"),
                              board=board, caps=Caps(), ledger=CapLedger(Caps()),
                              decisions=decisions, run_id="r1")
    ex.finalize.assert_not_called()
    ex.escalate.assert_called_once()
    assert state["t-2"]["state"] == "escalated" and len(decisions) == 1


def _decision(qid="q1", prio="high"):
    return DecisionItem(qid=qid, prompt=f"Merge PR for {qid}?", options={"yes": "y", "no": "n"},
                        action_ref="task-x", priority=prio)


def test_write_decision_captures_source_autopilot(monkeypatch, tmp_path):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    import skos.gtd_ingest as gi
    orch.write_decision(_decision("q1"))
    items = json.loads((gi.gtd_dir() / "waiting-for.json").read_text())
    assert items[0]["source"] == "autopilot" and items[0]["source_ref"] == "autopilot:q1"
    assert items[0]["decision"]["qid"] == "q1" and items[0]["decision"]["answered"] is False


def test_write_decision_falls_back_to_upsert_on_dup(monkeypatch):
    cap = MagicMock(return_value=None)              # simulate duplicate
    ups = MagicMock(return_value=("id1", "unchanged"))
    monkeypatch.setattr("skos.gtd_ingest.capture", cap)
    monkeypatch.setattr("skos.gtd_ingest.upsert", ups)
    gid = orch.write_decision(_decision("q2"))
    cap.assert_called_once()
    ups.assert_called_once()                        # None -> upsert guarantees presence
    assert gid == "id1"


def test_phase3_dry_run_no_gtd_writes(monkeypatch):
    cap = MagicMock(); monkeypatch.setattr("skos.gtd_ingest.capture", cap)
    out = orch.phase3_report([_decision("q3")], dry_run=True, digest_date="2026-07-12")
    cap.assert_not_called()
    assert out["dry_run"] is True and "digest_preview" in out


def test_phase3_writes_and_builds_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    out = orch.phase3_report([_decision("q4")], dry_run=False, digest_date="2026-07-12")
    from skos.autopilot import digest
    from skos.gtd_ingest import gtd_dir
    assert (gtd_dir() / "autopilot-digest.json").exists()
    assert out["manifest"]["items"][0]["qid"] == "q4"


def _config(**kw):
    base = dict(enabled=True, dry_run=False, caps=Caps(), repo_map={"skos": object()})
    base.update(kw)
    return SimpleNamespace(**base)


def test_run_once_full_pipeline(tmp_path, clean_execs):
    _write_task(tmp_path, "t-1", tags=["repo:skos"], acceptance_criteria=["works"])
    ex = _RunExec(GateResult(5, True, "ok", "pr#1")); EXECUTORS["engineering"] = ex
    board = _board(["t-1"])
    harness = SimpleNamespace(name="claude-code",
                              assess=lambda brief: Verdict(verdict="valid", reason=""))
    out = orch.run_once(board=board, harness=harness, config=_config(),
                        tasks_dir=tmp_path, run_id="r1")
    ex.run.assert_called_once()
    ex.finalize.assert_called_once()
    assert out["selected"] == ["t-1"] and out["run_id"] == "r1"
    assert out["report"]["dry_run"] is False


def test_dry_run_is_read_only(tmp_path, monkeypatch, clean_execs, fake_journal):
    _write_task(tmp_path, "stale", tags=["repo:skos"], acceptance_criteria=["x"])
    ex = _RunExec(GateResult(5, True, "ok", "pr#1")); EXECUTORS["engineering"] = ex
    board = _board(["stale"])
    board.create_task = MagicMock()
    harness = SimpleNamespace(name="h",
        assess=lambda brief: Verdict(verdict="stale", reason="d", updated_description="n"))
    cap = MagicMock(); monkeypatch.setattr("skos.gtd_ingest.capture", cap)

    out = orch.run_once(board=board, harness=harness, config=_config(dry_run=True),
                        tasks_dir=tmp_path, run_id="rdry",
                        deepdive_proposals=[{"title": "new"}])

    board.update_task.assert_not_called()           # no coord mutation
    board.close_task_obsolete.assert_not_called()
    board.create_task.assert_not_called()
    board.score_task.assert_not_called()
    ex.run.assert_not_called()                       # Phase 2 skipped
    cap.assert_not_called()                          # no GTD write
    assert out["dry_run"] is True
    assert out["report"]["dry_run"] is True and "digest_preview" in out["report"]
    assert any(rid == "rdry" for rid, _ in fake_journal)  # journal entry written


def test_kill_switch_stops_before_swarm(tmp_path, monkeypatch, clean_execs, fake_journal):
    monkeypatch.setenv("SKOS_AUTOPILOT_OFF", "1")
    _write_task(tmp_path, "t-1", tags=["repo:skos"], acceptance_criteria=["x"])
    ex = _RunExec(GateResult(5, True, "ok", "pr")); EXECUTORS["engineering"] = ex
    board = _board(["t-1"])
    harness = SimpleNamespace(name="h", assess=lambda b: Verdict(verdict="valid", reason=""))
    out = orch.run_once(board=board, harness=harness, config=_config(),
                        tasks_dir=tmp_path, run_id="rk")
    ex.run.assert_not_called()                       # never entered Phase 2
    assert out["stopped"] == "kill_switch"


def test_caps_stop_and_escalate_between_items(clean_execs, fake_journal):
    ex = _RunExec(GateResult(5, True, "ok", "pr")); EXECUTORS["engineering"] = ex
    board = MagicMock()
    ledger = CapLedger(Caps(max_tokens_per_run=100)); ledger.tokens = 200  # already over
    decisions = []
    state = orch.phase2_swarm([(_wi("t-1"), ex), (_wi("t-2"), ex)],
                              harness=SimpleNamespace(name="h"), board=board,
                              caps=Caps(), ledger=ledger, decisions=decisions, run_id="rc")
    ex.run.assert_not_called()                       # stopped before any run
    assert state == {}
    assert len(decisions) == 1 and "budget" in decisions[0].prompt.lower()


def test_resume_skips_finalized(tmp_path, monkeypatch, clean_execs):
    _write_task(tmp_path, "t-A", tags=["repo:skos"], acceptance_criteria=["x"])
    _write_task(tmp_path, "t-B", tags=["repo:skos"], acceptance_criteria=["x"])
    ex = _RunExec(GateResult(5, True, "ok", "pr")); EXECUTORS["engineering"] = ex
    board = _board(["t-A", "t-B"])
    harness = SimpleNamespace(name="h", assess=lambda b: Verdict(verdict="valid", reason=""))
    prior = {"run_id": "rr", "items": {"t-A": {"state": "finalized", "round": 1, "score": 5}}}
    monkeypatch.setattr(orch, "journal", SimpleNamespace(
        read_run=lambda rid: prior, write_run=lambda rid, d: None))

    orch.run_once(board=board, harness=harness, config=_config(),
                  tasks_dir=tmp_path, run_id="rr")

    # t-A already finalized -> not re-run; only t-B runs
    ran = [c.args[0].ref for c in ex.run.call_args_list]
    assert ran == ["t-B"]
