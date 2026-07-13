"""External CI verdict + diff-coverage, computed OUTSIDE the harness. The
load-bearing merge control (spec section 5.5)."""
from __future__ import annotations

import json
import subprocess
import time

from .types import RepoSpec

_POLL_INTERVAL = 5  # seconds between gh polls; patched to a no-op sleep in tests

_RED_CONCLUSIONS = {"failure", "cancelled", "timed_out",
                    "action_required", "startup_failure"}


def external_ci_verdict(repo: RepoSpec, pr_branch: str, head_sha: str) -> str:
    """Return green|red|pending|none for the repo's external CI over head_sha.

    github-actions: poll `gh run list` up to ci_poll_timeout, map the run whose
    headSha matches. success -> green; a failing/unknown conclusion -> red; a
    poll timeout -> red. NEVER green on an unknown conclusion or a timeout.
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
    return "red"
