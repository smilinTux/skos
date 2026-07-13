"""Engineering work-type executor: resolve repo, claim + lease, produce in an
isolated worktree, grade to 5/5 behind the external-CI twin gate, finalize.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .types import GateResult, GradeBrief, RepoSpec, TaskBrief, WorkItem


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
