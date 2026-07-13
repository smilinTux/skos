"""The harness-agnostic meta-orchestrator: phases 0-3, dry-run, kill switch,
caps, resume. Engineering executor internals live in engineering.py (Phase E);
here we only wire the phases, routing, decision queue, and guardrails.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .types import WorkItem, AssessBrief, Verdict, GateResult, DecisionItem
from .executor import EXECUTORS
from .config import Caps
from . import journal


@dataclass
class CapLedger:
    """Running token/dollar tally, checked between items."""
    caps: Caps
    tokens: int = 0
    usd: float = 0.0

    def add(self, tokens: int = 0, usd: float = 0.0) -> None:
        self.tokens += int(tokens or 0)
        self.usd += float(usd or 0.0)

    def exceeded(self) -> bool:
        return (self.tokens > self.caps.max_tokens_per_run
                or self.usd > self.caps.max_usd_per_day)


def kill_switch_active(enabled: bool) -> bool:
    """True when the run must stop cleanly: env override or disabled config."""
    if os.environ.get("SKOS_AUTOPILOT_OFF") == "1":
        return True
    return not enabled


def stable_qid(prompt: str, action_ref: str | None) -> str:
    """Deterministic 12-char decision id over (action_ref, prompt)."""
    return hashlib.sha256(f"{action_ref}|{prompt}".encode("utf-8")).hexdigest()[:12]


def load_raw_tasks(tasks_dir) -> list[dict]:
    """Load coord tasks as raw dicts so ``meta`` is visible (spec Phase 0)."""
    d = Path(tasks_dir)
    out: list[dict] = []
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def repo_tag(task: dict) -> str | None:
    """The single ``repo:<name>`` tag, or None when absent/ambiguous."""
    repos = [t.split(":", 1)[1] for t in (task.get("tags") or []) if t.startswith("repo:")]
    return repos[0] if len(repos) == 1 else None


def classify_kind(task: dict) -> str:
    """Route to an executor kind by repo tag then source (skjoule vocab)."""
    if any(t.startswith("repo:") for t in (task.get("tags") or [])):
        return "engineering"
    return {"itil": "ops", "email": "research", "telegram": "research",
            "order": "orders", "calendar": "calendar"}.get(task.get("source", "coord"),
                                                            "engineering")


def _to_workitem(task: dict, *, unblocked: bool = True, verdict: str = "valid") -> WorkItem:
    """Build a WorkItem, enriching the payload with the phase-0 facts the executor's
    selectable/run contract reads (unblocked, verdict) and mapping coord's
    acceptance_criteria onto the `acceptance` key the executor expects."""
    payload = {**task, "unblocked": unblocked, "verdict": verdict,
               "acceptance": task.get("acceptance_criteria") or task.get("acceptance") or []}
    return WorkItem(kind=classify_kind(task), ref=task["id"],
                    source=task.get("source", "coord"), repo=repo_tag(task), payload=payload)


def deepdive_spawn(board, proposals, *, caps: Caps, run_id: str,
                   dry_run: bool = False) -> list[str]:
    """Create new coord tasks from deep-dive proposals, capped and marked
    ``autopilot-untriaged`` so they are never auto-selected (spec section 14)."""
    made: list[str] = []
    for spec in (proposals or [])[: caps.new_tasks_per_run]:
        if dry_run:
            made.append("(dry-run)")
            continue
        made.append(board.create_task(title=spec.get("title", ""),
                                      description=spec.get("description", ""),
                                      tags=["autopilot", "autopilot-untriaged"]))
    return made


def phase0_assess(*, board, harness, tasks_dir, caps: Caps, run_id: str,
                  dry_run: bool = False, codebase_context: str = "",
                  deepdive_proposals=None) -> tuple[list[WorkItem], list[DecisionItem]]:
    """Reclaim stale claims, compute unblocked, assess each candidate, apply the
    verdict (stale rewrite / obsolete close / needs_decision queue), spawn capped
    deep-dive tasks. Returns (candidates, decisions)."""
    if not dry_run:
        board.release_stale_claims("autopilot", 3600)
    by_id = {t.get("id"): t for t in load_raw_tasks(tasks_dir)}
    candidates: list[WorkItem] = []
    decisions: list[DecisionItem] = []
    for tid in sorted(board.unblocked_task_ids()):
        t = by_id.get(tid)
        if not t or t.get("status") in ("completed", "closed", "obsolete"):
            continue
        brief = AssessBrief(task_id=tid, title=t.get("title", ""),
                            description=t.get("description", ""),
                            acceptance=t.get("acceptance_criteria") or [],
                            tags=t.get("tags") or [], repo=repo_tag(t),
                            codebase_context=codebase_context)
        v = harness.assess(brief)
        if v.verdict == "valid":
            candidates.append(_to_workitem(t, verdict="valid"))
        elif v.verdict == "stale":
            if not dry_run:
                board.update_task(tid, description=v.updated_description,
                                  acceptance_criteria=v.updated_acceptance, run_id=run_id)
            candidates.append(_to_workitem(t, verdict="stale"))
        elif v.verdict == "obsolete":
            if not dry_run:
                board.close_task_obsolete(tid, v.reason, run_id=run_id)
        elif v.verdict == "needs_decision":
            decisions.append(DecisionItem(qid=stable_qid(v.reason or tid, tid),
                                          prompt=v.reason or f"Task {tid} needs a decision.",
                                          options={"promote": "promote", "skip": "skip"},
                                          action_ref=tid, priority=t.get("priority") or "high"))
    deepdive_spawn(board, deepdive_proposals, caps=caps, run_id=run_id, dry_run=dry_run)
    return candidates, decisions


def is_untriaged(item: WorkItem) -> bool:
    return "autopilot-untriaged" in (item.payload.get("tags") or [])


def phase1_triage(candidates, harness, *, repo_map, decisions,
                  executors=None) -> list[tuple[WorkItem, object]]:
    """Select unblocked+valid+in-scope items whose executor.selectable is True and
    route them. Non-selectable or decision-shaped items go straight to the decision
    queue (the executor's escalate is NOT called for selectable=False). untriaged
    items are never auto-selected."""
    selected: list[tuple[WorkItem, object]] = []
    repo_map = repo_map or {}
    table = executors if executors is not None else EXECUTORS
    for item in candidates:
        if is_untriaged(item):
            continue
        ex = table.get(item.kind)
        if ex is None:
            decisions.append(DecisionItem(qid=stable_qid(f"no-exec:{item.kind}", item.ref),
                prompt=f"No executor registered for kind '{item.kind}' (task {item.ref}).",
                options={"skip": "skip"}, action_ref=item.ref, priority="medium"))
            continue
        if item.kind == "engineering" and (item.repo is None or item.repo not in repo_map):
            decisions.append(DecisionItem(qid=stable_qid("which-repo", item.ref),
                prompt=f"Task {item.ref} has no known repo:<name>; add to repo_map or route?",
                options={"map": "add-to-repo_map", "skip": "skip"},
                action_ref=item.ref, priority="high"))
            continue
        if ex.selectable(item):
            selected.append((item, ex))
        else:
            decisions.append(DecisionItem(qid=stable_qid("not-selectable", item.ref),
                prompt=f"Task {item.ref} ({item.kind}) is not autonomously actionable; needs you.",
                options={"take": "take", "defer": "defer"},
                action_ref=item.ref, priority="medium"))
    return selected


def phase2_swarm(selected, *, harness, board, caps: Caps, ledger: CapLedger,
                 decisions, run_id: str, state=None, enabled: bool = True) -> dict:
    """Run each routed item's produce-then-grade loop, write each round's score to
    the coord record, finalize cleared items and escalate non-converging ones. The
    token/dollar ceiling is checked between items (spec section 14). v1 is sequential
    (concurrency 1, always <= caps.max_concurrent)."""
    state = dict(state or {})
    for item, ex in selected:                       # concurrency cap: caps.max_concurrent
        if kill_switch_active(enabled):
            break
        if ledger.exceeded():
            decisions.append(DecisionItem(qid=stable_qid("budget-hit", run_id),
                prompt="Autopilot hit its budget ceiling (token/dollar limits); stopped early.",
                options={"ok": "acknowledge"}, action_ref=run_id, priority="high"))
            break
        result = ex.run(item, harness)
        rnd = int((state.get(item.ref, {}).get("round", 0) or 0)) + 1
        ledger.add(getattr(result, "tokens", 0), getattr(result, "cost_usd", 0.0))
        if result.passed:
            ex.finalize(item, result)
            state[item.ref] = {"state": "finalized", "round": rnd, "score": result.score}
        else:
            decisions.append(ex.escalate(item, result.notes))
            state[item.ref] = {"state": "escalated", "round": rnd, "score": result.score}
        journal.write_run(run_id, {"run_id": run_id, "phase": "swarm", "items": state})
    return state


def write_decision(d: DecisionItem) -> str | None:
    """Delegate to digest.queue_decision, the single decision-write path."""
    from . import digest as digest_mod
    return digest_mod.queue_decision(d.prompt, d.options, d.action_ref, d.priority, qid=d.qid)


def _decision_preview(d: DecisionItem) -> dict:
    return {"id": None, "source": "autopilot", "source_ref": f"autopilot:{d.qid}",
            "priority": d.priority, "created_at": "",
            "decision": {"qid": d.qid, "prompt": d.prompt, "options": d.options,
                         "answered": False}}


def phase3_report(decisions, *, dry_run: bool = False, digest_date: str | None = None) -> dict:
    """Build the numbered digest and (unless dry-run) write each decision to GTD and
    persist the manifest. sk-alert SEND is Phase F; this only builds."""
    from . import digest as digest_mod
    digest_date = digest_date or datetime.now(timezone.utc).date().isoformat()
    if dry_run:
        preview = digest_mod.build_manifest([_decision_preview(d) for d in decisions],
                                            digest_date=digest_date)
        return {"dry_run": True, "digest_preview": digest_mod.build_digest_text(preview),
                "decisions": len(decisions)}
    for d in decisions:
        write_decision(d)
    manifest = digest_mod.build_manifest(digest_date=digest_date)
    digest_mod.write_manifest(manifest)
    return {"dry_run": False, "manifest": manifest,
            "digest_text": digest_mod.build_digest_text(manifest)}


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_tasks_dir() -> Path:
    home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
    return home / "coordination" / "tasks"


def build_executors(config, board, run_id: str) -> dict:
    """Assemble this run's executor table: the stub registry plus a fresh
    EngineeringExecutor bound to run_id's journal handle. Per-run by design, so a
    second run in one process never reuses a stale RunHandle. Does not mutate the
    global EXECUTORS."""
    from . import stubs as _stubs        # noqa: F401  import self-registers the stubs
    from . import digest as digest_mod
    from .engineering import EngineeringExecutor
    table = dict(EXECUTORS)
    table["engineering"] = EngineeringExecutor(config, board,
                                               journal.handle(run_id), digest_mod)
    return table


def run_once(*, board, harness, config, tasks_dir=None, run_id=None, dry_run=None,
             ledger: CapLedger | None = None, deepdive_proposals=None,
             executors=None) -> dict:
    """Execute one daily cycle: assess -> triage -> swarm -> report. Journals
    per-item state so a re-run resumes (see the resume task). Guardrails (kill
    switch, caps, dry-run) are layered in the following tasks."""
    run_id = run_id or _new_run_id()
    dry = config.dry_run if dry_run is None else dry_run
    caps = config.caps
    ledger = ledger or CapLedger(caps)

    prior = journal.read_run(run_id) or {}
    state = dict(prior.get("items") or {})
    done = {ref for ref, st in state.items()
            if st.get("state") in ("finalized", "escalated")}

    def _checkpoint(phase: str):
        journal.write_run(run_id, {"run_id": run_id, "phase": phase,
                                   "stopped": "kill_switch", "items": state})
        return {"run_id": run_id, "dry_run": dry, "stopped": "kill_switch"}

    if kill_switch_active(config.enabled):
        return _checkpoint("assess")

    candidates, decisions = phase0_assess(
        board=board, harness=harness, tasks_dir=tasks_dir or _default_tasks_dir(),
        caps=caps, run_id=run_id, dry_run=dry, deepdive_proposals=deepdive_proposals)

    if kill_switch_active(config.enabled):
        return _checkpoint("triage")

    executors = executors if executors is not None else build_executors(config, board, run_id)
    selected = phase1_triage(candidates, harness, repo_map=config.repo_map,
                             decisions=decisions, executors=executors)
    selected = [(it, ex) for it, ex in selected if it.ref not in done]  # resume: skip settled

    if not dry:
        if kill_switch_active(config.enabled):
            return _checkpoint("swarm")
        state = phase2_swarm(selected, harness=harness, board=board, caps=caps,
                             ledger=ledger, decisions=decisions, run_id=run_id,
                             state=state, enabled=config.enabled)

    report = phase3_report(decisions, dry_run=dry)
    journal.write_run(run_id, {"run_id": run_id, "phase": "report", "items": state,
                               "decisions": len(decisions), "dry_run": dry,
                               "preview": report.get("digest_preview") if dry else None})
    return {"run_id": run_id, "dry_run": dry,
            "selected": [it.ref for it, _ in selected],
            "decisions": len(decisions), "report": report}


def run_cli(*, dry_run: bool = True, canary: bool = False, task=None,
           harness: str = "stub") -> dict:
    """CLI bridge for `skos autopilot run`. v1 posture C: only dry-run executes,
    against a StubHarness; canary and live are reported disabled until v1.5."""
    if canary or not dry_run:
        return {"disabled": "posture C: live/canary harness execution is disabled "
                            "in v1 (enable after the v1.5 sovereign sandbox)"}
    from .config import Config
    from .harness import StubHarness
    config = Config.load()
    from skcapstone.coordination import Board
    from skcapstone.mcp_tools._helpers import _shared_root
    board = Board(_shared_root())
    return run_once(board=board, harness=StubHarness(), config=config, dry_run=True)
