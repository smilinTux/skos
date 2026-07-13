"""Autopilot config: ~/.skcapstone/config/autopilot.yaml -> typed Config.

Precedence: SKOS_AUTOPILOT_CONFIG (explicit) > <SKCAPSTONE_HOME>/config/autopilot.yaml
> ~/.skcapstone/config/autopilot.yaml. A missing file yields a disabled default so
a fresh box never auto-runs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .types import RepoSpec

_REPO_KEYS = {
    "name", "path", "base_branch", "integration_branch", "test_cmd", "ci",
    "coverage_cmd", "ci_poll_timeout", "automerge", "auto_revert", "min_diff_coverage",
}


def config_path() -> Path:
    env = os.environ.get("SKOS_AUTOPILOT_CONFIG")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
    return home / "config" / "autopilot.yaml"


@dataclass
class Caps:
    max_concurrent: int = 3
    new_tasks_per_run: int = 10
    max_tokens_per_run: int = 2_000_000
    max_usd_per_day: float = 25.0


@dataclass
class Config:
    enabled: bool = False
    harness: str = "claude-code"
    allowed_tools: list[str] = field(default_factory=list)
    repo_map: dict[str, RepoSpec] = field(default_factory=dict)
    automerge_repos: list[str] = field(default_factory=list)
    caps: Caps = field(default_factory=Caps)
    digest_chat: str | None = None
    epic_id: str | None = None

    def repo(self, name: str) -> RepoSpec | None:
        return self.repo_map.get(name)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        p = path or config_path()
        if not p.exists():
            return cls()                                  # disabled default
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        repo_map: dict[str, RepoSpec] = {}
        for name, spec in (raw.get("repo_map") or {}).items():
            spec = dict(spec)
            spec.setdefault("name", name)                 # key is the canonical name
            repo_map[name] = RepoSpec(**{k: v for k, v in spec.items() if k in _REPO_KEYS})
        caps_raw = raw.get("caps") or {}
        caps = Caps(**{k: v for k, v in caps_raw.items()
                       if k in Caps.__dataclass_fields__})
        return cls(
            enabled=bool(raw.get("enabled", False)),
            harness=raw.get("harness", "claude-code"),
            allowed_tools=list(raw.get("allowed_tools") or []),
            repo_map=repo_map,
            automerge_repos=list(raw.get("automerge_repos") or []),
            caps=caps,
            digest_chat=raw.get("digest_chat"),
            epic_id=raw.get("epic_id"),
        )


_AUTOPILOT_JOB_YAML = """\
autopilot-daily:
  schedule: "30 6 * * *"
  type: shell
  nodes: [noroc2027]
  command: >
    /usr/bin/flock -n /home/cbrd21/.skcapstone/scheduler/autopilot-daily.lock
    /home/cbrd21/clawd/skos/scripts/sk-cron-run.sh autopilot-daily
    /home/cbrd21/.skenv/bin/skos autopilot run --once
  timeout: 3600
  retries: 0
  jitter: 30
  notify: on_failure
  notify_level: warn
  catchup: false
  enabled: true
"""


def render_autopilot_job_yaml() -> str:
    """Return the literal `autopilot-daily` scheduler block (spec section 13).

    Ready to paste under the top-level `jobs:` map in
    ~/.skcapstone/config/jobs.yaml. Kept as source-of-truth here so the block
    is testable even though the live synced config is edited by hand.
    """
    return _AUTOPILOT_JOB_YAML
