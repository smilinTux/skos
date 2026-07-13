"""Non-engineering executors, stubbed (spec sections 4 and 7).

Each is a real registered class so the routing table is total, but
``selectable`` is False so a matched item is queued to the operator by the
orchestrator and ``run`` is never entered. They exist so widening autopilot to a
new work-type is adding a ``run`` body, not new plumbing.
"""
from __future__ import annotations

import hashlib

from skos.autopilot.executor import EXECUTORS
from skos.autopilot.types import DecisionItem, WorkItem


class _Stub:
    kind: str = ""

    def selectable(self, item: WorkItem) -> bool:
        return False                                  # never acts without the operator

    def run(self, item: WorkItem, harness):
        raise NotImplementedError(f"{self.kind} executor is a v1 stub (spec section 7)")

    def finalize(self, item: WorkItem, result) -> None:
        return None

    def escalate(self, item: WorkItem, reason: str) -> DecisionItem:
        qid = hashlib.sha1(f"{self.kind}:{item.ref}:{reason}".encode()).hexdigest()[:12]
        return DecisionItem(qid=qid,
                            prompt=f"{self.kind} item {item.ref}: {reason}",
                            options={}, action_ref=None, priority="medium")


class ResearchStub(_Stub):
    kind = "research"


class CommsDraftStub(_Stub):
    kind = "comms-draft"


class OrdersStub(_Stub):
    kind = "orders"


class CalendarStub(_Stub):
    kind = "calendar"


def register_stubs() -> None:
    """Insert the stub executors into the shared registry (idempotent)."""
    for stub in (ResearchStub(), CommsDraftStub(), OrdersStub(), CalendarStub()):
        EXECUTORS[stub.kind] = stub


register_stubs()
