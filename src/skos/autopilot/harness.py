"""Harness adapter seam: the swappable model-execution port (spec section 11).

The orchestrator core holds no harness-specific logic; every model call goes
through a HarnessAdapter. The provider capability matrix (spec section 21) lets
the orchestrator warn at load time when a run needs a capability the selected
harness lacks, instead of failing mid-run.
"""
from __future__ import annotations

import logging
from typing import Protocol, TypedDict, runtime_checkable

from .types import AssessBrief, GateResult, GradeBrief, HarnessResult, TaskBrief, Verdict

log = logging.getLogger("skos.autopilot.harness")


class ProviderCapabilities(TypedDict):
    session_resume: bool
    structured_output: str          # "none" | "json" | "schema"
    sandbox: bool
    tool_restrictions: bool


@runtime_checkable
class HarnessAdapter(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...
    def assess(self, brief: AssessBrief) -> Verdict: ...
    def run_task(self, brief: TaskBrief) -> HarnessResult: ...
    def grade(self, brief: GradeBrief) -> GateResult: ...


def _absent(value) -> bool:
    """A capability is absent when false-y or the sentinel string 'none'."""
    return value in (False, None, "", "none")


def warn_missing_capabilities(adapter: HarnessAdapter, needed: dict) -> list[str]:
    """Warn for every capability a run needs that the adapter lacks.

    ``needed`` maps a capability name to a required value (a truthy bool, or a
    non-'none' structured_output tier). Returns the warning strings and logs
    each; the orchestrator surfaces the gap rather than failing mid-run.
    """
    caps = adapter.capabilities()
    warnings: list[str] = []
    for key, required in needed.items():
        if not required:
            continue
        if _absent(caps.get(key)):
            msg = (f"harness {adapter.name!r} lacks capability {key!r} "
                   f"(needed={required!r}, have={caps.get(key)!r})")
            warnings.append(msg)
            log.warning(msg)
    return warnings
