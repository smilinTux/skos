"""Stub executors register and never self-select (routing table stays total)."""
import pytest

from skos.autopilot import stubs
from skos.autopilot.executor import EXECUTORS
from skos.autopilot.types import DecisionItem, WorkItem


def _item(kind):
    return WorkItem(kind=kind, ref="r1", source="email", repo=None, payload={})


def test_all_four_stubs_register():
    stubs.register_stubs()
    for kind in ("research", "comms-draft", "orders", "calendar"):
        assert kind in EXECUTORS
        assert EXECUTORS[kind].kind == kind


def test_stubs_never_self_select():
    stubs.register_stubs()
    for kind in ("research", "comms-draft", "orders", "calendar"):
        assert EXECUTORS[kind].selectable(_item(kind)) is False


def test_stub_run_raises_not_implemented():
    stubs.register_stubs()
    with pytest.raises(NotImplementedError):
        EXECUTORS["research"].run(_item("research"), harness=None)


def test_stub_escalate_returns_decision_item():
    stubs.register_stubs()
    d = EXECUTORS["comms-draft"].escalate(_item("comms-draft"), "needs operator")
    assert isinstance(d, DecisionItem)
    assert d.qid and d.prompt and d.priority
