"""Engineering work-type executor: resolve repo, claim + lease, produce in an
isolated worktree, grade to 5/5 behind the external-CI twin gate, finalize.
"""
from __future__ import annotations

from datetime import datetime, timezone

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
