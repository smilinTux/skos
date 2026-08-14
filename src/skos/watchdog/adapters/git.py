"""git: read-only adapter over configured local git checkouts and their PRs.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.3: "merges
to main, open PRs aging, CI failures". "Configured repos" is
``SK_WATCHDOG_GIT_REPOS``: a comma-separated list of ``name=/abs/path``
entries (a bare ``/abs/path`` is also accepted; the name defaults to the
path's basename). Unset means no repos configured, which in Phase 1 is a
quiet default (zero events), not a broken source: nothing has been wired up
yet is not the same as something being unreachable. WD-4 (schedule cutover)
is expected to set this in the job's env.

Two independent reads per repo, each fails safe on its own so one bad repo
never blanks the others:

  ``git log``     merges to the repo's main/master branch within the window,
                   via a plain ``git -C <path> log`` subprocess. Tested
                   against a throwaway ``git init`` fixture repo, never a
                   live checkout.
  ``gh pr list``  open PRs, aging + CI status, via an INJECTABLE
                   ``pr_lister`` callable so tests never invoke the real
                   ``gh`` CLI or network. Defaults to the real ``gh`` binary
                   when present on PATH; silently skipped (no event, not
                   unavailable) when ``gh`` is absent, since PR/CI narration
                   is additive on top of the git-log core the spec leads
                   with.

A repo whose path is not a git checkout, or whose ``git log`` call itself
fails, degrades to one ``source_unavailable(f"git:{name}", ...)`` line for
that repo (never a raise, never blanking the other configured repos); an
uncaught exception anywhere else in ``collect()`` still propagates to
``collect_safe``, which degrades the WHOLE ``git`` source the same way.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, registry

#: An open PR older than this many days is narrated as aging.
AGING_PR_DAYS = 7

#: Separates git log fields; %x1f is the ASCII unit separator, never
#: legitimately present in a commit subject, so splitting on it is safe.
_LOG_FORMAT = "%H%x1f%cI%x1f%s"


def _configured_repos() -> list[tuple[str, Path]]:
    raw = os.environ.get("SK_WATCHDOG_GIT_REPOS", "")
    out: list[tuple[str, Path]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            name, path = chunk.split("=", 1)
        else:
            name, path = Path(chunk).name, chunk
        out.append((name.strip(), Path(path.strip()).expanduser()))
    return out


def _run(cmd: list[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=30)


def _main_branch(path: Path, runner: Callable) -> Optional[str]:
    for candidate in ("main", "master"):
        r = runner(["git", "-C", str(path), "rev-parse", "--verify", candidate])
        if r.returncode == 0:
            return candidate
    return None


def _remote_web_url(path: Path, runner: Callable) -> Optional[str]:
    r = runner(["git", "-C", str(path), "remote", "get-url", "origin"])
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    if url.endswith(".git"):
        url = url[:-4]
    return url if url.startswith("https://github.com/") else None


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


@registry.register
class GitAdapter(WatchdogSourceAdapter):
    name = "git"

    def __init__(self, *, runner: Optional[Callable] = None,
                 pr_lister: Optional[Callable[[str, Path], list[dict]]] = None):
        self._runner = runner or _run
        self._pr_lister = pr_lister  # None = resolve the real `gh` at call time

    def collect(self, window: Window) -> list[WatchdogEvent]:
        out: list[WatchdogEvent] = []
        for name, path in _configured_repos():
            out.extend(self._repo_events(name, path, window))
        return out

    def _repo_events(self, name: str, path: Path, window: Window) -> list[WatchdogEvent]:
        if not (path / ".git").exists():
            return [source_unavailable(f"{self.name}:{name}", ts=window.until,
                                        error=f"{path} is not a git checkout")]
        out = self._merge_events(name, path, window)
        out.extend(self._pr_events(name, path, window))
        return out

    def _merge_events(self, name: str, path: Path, window: Window) -> list[WatchdogEvent]:
        branch = _main_branch(path, self._runner)
        if branch is None:
            return [source_unavailable(f"{self.name}:{name}", ts=window.until,
                                        error="no main or master branch found")]
        r = self._runner([
            "git", "-C", str(path), "log", branch, "--merges",
            f"--since={window.since}", f"--until={window.until}",
            f"--pretty=format:{_LOG_FORMAT}",
        ])
        if r.returncode != 0:
            return [source_unavailable(f"{self.name}:{name}", ts=window.until,
                                        error=(r.stderr or "git log failed").strip()[:200])]
        web = _remote_web_url(path, self._runner)
        out: list[WatchdogEvent] = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\x1f")
            if len(parts) != 3:
                continue
            sha, ts, subject = parts
            short = sha[:10]
            out.append(WatchdogEvent(
                ts=ts, source=self.name, kind="GitMergeToMain",
                object=f"{name}@{short}", severity="info",
                summary=f"{name}: merged '{subject}' to {branch}.",
                link=WatchdogLink(
                    uri=f"skworld://skos/watchdog/git/{name}/commit/{sha}",
                    http=f"{web}/commit/{sha}" if web else ""),
                ref=f"git:{name}:merge:{sha}",
                meta={"branch": branch},
            ))
        return out

    def _pr_events(self, name: str, path: Path, window: Window) -> list[WatchdogEvent]:
        lister = self._pr_lister
        if lister is None:
            if shutil.which("gh") is None:
                return []  # bonus signal only; quiet when gh is not installed
            lister = self._real_pr_lister
        try:
            prs = lister(name, path)
        except Exception as exc:  # noqa: BLE001 - one bad `gh` call must not cost the git log
            return [source_unavailable(f"{self.name}:{name}:pr", ts=window.until,
                                        error=f"gh pr list failed: {exc}")]

        out: list[WatchdogEvent] = []
        now = datetime.now(timezone.utc)
        date = window.until[:10]
        for pr in prs or []:
            number = pr.get("number")
            title = str(pr.get("title", ""))
            url = str(pr.get("url", ""))
            created = _parse_ts(pr.get("createdAt"))
            age_days = (now - created).days if created else None
            if age_days is not None and age_days >= AGING_PR_DAYS:
                out.append(WatchdogEvent(
                    ts=window.until, source=self.name, kind="AgingPR",
                    object=f"{name}#{number}", severity="notable",
                    summary=f"{name} PR #{number} '{title}' has been open {age_days}d.",
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/git/{name}/pr/{number}",
                                       http=url),
                    ref=f"git:{name}:pr:{number}:aging:{date}",
                ))
            rollup = pr.get("statusCheckRollup") or []
            failing = [c for c in rollup if str((c or {}).get("conclusion", "")).upper() == "FAILURE"]
            if failing:
                out.append(WatchdogEvent(
                    ts=window.until, source=self.name, kind="CIFailure",
                    object=f"{name}#{number}", severity="problem",
                    summary=(f"{name} PR #{number} '{title}' has {len(failing)} "
                             f"failing check(s)."),
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/git/{name}/pr/{number}",
                                       http=url),
                    ref=f"git:{name}:pr:{number}:ci-failing:{date}",
                ))
        return out

    def _real_pr_lister(self, name: str, path: Path) -> list[dict]:
        r = self._runner([
            "gh", "pr", "list", "--state", "open", "--json",
            "number,title,url,createdAt,statusCheckRollup",
        ], cwd=path)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "gh exited non-zero").strip()[:200])
        return json.loads(r.stdout)
