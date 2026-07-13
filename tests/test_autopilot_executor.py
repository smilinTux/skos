"""Executor Protocol + registry: registration and lookup by kind."""
import pytest

from skos.autopilot.executor import Executor, EXECUTORS, register, get
from skos.autopilot.types import WorkItem, GateResult, DecisionItem


class _FakeExec:
    kind = "engineering"
    def selectable(self, item): return True
    def run(self, item, harness): return GateResult(score=5, passed=True, notes="", artifact=None)
    def finalize(self, item, result): pass
    def escalate(self, item, reason):
        return DecisionItem(qid="q", prompt=reason, options={}, action_ref=item.ref, priority="high")


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(EXECUTORS)
    EXECUTORS.clear()
    yield
    EXECUTORS.clear()
    EXECUTORS.update(saved)


def test_register_and_get_by_kind():
    ex = _FakeExec()
    assert register(ex) is ex
    assert EXECUTORS["engineering"] is ex
    assert get("engineering") is ex


def test_get_unknown_kind_returns_none():
    assert get("nope") is None


def test_register_is_idempotent_last_wins():
    a, b = _FakeExec(), _FakeExec()
    register(a); register(b)
    assert get("engineering") is b


def test_fakeexec_satisfies_protocol():
    assert isinstance(_FakeExec(), Executor)
