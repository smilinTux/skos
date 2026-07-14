"""Engineering work-type executor: resolve repo, claim + lease, produce in an
isolated worktree, grade to 5/5 behind the external-CI twin gate, finalize.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .ci import external_ci_verdict, diff_coverage
from .types import DecisionItem, GateResult, GradeBrief, RepoSpec, TaskBrief, WorkItem


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_PROMISE = re.compile(r"<promise>\s*([A-Z_]+)\s*</promise>")


def parse_promise(text: str | None) -> str | None:
    """Return the SIGNAL inside a <promise>SIGNAL</promise> tag, else None."""
    m = _PROMISE.search(text or "")
    return m.group(1) if m else None


def strip_promise(text: str | None) -> str:
    """Remove any promise tag(s) and trim, for display / feedback carry-forward."""
    return _PROMISE.sub("", text or "").strip()


def is_complete(text: str | None, signal: str = "COMPLETE") -> bool:
    """True only for a real promise tag carrying exactly `signal`."""
    return parse_promise(text) == signal


class EngineeringExecutor:
    kind = "engineering"

    def __init__(self, config, board, journal, digest=None) -> None:
        self.config = config
        self.board = board
        self.journal = journal
        self.digest = digest

    def _repo_names(self, item: WorkItem) -> list[str]:
        return [t.split(":", 1)[1] for t in item.payload.get("tags", [])
                if t.startswith("repo:")]

    def resolve_repo(self, item: WorkItem) -> RepoSpec | None:
        names = self._repo_names(item)
        if len(names) != 1:
            return None
        return self.config.repo_map.get(names[0])

    def selectable(self, item: WorkItem) -> bool:
        p = item.payload
        tags = p.get("tags", [])
        if not p.get("unblocked"):
            return False
        if p.get("verdict") != "valid":
            return False
        if "autopilot-untriaged" in tags:
            return False
        if self.resolve_repo(item) is None:   # also enforces exactly-one-known
            return False
        if not (p.get("acceptance") or p.get("deliverable")):
            return False
        return True

    def claim(self, item: WorkItem) -> None:
        """Claim the coord task before any work (a second runtime cannot double-
        execute), then record the lease start so a crash is reclaimable."""
        self.board.claim_task("autopilot", item.ref)
        self.journal.record_claim(item.ref, claimed_at=_now_iso())

    def _worktree_path(self, item: WorkItem, repo: RepoSpec) -> str:
        base = Path(repo.path)
        return str(base.parent / f"{base.name}-wt" / item.ref)

    def make_worktree(self, item: WorkItem, repo: RepoSpec) -> str:
        wt = self._worktree_path(item, repo)
        try:
            Path(wt).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        branch = f"autopilot/{item.ref}"
        subprocess.run(["git", "-C", repo.path, "worktree", "add", "-b",
                        branch, wt, repo.base_branch],
                       check=True, capture_output=True, text=True)
        return wt

    def prune_worktree(self, repo: RepoSpec, wt: str) -> None:
        subprocess.run(["git", "-C", repo.path, "worktree", "remove", "--force", wt],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", repo.path, "worktree", "prune"],
                       capture_output=True, text=True)

    _MAX_ROUNDS = 4

    def _stage_work(self, wt: str) -> None:
        """Stage the harness's edits INCLUDING new/untracked files, minus CI/coverage
        byproducts. The harness writes new files (e.g. fresh test files) but never
        `git add`s them; a plain `git diff` omits untracked files, so the grade would
        see 'no tests present', and scoped CI + diff-coverage would never run the new
        tests (coverage reads ~0 on the new source). The twin gate then can NEVER pass
        a correct TDD change. Staging first is what makes new tests visible to all
        three gate arms."""
        subprocess.run(["git", "-C", wt, "add", "-A"], capture_output=True, text=True)
        subprocess.run(["git", "-C", wt, "reset", "-q", "--",
                        "coverage.xml", ".coverage", ".pytest_cache",
                        ":(glob)**/__pycache__/**", ":(glob)**/*.pyc"],
                       capture_output=True, text=True)

    def _diff(self, repo: RepoSpec, wt: str) -> str:
        # Stage first so untracked new files (fresh test files!) appear in the diff;
        # `--cached` then diffs the full staged worktree against base.
        self._stage_work(wt)
        proc = subprocess.run(["git", "-C", wt, "diff", "--cached", repo.base_branch],
                              capture_output=True, text=True)
        return proc.stdout

    def _head_sha(self, wt: str) -> str:
        proc = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        return proc.stdout.strip()

    def escalate(self, item: WorkItem, reason: str) -> DecisionItem:
        """Queue a decision for a non-converging item (mirrors the stub shape)."""
        qid = hashlib.sha1(f"engineering:{item.ref}:{reason}".encode()).hexdigest()[:12]
        return DecisionItem(qid=qid,
                            prompt=f"Engineering task {item.ref} did not converge: {reason}",
                            options={"take": "take", "defer": "defer"},
                            action_ref=item.ref, priority="high")

    def run(self, item: WorkItem, harness) -> GateResult:
        self.claim(item)                    # claim before any work: no double-execution
        repo = self.resolve_repo(item)
        p = item.payload
        wt = self.make_worktree(item, repo)
        self.journal.set_worktree(item.ref, wt)   # so finalize() can find this worktree
        pr_branch = f"autopilot/{item.ref}"
        feedback: str | None = None
        last: GateResult | None = None
        for rnd in range(1, self._MAX_ROUNDS + 1):
            # Ralph: a FRESH harness session that re-reads disk state each round.
            tb = TaskBrief(task_id=item.ref, repo=repo, worktree=wt,
                           title=p.get("title", ""), description=p.get("description", ""),
                           acceptance=p.get("acceptance", []),
                           prior_feedback=feedback, round=rnd)
            harness.run_task(tb)
            diff = self._diff(repo, wt)
            ci_status = external_ci_verdict(repo, pr_branch, self._head_sha(wt),
                                            worktree=wt, diff=diff)
            cov = diff_coverage(repo, wt, diff)
            gb = GradeBrief(task_id=item.ref, repo=repo, worktree=wt, diff=diff,
                            acceptance=p.get("acceptance", []),
                            ci_status=ci_status, diff_coverage=cov)
            gr = harness.grade(gb)              # fresh, no shared context with run_task
            self.board.score_task(item.ref, round=rnd, score=(gr.score or 0),
                                  notes=strip_promise(gr.notes), harness=harness.name)
            last = gr
            cov_ok = cov is not None and cov >= repo.min_diff_coverage
            # deterministic twin gate: LLM 5/5 + promise ANDed with CI green + coverage
            if (gr.score == 5 and is_complete(gr.notes)
                    and ci_status == "green" and cov_ok):
                return GateResult(score=5, passed=True,
                                  notes=strip_promise(gr.notes), artifact=gr.artifact)
            feedback = strip_promise(gr.notes)
        return GateResult(score=(last.score if last else None), passed=False,
                          notes=f"did not converge in {self._MAX_ROUNDS} rounds: "
                                f"{strip_promise(last.notes) if last else ''}",
                          artifact=(last.artifact if last else None))

    def _merge(self, repo: RepoSpec, pr_branch: str) -> str:
        subprocess.run(["git", "-C", repo.path, "checkout", repo.integration_branch],
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", repo.path, "merge", "--no-ff", pr_branch],
                       check=True, capture_output=True, text=True)
        proc = subprocess.run(["git", "-C", repo.path, "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        return proc.stdout.strip()

    def _commit_and_push(self, repo: RepoSpec, wt: str, pr_branch: str, item: WorkItem) -> None:
        """Commit the harness's worktree edits onto pr_branch and push. The harness
        edits the worktree but does not commit, so a PR/merge would otherwise have
        no commits. Push is best-effort (a repo with no origin stays local)."""
        # same staging as the gate (new files in, CI/coverage byproducts out)
        self._stage_work(wt)
        title = item.payload.get("title", item.ref)
        subprocess.run(["git", "-C", wt, "commit", "-m", f"autopilot: {title}"],
                       capture_output=True, text=True)          # no-op commit tolerated
        subprocess.run(["git", "-C", wt, "push", "-u", "origin", pr_branch],
                       capture_output=True, text=True)          # best-effort (needs origin)

    def _open_pr(self, repo: RepoSpec, pr_branch: str, item: WorkItem) -> str:
        proc = subprocess.run(
            ["gh", "pr", "create", "--head", pr_branch, "--base", repo.integration_branch,
             "--title", f"autopilot: {item.payload.get('title', item.ref)}",
             "--body", f"Autopilot task {item.ref}"],
            cwd=repo.path, capture_output=True, text=True)
        return proc.stdout.strip()

    def finalize(self, item: WorkItem, result: GateResult) -> None:
        repo = self.resolve_repo(item)
        wt = self.journal.worktree_for(item.ref)
        pr_branch = f"autopilot/{item.ref}"
        self._commit_and_push(repo, wt, pr_branch, item)   # harness edits are uncommitted
        ci_status = external_ci_verdict(repo, pr_branch, self._head_sha(wt), worktree=wt)
        automerge = (repo.name in self.config.automerge_repos
                     and repo.ci != "none" and ci_status == "green"
                     and result.passed and repo.automerge)
        if automerge:
            sha = self._merge(repo, pr_branch)
            merge = {"sha": sha, "pr": None, "branch": pr_branch, "ts": _now_iso()}
            self.board._write_task_raw(
                item.ref,
                lambda d: d.setdefault("meta", {}).setdefault("autopilot", {})
                          .__setitem__("merge", merge))
            self.board.complete_task("autopilot", item.ref)
            self.prune_worktree(repo, wt)
        else:
            pr_url = self._open_pr(repo, pr_branch, item)
            self.digest.queue_decision(
                prompt=f"Merge PR {pr_url} for task {item.ref}?",
                options={"yes": "merge", "no": "close", "defer": "later"},
                action_ref=f"merge:{item.ref}", priority="high")
            # leave the task claimed (not completed) until the operator approves


def _revert_impl(board, config, task_id: str, agent: str = "autopilot") -> dict:
    """Revert the recorded merge commit and reopen the coord task.

    Governance: autopilot reverts only a merge it recorded (meta.autopilot.merge).
    """
    task = next((t for t in board.load_tasks() if t.id == task_id), None)
    if task is None:
        raise ValueError(f"unknown task {task_id}")
    merge = (task.meta or {}).get("autopilot", {}).get("merge")
    if not merge or not merge.get("sha"):
        raise ValueError(f"no recorded merge for {task_id}")
    name = next((t.split(":", 1)[1] for t in task.tags if t.startswith("repo:")), None)
    repo = config.repo_map[name]
    subprocess.run(["git", "-C", repo.path, "checkout", repo.integration_branch],
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", repo.path, "revert", "--no-edit", merge["sha"]],
                   check=True, capture_output=True, text=True)
    af = board.load_agent(agent)
    if af is not None and task_id in af.completed_tasks:
        af.completed_tasks.remove(task_id)     # reopen: undo the completion
        board.save_agent(af)
    board._write_task_raw(
        task_id,
        lambda d: d.setdefault("meta", {}).setdefault("autopilot", {})
                  .__setitem__("reverted", {"sha": merge["sha"], "ts": _now_iso()}))
    return {"task_id": task_id, "reverted_sha": merge["sha"], "reopened": True}


def _load_board():
    from skcapstone.coordination import Board
    from skcapstone.mcp_tools._helpers import _shared_root
    return Board(_shared_root())


def _load_config():
    from skos.autopilot import config
    return config.load()


def revert(task_id: str, agent: str = "autopilot") -> dict:
    """One-arg convenience for the CLI: load Board + Config, delegate to _revert_impl."""
    return _revert_impl(_load_board(), _load_config(), task_id, agent)
