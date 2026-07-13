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


class StubHarness:
    """Deterministic no-spawn harness for v1 (posture C). Never launches a model.
    assess returns valid so dry-run previews show the candidate pool; run_task and
    grade are never reached in dry-run (Phase 2 is skipped) and hard-raise if called."""
    name = "stub"

    def capabilities(self) -> ProviderCapabilities:
        return {"session_resume": False, "structured_output": "none",
                "sandbox": False, "tool_restrictions": False}

    def assess(self, brief: AssessBrief) -> Verdict:
        return Verdict(verdict="valid", reason="stub")

    def run_task(self, brief: TaskBrief) -> HarnessResult:
        raise RuntimeError("StubHarness cannot execute; live harness is disabled in v1 (posture C)")

    def grade(self, brief: GradeBrief) -> GateResult:
        raise RuntimeError("StubHarness cannot grade; live harness is disabled in v1 (posture C)")


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


HARNESSES: dict = {}


def register_harness(name: str, factory) -> None:
    """Register (or replace) a harness factory under its selection name."""
    HARNESSES[name] = factory


def build_harness(config, name: str | None = None):
    """Construct the selected harness from config. Unknown name fails closed."""
    import skos.autopilot.adapters   # noqa: F401  ensure adapters register
    name = name or getattr(config, "harness", "claude-code")
    factory = HARNESSES.get(name)
    if factory is None:
        raise ValueError(
            f"unknown harness {name!r}; registered: {sorted(HARNESSES)}")
    return factory(config)


register_harness("stub", lambda config: StubHarness())
