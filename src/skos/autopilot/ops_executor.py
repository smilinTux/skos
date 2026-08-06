"""OpsExecutor: bare type for the "ops" work-item kind.

Scaffolding step only (ac575525): a plain, constructible type with no
Executor protocol methods yet. See docs/skos-autopilot-architecture.md
section 6 for the full ITIL v1.5 design this will grow into.
"""
from dataclasses import dataclass


@dataclass
class OpsExecutor:
    """Placeholder executor for ops-kind work items."""


def new_ops_executor() -> OpsExecutor:
    """Return an empty OpsExecutor instance."""
    return OpsExecutor()
