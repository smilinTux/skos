"""External CI verdict + diff-coverage, computed OUTSIDE the harness. The
load-bearing merge control (spec section 5.5)."""
from __future__ import annotations

import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .types import RepoSpec

_POLL_INTERVAL = 5  # seconds between gh polls; patched to a no-op sleep in tests

_RED_CONCLUSIONS = {"failure", "cancelled", "timed_out",
                    "action_required", "startup_failure"}


def external_ci_verdict(repo: RepoSpec, pr_branch: str, head_sha: str,
                        worktree: str | None = None) -> str:
    """Return green|red|pending|none for the repo's external CI over head_sha.

    github-actions: poll `gh run list` up to ci_poll_timeout, map the run whose
    headSha matches. success -> green; a failing/unknown conclusion -> red; a
    poll timeout -> red. NEVER green on an unknown conclusion or a timeout.
    local:<cmd> runs in the worktree (where the harness's edits live) so it gates
    the current work, not the base checkout.
    """
    if repo.ci == "none":
        return "none"
    if repo.ci == "github-actions":
        deadline = time.monotonic() + repo.ci_poll_timeout
        while True:
            proc = subprocess.run(
                ["gh", "run", "list", "--branch", pr_branch,
                 "--json", "headSha,databaseId,status,conclusion"],
                cwd=repo.path, capture_output=True, text=True)
            runs = json.loads(proc.stdout or "[]")
            match = next((r for r in runs if r.get("headSha") == head_sha), None)
            if match is not None and match.get("status") == "completed":
                return "green" if match.get("conclusion") == "success" else "red"
            if time.monotonic() >= deadline:
                return "red"
            time.sleep(_POLL_INTERVAL)
    if repo.ci.startswith("local:"):
        cmd = repo.ci[len("local:"):]
        proc = subprocess.run(cmd, shell=True, cwd=(worktree or repo.path),
                              capture_output=True, text=True)
        return "green" if proc.returncode == 0 else "red"
    return "red"


def _changed_lines(diff: str) -> dict[str, set[int]]:
    """Map post-image path -> set of added line numbers from a unified diff."""
    changed: dict[str, set[int]] = {}
    cur: str | None = None
    newno = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            cur = path[2:] if path.startswith("b/") else path
            changed.setdefault(cur, set())
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            newno = int(m.group(1)) if m else 0
        elif cur is not None:
            if line.startswith("+") and not line.startswith("+++"):
                changed[cur].add(newno); newno += 1
            elif line.startswith("-") and not line.startswith("---"):
                continue
            else:
                newno += 1
    return changed


def diff_coverage(repo: RepoSpec, worktree: str, diff: str) -> float | None:
    """Changed-lines coverage ratio, or None when the repo has no coverage_cmd.

    Runs coverage_cmd in the worktree (emitting a Cobertura coverage.xml), then
    scores only the lines the diff added. Computed outside the harness.
    """
    if not repo.coverage_cmd:
        return None
    subprocess.run(repo.coverage_cmd, shell=True, cwd=worktree,
                   capture_output=True, text=True)
    changed = _changed_lines(diff)
    root = ET.parse(str(Path(worktree) / "coverage.xml")).getroot()
    covered = missed = 0
    for cls in root.iter("class"):
        want = changed.get(cls.get("filename"))
        if not want:
            continue
        for ln in cls.iter("line"):
            n = int(ln.get("number"))
            if n in want:
                if int(ln.get("hits", "0")) > 0:
                    covered += 1
                else:
                    missed += 1
    total = covered + missed
    return 1.0 if total == 0 else covered / total
