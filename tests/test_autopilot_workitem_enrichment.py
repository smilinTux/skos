from types import SimpleNamespace

from skos.autopilot.orchestrator import _to_workitem
from skos.autopilot.engineering import EngineeringExecutor
from skos.autopilot.config import Caps


def _ex():
    cfg = SimpleNamespace(repo_map={"skos": object()}, caps=Caps(), automerge_repos=[])
    return EngineeringExecutor(cfg, board=object(), journal=object())


def test_to_workitem_enriches_payload_for_selectable():
    task = {"id": "t1", "tags": ["repo:skos"], "acceptance_criteria": ["works"],
            "status": "open"}
    wi = _to_workitem(task, verdict="valid")
    assert wi.ref == "t1" and wi.repo == "skos" and wi.kind == "engineering"
    assert wi.payload["unblocked"] is True
    assert wi.payload["verdict"] == "valid"
    assert wi.payload["acceptance"] == ["works"]
    assert _ex().selectable(wi) is True           # real executor now selects it


def test_to_workitem_stale_is_not_auto_selectable():
    task = {"id": "t2", "tags": ["repo:skos"], "acceptance_criteria": ["x"],
            "status": "open"}
    wi = _to_workitem(task, verdict="stale")
    assert wi.payload["verdict"] == "stale"
    assert _ex().selectable(wi) is False           # rewritten-stale surfaced, not auto-worked


def test_to_workitem_preserves_original_task_keys():
    task = {"id": "t3", "tags": ["repo:skos"], "acceptance_criteria": ["x"],
            "status": "open", "priority": "high"}
    wi = _to_workitem(task)
    assert wi.payload["priority"] == "high" and wi.payload["status"] == "open"
