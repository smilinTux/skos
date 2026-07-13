# SKOS Autopilot v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the SKOS Autopilot v1 slice: the coord scoring/mutation foundations in skcapstone, then the harness-agnostic meta-orchestrator + engineering executor + numbered-digest resolver in skos, runnable as a daily job in read-only dry-run.

**Architecture:** skcapstone gains a `Task.meta` field and three atomic single-writer mutators (`score_task`, `update_task`, and an obsolete-close), surfaced as `coord score` CLI + `coord_score` MCP. skos gains a new `skos/autopilot/` package: a phased orchestrator (assess/triage/swarm/report) that routes coord tasks to an engineering executor, which implements in an isolated git worktree and grades to 5/5 behind an external-CI gate, all behind a swappable `HarnessAdapter` (ClaudeCodeAdapter now) run under a deny-by-default sandbox. Human decisions surface as a numbered digest resolved by one shared `answer()` with a CLI front door.

**Tech Stack:** Python 3.12+, pydantic v2, Typer (skos CLI), pytest (+ pytest-mock), git worktrees, `gh` CLI (external CI), `flock`/`bwrap` (sandbox), skscheduler (`jobs.yaml`), `sk-alert` (Telegram DM), the existing `skos.gtd_ingest` port.

**Design spec:** `docs/skos-autopilot-architecture.md` (graded 5/5). This plan implements its v1 scope (spec section 19). Every task's requirements implicitly include the Global Constraints and Locked Shared Interfaces below.

## Global Constraints

- **Python floor 3.12**; skos uses `src/` layout with `pythonpath=["src"]`, tests in `skos/tests/` (pytest, fixtures in `tests/conftest.py`). skcapstone tests in `skcapstone/tests/test_coordination.py`.
- **No em/en dashes** in any authored string, comment, doc, commit, or user-facing text (Chef hard rule). Regular hyphens only.
- **Build sequence is skcapstone-first:** all Phase A changes land and are `pip install -e`'d on .158 before any skos autopilot code runs, because Phase 0 (unblocked compute) and the engineering executor (scoring) depend on them.
- **Single-node constraint:** all coord task mutation is safe only because the job is pinned to `nodes: [noroc2027]`. Never introduce a second concurrent task-file writer.
- **Sovereign + GPLv3:** reimplement patterns natively (the Archon eval borrows are concepts, not code). No new external product dependency.
- **TDD, frequent commits, DRY, YAGNI.** Every task ends with a passing test and a commit. Commit trailer: `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- **Dry-run is the default runtime** for the first live period: no coord/GTD writes, no merges, no DMs beyond one opt-in summary.

## Locked Shared Interfaces

These signatures are fixed. Every task consumes/produces exactly these names and types (no drift).

**skcapstone `coordination.py` (Phase A):**
```python
class Task(BaseModel):
    ...                                  # existing fields unchanged
    meta: dict = Field(default_factory=dict)          # NEW

class Board:
    def _write_task_raw(self, task_id: str, mutate: Callable[[dict], None]) -> Path: ...
        # locate tasks/<id>-*.json, json.load raw dict, mutate(d) in place,
        # atomic write (tmp + os.replace), return path. THE ONLY task-file mutator helper.
    def score_task(self, task_id: str, round: int, score: int, notes: str = "",
                   harness: str = "", phase: str | None = None, ref: str | None = None) -> Path: ...
    def update_task(self, task_id: str, description: str | None = None,
                    acceptance_criteria: list[str] | None = None,
                    add_tags: list[str] | None = None, run_id: str | None = None) -> Path: ...
    def close_task_obsolete(self, task_id: str, reason: str, run_id: str | None = None) -> Path: ...
    def unblocked_task_ids(self) -> set[str]: ...     # deps subset of union(agents.completed_tasks)
    def release_stale_claims(self, agent: str, older_than_seconds: int) -> list[str]: ...
```
`meta.autopilot` shape: `{phase, pr|artifact, merge:{sha,pr,branch,ts}, harness, scores:[{round,score,notes,ts,harness}], edits:[{field,old,new,ts,run_id}]}`.

**skos `autopilot/types.py` (Phase B)**: exactly as spec section 10: `WorkItem, RepoSpec, AssessBrief, TaskBrief, GradeBrief, GateResult, Verdict, HarnessResult, DecisionItem`. RepoSpec fields: `name, path, base_branch, integration_branch, test_cmd, ci, coverage_cmd=None, ci_poll_timeout=1200, automerge=False, auto_revert=False, min_diff_coverage=0.8`.

**skos `autopilot/harness.py` (Phase C):**
```python
class ProviderCapabilities(TypedDict):
    session_resume: bool; structured_output: str; sandbox: bool; tool_restrictions: bool
class HarnessAdapter(Protocol):
    name: str
    def capabilities(self) -> ProviderCapabilities: ...
    def assess(self, brief: AssessBrief) -> Verdict: ...
    def run_task(self, brief: TaskBrief) -> HarnessResult: ...
    def grade(self, brief: GradeBrief) -> GateResult: ...
```

**skos `autopilot/executor.py` (Phase D):**
```python
class Executor(Protocol):
    kind: str
    def selectable(self, item: WorkItem) -> bool: ...
    def run(self, item: WorkItem, harness: HarnessAdapter) -> GateResult: ...
    def finalize(self, item: WorkItem, result: GateResult) -> None: ...
    def escalate(self, item: WorkItem, reason: str) -> DecisionItem: ...
EXECUTORS: dict[str, Executor]           # registry keyed by kind
```

**skos `autopilot/resolver.py` (Phase F):** `def answer(n: int, response: str | None = None) -> dict: ...`

## File Structure

**skcapstone** (`~/clawd/skcapstone-repos/skcapstone/src/skcapstone/`):
- `coordination.py` (modify): `Task.meta`, `_write_task_raw`, `score_task`, `update_task`, `close_task_obsolete`, `unblocked_task_ids`, `release_stale_claims`.
- `cli/coord.py` (modify): `coord score`.
- `mcp_tools/coord_tools.py` (modify): `coord_score` tool + handler.

**skos** (`~/clawd/skos/src/skos/autopilot/`, new package):
- `types.py`: all dataclasses (Phase B). `config.py`: `autopilot.yaml` + `repo_map` loading (Phase B). `journal.py`: run journal read/write (Phase B).
- `harness.py`: `HarnessAdapter` protocol + capability matrix (Phase C). `claude_code.py`: ClaudeCodeAdapter: argv build, sandbox (confined Bash + pinned egress), injection framing (Phase C).
- `executor.py`: `Executor` protocol + registry (Phase D). `orchestrator.py`: phases 0-3, dry-run (Phase D). `digest.py`: numbered digest build + send (Phase D/F). `resolver.py`: `answer()` (Phase F). `stubs.py`: non-engineering executors (Phase G).
- `engineering.py`: engineering executor: repo resolution, claim+lease, worktree, grade-to-5/5, external-CI gate, finalize, revert (Phase E). `ci.py`: external CI verdict + diff-coverage (Phase E). `runbook_index.py`: deferred to v1.5 (ops), not in this plan.
- `cli.py` (modify existing skos `cli.py`): `skos autopilot {run, answer, list, status, show, revert, send}` (Phase F).
- `capabilities.yaml` (modify): `autopilot` line (Phase G).

**Config/ops:** `~/.skcapstone/config/autopilot.yaml` (new), `~/.skcapstone/config/jobs.yaml` (add `autopilot-daily`, Phase F).
**Docs:** `skos/docs/skos-autopilot-SOP.md` (Phase G), `skcapstone/docs/autopilot-coord-scoring.md` (Phase A).

---

## MASTER PROGRAM SEQUENCE (ground-up, all work)

This plan's detailed tasks cover Phases A-G (Autopilot v1). The full program order, including the archon-liberator leverage (coord epic `49a74ed2`) and the root-dir rename (`84377b3e`), each of which gets its own spec/plan when reached:

1. **Phase A - skcapstone coord foundations** (this plan) - hard prerequisite for everything.
2. **Leverage pull-forward: MCP tool consolidation** (`f346a07b`) - reduces agent mis-selection that Autopilot's harness will hit; do alongside/after A. Its own plan.
3. **Phases B-G - Autopilot v1** (this plan) - orchestrator, engineering executor, digest, dry-run, daily job.
4. **Leverage feeders into Autopilot v1.5:** worktree-as-primitive (`aba4f81a`), diverse-lens grader panel (`1e10db2e`), skgateway structured-output tiering (`36a2a047`). Fold into v1.5 executors.
5. **Autopilot v1.5 - ops / self-triaging ITIL executor** (own plan; spec section 6).
6. **Leverage standalone subsystems** (own spec+plan each, any order by value): skmem-pg RAG strategies (`650b2550`), smart_crawl (`0f4418f7`), multi-surface bridge fabric (`93fc5449`), process-as-code workflow DSL (`3b53e2d1`, decision: reimplement sovereign GPLv3), Mission Control dashboard (`05a85ba9`), telemetry (`83c5e737`).
7. **Autopilot v2** - GTD-inbox-wide triage. **v3** - research/comms executors + continuous local-model mode.
8. **Root-dir rename `~/clawd` -> `~/skd`** (`84377b3e`) - audit-first, symlink-bridge; schedule when a low-churn window opens (touches every path in this plan, so do it either before serious building or well after v1 stabilizes, not mid-build).

---

## Autopilot v1 detailed tasks

The bite-sized TDD tasks for Phases A through G follow. Each task: exact files, interfaces consumed/produced, failing test, verify-fail, minimal implementation, verify-pass, commit.

### Execution note

The 44 per-task TDD blocks (exact files, failing test with real code, verify-fail, minimal implementation, verify-pass, commit) were drafted against the Locked Shared Interfaces above and are materialized to each implementing subagent at execution time (subagent-driven-development, one fresh agent per task). They are stored with this plan. Phase H below is authored inline because it is the wiring that makes the independently-drafted phases compose, and it must be read as a whole.

### Task manifest (build order)

**Phase A - skcapstone coord foundations** (`~/clawd/skcapstone-repos/skcapstone`), land + `pip install -e` on .158 first:
- A1 `Task.meta` field (back-compat) · A2 `Board._write_task_raw` atomic single-writer · A3 `Board.score_task` (idempotent) · A4 `Board.update_task` (edit snapshots) · A5 `Board.close_task_obsolete` · A6 `Board.unblocked_task_ids` · A7 `Board.release_stale_claims` (keyed on last_seen) · A8 `coord score` CLI · A9 `coord_score` MCP tool · A10 `docs/autopilot-coord-scoring.md`.

**Phase B - skos scaffolding** (`~/clawd/skos/src/skos/autopilot/`):
- B1 `types.py` (all 9 dataclasses) · B2 `config.py` + `docs/examples/autopilot.yaml` · B3 `journal.py` (`RunJournal`).

**Phase C - harness seam + security:**
- C1 `harness.py` (`HarnessAdapter` protocol + `warn_missing_capabilities`) · C2 `claude_code.py` (`ClaudeCodeAdapter`: deny-by-default firewall, confined Bash, pinned egress, DATA framing, `RUN_HARNESS_IT` integration test).

**Phase D - orchestrator core:**
- D1 `executor.py` (`Executor` protocol + `EXECUTORS` + `register`/`get`) · D2 orchestrator helpers (`Caps`, `CapLedger`, `kill_switch_active`, `stable_qid`) · D3 Phase 0 assess · D4 Phase 1 triage · D5 Phase 2 swarm wrapper · D6 `digest.py` (`build_manifest`/`build_digest_text`/`write_manifest`) · D7 Phase 3 report (`write_decision` + upsert fallback) · D8 `run_once` composition · D9 dry-run read-only lock · D10 kill switch + caps · D11 pause-as-state resume.

**Phase E - engineering executor:**
- E1 `ci.external_ci_verdict` github-actions poll · E2 ci `local:`/`none` · E3 `ci.diff_coverage` (Cobertura over diff) · E4 `EngineeringExecutor` + repo-tag resolution · E5 `selectable` · E6 claim + lease · E7 worktree add/prune · E8 `<promise>` signal helper · E9 grade-to-5/5 Ralph loop + twin gate · E10 finalize automerge/PR · E11 `revert`.

**Phase F - digest/resolver/CLI/job:**
- F1 `resolver.answer(n)` (idempotent) · F2 `digest.send_digest` (sk-alert, dry-run-safe) · F3 `skos autopilot` Typer group · F4 `render_autopilot_job_yaml` + manual install.

**Phase G - stubs/wiring/docs:**
- G1 `stubs.py` (research/comms/orders/calendar, never self-select) · G2 `autopilot` capability line · G3 `skos-autopilot-SOP.md`.

**Phase H - wiring and reconciliation** (authored below): resolves the interface seams between independently-drafted phases and adds the end-to-end smoke test.

---

## Phase H: wiring and reconciliation

The five phase drafts were written against the locked *type* contracts, which agree. The self-review found four *function-signature* seams and one behavioral overlap where drafters diverged. These tasks reconcile them. Land Phase H after B, C, D, E, F, G are individually green, before the daily job is enabled.

### Task H1: unify the journal module API

**Why:** D calls module-level `journal.read_run(run_id)` / `journal.write_run(run_id, data)`; E calls instance-style `journal.record_claim(ref, claimed_at=)` / `journal.worktree_for(ref)`; F's CLI calls `journal.render_list(what)` / `render_status()` / `render_run(run_id)`. B3 built only the `RunJournal` class (`set_item`, `add_score`, ...). Canonicalize journal.py to expose all of it: keep `RunJournal`, add the module functions D and F expect, and a per-run `RunHandle` (bound to a run_id) exposing what E expects.

**Files:** Modify `~/clawd/skos/src/skos/autopilot/journal.py`; Test `~/clawd/skos/tests/test_autopilot_journal_api.py` (new).

**Interfaces produced (canonical):**
- `read_run(run_id: str) -> dict` (returns `{}` if absent).
- `write_run(run_id: str, data: dict) -> None` (atomic tmp + os.replace).
- `render_list(what: str) -> list[str]`, `render_status() -> str`, `render_run(run_id: str) -> str`.
- `handle(run_id: str) -> RunHandle` where `RunHandle.record_claim(ref, claimed_at)`, `RunHandle.worktree_for(ref) -> str | None`, `RunHandle.set_worktree(ref, path)`, `RunHandle.set_item(ref, state, **extra)`. `RunHandle` reads/writes through `read_run`/`write_run` so all three call styles share one on-disk shape (`{run_id, items:{ref:{state, claimed_at, worktree, scores, ...}}, tokens, cost_usd}`).

- [ ] **Step 1: Write the failing test**
```python
# tests/test_autopilot_journal_api.py
import pytest
from skos.autopilot import journal


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_AUTOPILOT_RUNS_DIR", str(tmp_path / "runs"))


def test_read_write_run_roundtrip():
    journal.write_run("r1", {"run_id": "r1", "items": {"t1": {"state": "claimed"}}})
    assert journal.read_run("r1")["items"]["t1"]["state"] == "claimed"
    assert journal.read_run("absent") == {}


def test_handle_record_claim_and_worktree():
    h = journal.handle("r2")
    h.record_claim("t1", claimed_at="2026-07-12T00:00:00Z")
    h.set_worktree("t1", "/wt/t1")
    assert journal.read_run("r2")["items"]["t1"]["claimed_at"] == "2026-07-12T00:00:00Z"
    assert h.worktree_for("t1") == "/wt/t1"
    assert h.worktree_for("nope") is None


def test_render_helpers_do_not_raise():
    journal.write_run("r3", {"run_id": "r3", "phase": "report",
                             "items": {"t1": {"state": "finalized"}}, "decisions": 0})
    assert isinstance(journal.render_status(), str)
    assert isinstance(journal.render_run("r3"), str)
    assert isinstance(journal.render_list("runs"), list)
```
- [ ] **Step 2: Run test to verify it fails**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_journal_api.py -q` -> `AttributeError: module 'skos.autopilot.journal' has no attribute 'read_run'`.
- [ ] **Step 3: Write minimal implementation** (append to `journal.py`)
```python
def read_run(run_id: str) -> dict:
    p = runs_dir() / f"{run_id}.json"
    if not p.exists():
        return {}
    import json
    return json.loads(p.read_text(encoding="utf-8"))


def write_run(run_id: str, data: dict) -> None:
    import json
    p = runs_dir() / f"{run_id}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    os.replace(tmp, p)


class RunHandle:
    """Per-run view used by executors: mutate one run's items through read/write_run."""
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def _mutate(self, ref: str, patch: dict) -> None:
        data = read_run(self.run_id) or {"run_id": self.run_id, "items": {},
                                         "tokens": 0, "cost_usd": 0.0}
        item = data.setdefault("items", {}).setdefault(ref, {})
        item.update(patch)
        item["updated_at"] = _now()
        write_run(self.run_id, data)

    def record_claim(self, ref: str, claimed_at: str) -> None:
        self._mutate(ref, {"state": "claimed", "claimed_at": claimed_at})

    def set_worktree(self, ref: str, path: str) -> None:
        self._mutate(ref, {"worktree": path})

    def worktree_for(self, ref: str) -> str | None:
        return ((read_run(self.run_id).get("items") or {}).get(ref) or {}).get("worktree")

    def set_item(self, ref: str, state: str, **extra) -> None:
        self._mutate(ref, {"state": state, **extra})


def handle(run_id: str) -> RunHandle:
    return RunHandle(run_id)


def render_status() -> str:
    runs = sorted(runs_dir().glob("*.json"))
    if not runs:
        return "no autopilot runs yet"
    latest = read_run(runs[-1].stem)
    items = latest.get("items", {})
    return (f"run {latest.get('run_id')} phase={latest.get('phase')} "
            f"items={len(items)} decisions={latest.get('decisions', 0)} "
            f"dry_run={latest.get('dry_run')}")


def render_run(run_id: str) -> str:
    import json
    return json.dumps(read_run(run_id), indent=2, default=str)


def render_list(what: str) -> list[str]:
    if what == "runs":
        return [p.stem for p in sorted(runs_dir().glob("*.json"))]
    if what == "claims":
        out = []
        for p in sorted(runs_dir().glob("*.json")):
            for ref, it in (read_run(p.stem).get("items") or {}).items():
                if it.get("state") == "claimed":
                    out.append(f"{ref} claimed_at={it.get('claimed_at')}")
        return out
    # decisions live in the digest manifest, not the run journal
    from skos.gtd_ingest import gtd_dir
    import json
    mp = gtd_dir() / "autopilot-digest.json"
    if not mp.exists():
        return []
    return [f"{i['n']}. {i['prompt']}" for i in json.loads(mp.read_text()).get("items", [])]
```
- [ ] **Step 4: Run test to verify it passes**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_journal_api.py tests/test_autopilot_journal.py -q` -> all pass.
- [ ] **Step 5: Commit**
```bash
cd ~/clawd/skos && git add src/skos/autopilot/journal.py tests/test_autopilot_journal_api.py
git commit -m "feat(autopilot): unify journal API (read/write_run, RunHandle, render_*)

Reconciles the module-level (orchestrator), per-run (engineering executor), and
render (CLI) call styles onto one on-disk shape.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task H2: `config.load()` module convenience + `Config.dry_run`/`dry_run_summary`

**Why:** F calls `config.load()` (module fn) but B built `Config.load()` (classmethod); D's `run_once` reads `config.dry_run`; F's `send_digest` reads `config.dry_run_summary`. Add the module wrapper and the two fields.

**Files:** Modify `~/clawd/skos/src/skos/autopilot/config.py`; Test `~/clawd/skos/tests/test_autopilot_config_wrap.py` (new).

**Interfaces produced:** `load(path=None) -> Config`; `Config.dry_run: bool = True` (default read-only); `Config.dry_run_summary: bool = False`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_autopilot_config_wrap.py
from skos.autopilot import config


def test_module_load_returns_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SKOS_AUTOPILOT_CONFIG", raising=False)
    cfg = config.load(tmp_path / "none.yaml")
    assert isinstance(cfg, config.Config)
    assert cfg.dry_run is True            # default is read-only
    assert cfg.dry_run_summary is False
```
- [ ] **Step 2: Run test to verify it fails**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_config_wrap.py -q` -> `AttributeError: module 'skos.autopilot.config' has no attribute 'load'`.
- [ ] **Step 3: Write minimal implementation**
In `config.py`, add the two fields to the `Config` dataclass (`dry_run: bool = True`, `dry_run_summary: bool = False`), read them in `Config.load` (`dry_run=bool(raw.get("dry_run", True))`, `dry_run_summary=bool(raw.get("dry_run_summary", False))`), and append the module fn:
```python
def load(path=None) -> "Config":
    """Module-level convenience so callers can `from skos.autopilot import config;
    config.load()` without touching the classmethod."""
    return Config.load(path)
```
- [ ] **Step 4: Run test to verify it passes**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_config_wrap.py tests/test_autopilot_config.py -q` -> pass.
- [ ] **Step 5: Commit**
```bash
cd ~/clawd/skos && git add src/skos/autopilot/config.py tests/test_autopilot_config_wrap.py
git commit -m "feat(autopilot): config.load() module wrapper + dry_run/dry_run_summary fields

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task H3: `digest.queue_decision` as the single decision-write path

**Why:** E10's `finalize` calls `self.digest.queue_decision(prompt, options, action_ref, priority)`; D7 put the identical GTD-write logic in `orchestrator.write_decision`. Make `digest.queue_decision` the one implementation and have `orchestrator.write_decision` delegate to it (no circular import: digest does not import orchestrator).

**Files:** Modify `~/clawd/skos/src/skos/autopilot/digest.py` and `orchestrator.py`; Test `~/clawd/skos/tests/test_autopilot_queue_decision.py` (new).

**Interfaces produced:** `digest.queue_decision(prompt, options, action_ref, priority='high', qid=None) -> str | None` (builds the `DecisionItem`, captures with the upsert-on-None fallback, source `autopilot`). `orchestrator.write_decision(d)` becomes `return digest.queue_decision(d.prompt, d.options, d.action_ref, d.priority, qid=d.qid)`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_autopilot_queue_decision.py
import json
import pytest
from skos.autopilot import digest


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))


def test_queue_decision_writes_source_autopilot():
    from skos.gtd_ingest import gtd_dir
    digest.queue_decision("Merge PR?", {"yes": "y"}, "task-x", "high", qid="q1")
    items = json.loads((gtd_dir() / "waiting-for.json").read_text())
    assert items[0]["source"] == "autopilot" and items[0]["source_ref"] == "autopilot:q1"


def test_queue_decision_upsert_fallback(monkeypatch):
    import skos.gtd_ingest as gi
    monkeypatch.setattr(gi, "capture", lambda c: None)              # simulate dup
    called = {}
    monkeypatch.setattr(gi, "upsert", lambda c: called.setdefault("u", ("id", "unchanged")))
    out = digest.queue_decision("P", {}, "t", "high", qid="q2")
    assert called["u"][0] == "id"
```
- [ ] **Step 2: Run test to verify it fails**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_queue_decision.py -q` -> `AttributeError: ... 'queue_decision'`.
- [ ] **Step 3: Write minimal implementation** (append to `digest.py`)
```python
def queue_decision(prompt: str, options: dict, action_ref: str | None,
                   priority: str = "high", qid: str | None = None) -> str | None:
    """Write one decision to GTD via the gtd_ingest port (source='autopilot').
    capture() returns None on a duplicate (source, source_ref); fall back to
    upsert() so the resolver's source_ref always resolves (spec section 9.1).
    The single decision-write path, called by both the orchestrator and the
    engineering executor's finalize."""
    import hashlib
    from skos import gtd_ingest
    qid = qid or hashlib.sha256(f"{action_ref}|{prompt}".encode()).hexdigest()[:12]
    c = gtd_ingest.GtdCapture(
        text=prompt, source="autopilot", source_ref=f"autopilot:{qid}",
        status="waiting", context="@decide", priority=priority or "high",
        meta={"decision": {"qid": qid, "prompt": prompt, "options": options,
                           "answered": False, "answer": None, "action_ref": action_ref}})
    gid = gtd_ingest.capture(c)
    if gid is None:
        gid, _ = gtd_ingest.upsert(c)
    return gid
```
Then edit `orchestrator.write_decision` to delegate:
```python
def write_decision(d):
    from . import digest as digest_mod
    return digest_mod.queue_decision(d.prompt, d.options, d.action_ref, d.priority, qid=d.qid)
```
- [ ] **Step 4: Run test to verify it passes**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_queue_decision.py tests/test_autopilot_orchestrator.py -k "phase3 or decision" -q` -> pass.
- [ ] **Step 5: Commit**
```bash
cd ~/clawd/skos && git add src/skos/autopilot/digest.py src/skos/autopilot/orchestrator.py tests/test_autopilot_queue_decision.py
git commit -m "refactor(autopilot): single decision-write path digest.queue_decision (orchestrator + executor share it)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task H4: `engineering.revert(task_id)` one-arg convenience + executor scoring ownership

**Why:** F's CLI calls `engineering.revert(task_id)` (one arg); E11 defined `revert(board, config, task_id, agent)`. Add the one-arg module convenience that loads Board + Config. Also resolve the double-scoring overlap: E9's `run()` writes a `score_task` per grade round (correct); D5's `phase2_swarm` also wrote one `score_task` per item. The executor owns per-round scoring, so `phase2_swarm` drops its `score_task` call and only journals state.

**Files:** Modify `~/clawd/skos/src/skos/autopilot/engineering.py` and `orchestrator.py`; Test `~/clawd/skos/tests/test_autopilot_revert_wrap.py` (new) + edit `test_autopilot_orchestrator.py`.

**Interfaces produced:** `engineering.revert(task_id: str) -> dict` (loads `Board(_shared_root())` + `config.load()`, delegates to the E11 `_revert_impl`). `phase2_swarm` no longer calls `board.score_task`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_autopilot_revert_wrap.py
from unittest.mock import MagicMock
from skos.autopilot import engineering


def test_revert_one_arg_loads_board_and_config(monkeypatch):
    impl = MagicMock(return_value={"reverted": "sha1"})
    monkeypatch.setattr(engineering, "_revert_impl", impl)
    monkeypatch.setattr(engineering, "_load_board", lambda: "BOARD")
    monkeypatch.setattr(engineering, "_load_config", lambda: "CFG")
    out = engineering.revert("t1")
    impl.assert_called_once_with("BOARD", "CFG", "t1")
    assert out == {"reverted": "sha1"}
```
Also edit `test_autopilot_orchestrator.py::test_phase2_finalizes_and_scores_on_pass`: replace `board.score_task.assert_called_once()` / score assertion with `board.score_task.assert_not_called()` (the executor, not the wrapper, scores).
- [ ] **Step 2: Run test to verify it fails**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_revert_wrap.py -q` -> the E11 `revert` takes 3 required args, so the one-arg call errors / `_revert_impl` absent.
- [ ] **Step 3: Write minimal implementation**
In `engineering.py`: rename the E11 `revert(board, config, task_id, agent="autopilot")` to `_revert_impl(...)`, add helpers and the one-arg wrapper:
```python
def _load_board():
    from skcapstone.mcp_tools._helpers import _shared_root  # same root the CLI/MCP use
    from skcapstone.coordination import Board
    return Board(_shared_root())

def _load_config():
    from skos.autopilot import config
    return config.load()

def revert(task_id: str, agent: str = "autopilot") -> dict:
    """One-arg convenience for the CLI: load Board + Config, delegate to _revert_impl."""
    return _revert_impl(_load_board(), _load_config(), task_id, agent)
```
In `orchestrator.py` `phase2_swarm`: delete the `board.score_task(...)` call (the executor's `run()` already scored each round); keep the `journal`/state write.
- [ ] **Step 4: Run test to verify it passes**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_revert_wrap.py tests/test_autopilot_orchestrator.py tests/test_autopilot_engineering.py -q` -> pass.
- [ ] **Step 5: Commit**
```bash
cd ~/clawd/skos && git add src/skos/autopilot/engineering.py src/skos/autopilot/orchestrator.py tests/test_autopilot_revert_wrap.py tests/test_autopilot_orchestrator.py
git commit -m "fix(autopilot): revert(task_id) convenience + executor owns per-round scoring

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task H5: build + register the engineering executor in `run_once`

**Why:** D's `run_once` routes via `EXECUTORS.get(kind)`, and G1's stubs self-register at import, but nothing instantiates the stateful `EngineeringExecutor` (it needs `config`, `board`, a per-run journal handle, and the digest module). Build and register it at the top of `run_once`, passing `digest` so `finalize`'s `self.digest.queue_decision` resolves.

**Files:** Modify `~/clawd/skos/src/skos/autopilot/orchestrator.py`; Test edit `test_autopilot_orchestrator.py`.

**Interfaces produced:** `build_executors(config, board, run_id) -> None` registering `EngineeringExecutor(config, board, journal.handle(run_id), digest=digest_module)` into `EXECUTORS`; `run_once` calls it after computing `run_id`. (Tests that inject a fake `_RunExec` still pass because they pre-populate `EXECUTORS["engineering"]`; `build_executors` is skipped when a test double is already registered via a `skip_build` flag, or the test sets `config.build_executors=False`.)

- [ ] **Step 1: Write the failing test**
```python
def test_build_executors_registers_engineering(monkeypatch):
    from skos.autopilot import orchestrator as o
    from skos.autopilot.executor import EXECUTORS
    EXECUTORS.pop("engineering", None)
    o.build_executors(config=_config(), board=MagicMock(), run_id="rb")
    assert EXECUTORS["engineering"].kind == "engineering"
```
- [ ] **Step 2: Run test to verify it fails**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_orchestrator.py -k build_executors -q` -> `AttributeError: ... 'build_executors'`.
- [ ] **Step 3: Write minimal implementation** (in `orchestrator.py`)
```python
def build_executors(*, config, board, run_id: str) -> None:
    """Instantiate stateful executors for this run and register them. Stubs
    self-register at import; the engineering executor needs run-scoped deps."""
    from .engineering import EngineeringExecutor
    from . import digest as digest_module
    from .executor import register
    from . import journal
    register(EngineeringExecutor(config, board, journal.handle(run_id),
                                 digest=digest_module))
```
In `run_once`, after `run_id = run_id or _new_run_id()` and before Phase 0, call `build_executors(config=config, board=board, run_id=run_id)` unless `getattr(config, "skip_build", False)`. Add `skip_build: bool = False` to the test `_config()` helper default and set it True in the fake-executor tests (or guard on `"engineering" in EXECUTORS`).
- [ ] **Step 4: Run test to verify it passes**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_orchestrator.py -q` -> pass.
- [ ] **Step 5: Commit**
```bash
cd ~/clawd/skos && git add src/skos/autopilot/orchestrator.py tests/test_autopilot_orchestrator.py
git commit -m "feat(autopilot): build_executors wires the engineering executor into run_once

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

### Task H6: end-to-end dry-run smoke test (real modules, fake harness)

**Why:** every prior test mocks at a boundary. This one exercises the whole composed pipeline in `--dry-run` against the real orchestrator/executor/digest/journal/config modules (only the harness and the coord Board are doubled), asserting the read-only contract holds across the real wiring.

**Files:** Test `~/clawd/skos/tests/test_autopilot_e2e_dryrun.py` (new).

- [ ] **Step 1: Write the failing test**
```python
# tests/test_autopilot_e2e_dryrun.py
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from skos.autopilot import orchestrator as orch, config as cfgmod
from skos.autopilot.types import Verdict, RepoSpec


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv("SK_AUTOPILOT_RUNS_DIR", str(tmp_path / "runs"))


def _task(d, tid):
    (d / f"{tid}-x.json").write_text(json.dumps(
        {"id": tid, "title": tid, "description": "", "tags": ["repo:skos"],
         "acceptance_criteria": ["x"], "dependencies": [], "status": "open"}))


def test_e2e_dry_run_writes_nothing_but_previews(tmp_path, monkeypatch):
    tdir = tmp_path / "tasks"; tdir.mkdir(); _task(tdir, "t-1")
    board = MagicMock(); board.unblocked_task_ids.return_value = {"t-1"}
    harness = SimpleNamespace(name="fake",
        assess=lambda b: Verdict(verdict="needs_decision", reason="pick repo"))
    cfg = cfgmod.Config(enabled=True, dry_run=True,
                        repo_map={"skos": RepoSpec("skos", "/x", "main", "ap", "pytest", "none")})
    import skos.gtd_ingest as gi
    cap = MagicMock(); monkeypatch.setattr(gi, "capture", cap)

    out = orch.run_once(board=board, harness=harness, config=cfg,
                        tasks_dir=tdir, run_id="rE2E")

    board.update_task.assert_not_called()
    board.close_task_obsolete.assert_not_called()
    board.score_task.assert_not_called()
    cap.assert_not_called()                                  # no GTD write in dry-run
    assert out["dry_run"] is True
    assert "digest_preview" in out["report"] and "pick repo" in out["report"]["digest_preview"]
```
- [ ] **Step 2: Run test to verify it fails**
`cd ~/clawd/skos && python -m pytest tests/test_autopilot_e2e_dryrun.py -q` -> fails until H1-H5 have landed and `Config` carries `dry_run` (it wires `build_executors`, `journal`, `digest` together). If red, fix the specific seam it surfaces.
- [ ] **Step 3: Write minimal implementation**
No new product code: this test is the acceptance gate for Phase H. If it fails, the failure names the remaining seam (most likely a `Config` field or a `build_executors` guard); fix that module, do not weaken the test.
- [ ] **Step 4: Run the whole suite**
`cd ~/clawd/skos && python -m pytest tests/ -q` and `cd ~/clawd/skcapstone-repos/skcapstone && python -m pytest tests/test_coordination.py tests/test_cli_coord_score.py tests/test_coord_score_mcp.py -q` -> all green.
- [ ] **Step 5: Commit**
```bash
cd ~/clawd/skos && git add tests/test_autopilot_e2e_dryrun.py
git commit -m "test(autopilot): end-to-end dry-run smoke over the real composed pipeline

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-review (writing-plans checklist)

**1. Spec coverage.** Every v1 scope item in spec section 19 maps to a task: coord scoring extension -> A1-A9; meta-orchestrator core -> D1-D11; engineering executor end-to-end (repo-map, briefs, grade loop, external-CI gate, revert) -> E1-E11 + B1; harness security model -> C1-C2; numbered digest + CLI resolver -> D6/D7/F1/F2/F3; daily job -> F4; stubs -> G1; dry-run/canary/live promotion -> D9 + F3 (`--dry-run`/`--canary`) + SOP G3; kill switch/budget/board-flood -> D2/D10/D3. Adopted Archon patterns (spec section 21): fresh-context Ralph loop + `<promise>` + twin gate -> E8/E9; pause-as-state resume -> D11; typed sidecars -> covered by the worktree artifact dir (E7) and journal (B3/H1); capability matrix -> C1. No uncovered requirement.

**2. Placeholder scan.** No "TBD"/"add error handling"/"similar to"; every code step carries real code. D9/D11 steps 2-3 that say "if green already, ..." are verify-then-lock steps on already-implemented behavior with a concrete assertion, not placeholders.

**3. Type consistency (the reconciliation).** The locked type contracts (section 10) agree across all phases (verified: `RepoSpec`, `GateResult`, `Verdict`, `DecisionItem`, `WorkItem`, `TaskBrief`, `GradeBrief`, `HarnessResult` used identically in B/C/D/E/F). The four function-signature seams and one behavioral overlap are resolved in Phase H: journal API (H1), `config.load()` + `dry_run` fields (H2), `digest.queue_decision` single write path (H3), `engineering.revert(task_id)` + executor-owns-scoring (H4), engineering executor construction/registration (H5), and the end-to-end gate (H6). After Phase H the pipeline composes with one on-disk journal shape, one decision-write path, one scoring owner, and one config entry point.

