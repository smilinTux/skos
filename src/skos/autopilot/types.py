"""Autopilot data contracts (spec section 10). Plain dataclasses, no I/O."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkItem:
    kind: str
    ref: str
    source: str
    repo: str | None
    payload: dict


@dataclass
class RepoSpec:                       # one entry of repo_map (autopilot.yaml)
    name: str
    path: str
    base_branch: str
    integration_branch: str
    test_cmd: str
    ci: str                           # "github-actions" | "local:<cmd>" | "none"
    coverage_cmd: str | None = None   # emits Cobertura/lcov; None -> PR-only
    ci_poll_timeout: int = 1200       # seconds to poll github-actions before red
    automerge: bool = False
    auto_revert: bool = False
    min_diff_coverage: float = 0.8
    sandbox_image: str | None = None


@dataclass
class AssessBrief:                    # Phase 0 assess input
    task_id: str
    title: str
    description: str
    acceptance: list[str]
    tags: list[str]
    repo: str | None
    codebase_context: str


@dataclass
class TaskBrief:                      # implement input
    task_id: str
    repo: RepoSpec
    worktree: str
    title: str
    description: str
    acceptance: list[str]
    prior_feedback: str | None
    round: int


@dataclass
class GradeBrief:                     # grade input
    task_id: str
    repo: RepoSpec
    worktree: str
    diff: str
    acceptance: list[str]
    ci_status: str                    # green | red | pending | none
    diff_coverage: float | None       # changed-lines coverage ratio, or None


@dataclass
class GateResult:
    score: int | None
    passed: bool
    notes: str
    artifact: str | None


@dataclass
class Verdict:                        # Phase 0 assess output
    verdict: str                      # valid | stale | obsolete | needs_decision
    reason: str
    updated_description: str | None = None
    updated_acceptance: list[str] | None = None


@dataclass
class HarnessResult:
    ok: bool
    artifact: str | None
    tokens: int
    cost_usd: float
    raw: dict


@dataclass
class DecisionItem:
    qid: str
    prompt: str
    options: dict
    action_ref: str | None
    priority: str
