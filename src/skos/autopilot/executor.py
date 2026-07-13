"""Executor Protocol + the kind-keyed registry.

An Executor turns one class of WorkItem into a graded, finalized (or escalated)
outcome. The orchestrator (orchestrator.py) never knows an executor's internals;
it looks one up by ``WorkItem.kind`` in EXECUTORS and drives the seam below.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import WorkItem, GateResult, DecisionItem


@runtime_checkable
class Executor(Protocol):
    kind: str
    def selectable(self, item: WorkItem) -> bool: ...
    def run(self, item: WorkItem, harness) -> GateResult: ...
    def finalize(self, item: WorkItem, result: GateResult) -> None: ...
    def escalate(self, item: WorkItem, reason: str) -> DecisionItem: ...


EXECUTORS: dict[str, Executor] = {}


def register(ex: Executor) -> Executor:
    """Register (or replace) an executor under its ``kind``. Last write wins."""
    EXECUTORS[ex.kind] = ex
    return ex


def get(kind: str) -> Executor | None:
    """Look up an executor by kind, or None when none is registered."""
    return EXECUTORS.get(kind)
