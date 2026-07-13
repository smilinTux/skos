"""build_executors: per-run executor table binding EngineeringExecutor to
this run's journal handle, threaded through run_once -> phase1_triage."""
from types import SimpleNamespace

from skos.autopilot import orchestrator as orch
from skos.autopilot.config import Caps
from skos.autopilot.engineering import EngineeringExecutor
from skos.autopilot.journal import RunHandle


def _cfg(**kw):
    base = dict(enabled=True, dry_run=True, caps=Caps(),
                repo_map={"skos": object()}, automerge_repos=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_build_executors_binds_engineering_to_run_handle():
    cfg = _cfg()
    board = object()
    table = orch.build_executors(cfg, board, "rX")
    eng = table["engineering"]
    assert isinstance(eng, EngineeringExecutor)
    assert eng.config is cfg and eng.board is board
    assert isinstance(eng.journal, RunHandle) and eng.journal.run_id == "rX"


def test_build_executors_keeps_stub_kinds():
    table = orch.build_executors(_cfg(), object(), "rY")
    for kind in ("research", "comms-draft", "orders", "calendar"):
        assert kind in table                     # routing table stays total


def test_build_executors_does_not_mutate_global():
    from skos.autopilot.executor import EXECUTORS
    before = dict(EXECUTORS)
    orch.build_executors(_cfg(), object(), "rZ")
    assert EXECUTORS == before                   # global untouched; returns a copy


def test_run_once_builds_and_threads_executors(monkeypatch, tmp_path):
    sentinel = {"engineering": object()}
    seen = {}
    monkeypatch.setattr(orch, "build_executors",
                        lambda config, board, run_id: seen.setdefault("run_id", run_id) and None or sentinel)
    def triage_spy(candidates, harness, *, repo_map, decisions, executors=None):
        seen["executors"] = executors
        return []
    monkeypatch.setattr(orch, "phase0_assess", lambda **kw: ([], []))
    monkeypatch.setattr(orch, "phase1_triage", triage_spy)
    monkeypatch.setattr(orch, "journal",
                        SimpleNamespace(read_run=lambda r: {}, write_run=lambda *a, **k: None))
    orch.run_once(board=object(), harness=object(), config=_cfg(dry_run=True),
                  tasks_dir=tmp_path, run_id="rZ")
    assert seen["run_id"] == "rZ"
    assert seen["executors"] is sentinel          # the built table is threaded into triage
