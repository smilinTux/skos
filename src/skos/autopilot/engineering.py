"""Engineering work-type executor: resolve repo, claim + lease, produce in an
isolated worktree, grade to 5/5 behind the external-CI twin gate, finalize.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .ci import external_ci_verdict, diff_coverage
from .types import GateResult, GradeBrief, RepoSpec, TaskBrief, WorkItem


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

    def _diff(self, repo: RepoSpec, wt: str) -> str:
        proc = subprocess.run(["git", "-C", wt, "diff", repo.base_branch],
                              capture_output=True, text=True)
        return proc.stdout

    def _head_sha(self, wt: str) -> str:
        proc = subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        return proc.stdout.strip()

    def run(self, item: WorkItem, harness) -> GateResult:
        repo = self.resolve_repo(item)
        p = item.payload
        wt = self.make_worktree(item, repo)
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
            ci_status = external_ci_verdict(repo, pr_branch, self._head_sha(wt))
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
        ci_status = external_ci_verdict(repo, pr_branch, self._head_sha(wt))
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
