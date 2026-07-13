import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from skos.autopilot import orchestrator as orch
from skos.autopilot import digest as digest_mod
from skos.autopilot.config import Caps
from skos.autopilot.harness import StubHarness


def _write_task(d, tid, **fields):
    t = {"id": tid, "title": tid, "description": "", "tags": [],
         "acceptance_criteria": [], "dependencies": [], "status": "open"}
    t.update(fields)
    (d / f"{tid}-x.json").write_text(json.dumps(t))


def _config(**kw):
    base = dict(enabled=True, dry_run=True, caps=Caps(),
                repo_map={"skos": object()}, automerge_repos=[])
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def board():
    b = MagicMock()
    b.unblocked_task_ids.return_value = {"t-valid", "t-norepo"}
    return b


def test_e2e_dry_run_selects_and_writes_nothing(tmp_path, monkeypatch, board):
    monkeypatch.setenv("SK_AUTOPILOT_RUNS_DIR", str(tmp_path / "runs"))
    # a valid, in-scope engineering task, and one with no repo tag (queues a decision)
    _write_task(tmp_path, "t-valid", tags=["repo:skos"], acceptance_criteria=["works"])
    _write_task(tmp_path, "t-norepo", tags=[], acceptance_criteria=["x"])

    # spy on every write surface
    cap = MagicMock(); up = MagicMock(); send = MagicMock()
    monkeypatch.setattr("skos.gtd_ingest.capture", cap)
    monkeypatch.setattr("skos.gtd_ingest.upsert", up)
    monkeypatch.setattr(digest_mod, "send_digest", send)

    out = orch.run_once(board=board, harness=StubHarness(), config=_config(),
                        tasks_dir=tmp_path, run_id="e2e", executors=None)  # production wiring

    # (a) the real EngineeringExecutor selected the valid task end-to-end
    assert out["selected"] == ["t-valid"]
    assert out["dry_run"] is True
    assert out["decisions"] == 1                         # t-norepo -> which-repo decision
    assert out["report"]["dry_run"] is True
    assert isinstance(out["report"]["digest_preview"], str)

    # (b) genuinely read-only: no coord mutation, no GTD write, no DM
    board.update_task.assert_not_called()
    board.score_task.assert_not_called()
    board.close_task_obsolete.assert_not_called()
    board.complete_task.assert_not_called()
    board.create_task.assert_not_called()
    board.release_stale_claims.assert_not_called()  # dry-run must not reclaim (B1)
    board.claim_task.assert_not_called()             # run() never reached in dry-run
    cap.assert_not_called()
    up.assert_not_called()
    send.assert_not_called()
