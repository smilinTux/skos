#!/usr/bin/env python3
"""skos.secret_env: resolve secrets and operator-identifying config (PII) at
runtime instead of hardcoding them in tracked source.

Resolution order for any name:
  1. the process environment (os.environ) - injected by the scheduler unit,
     an interactive `export`, or a systemd EnvironmentFile;
  2. the gitignored operator env file (default ~/.skcapstone/skos-schedule.env,
     override with $SKOS_SCHEDULE_ENV) - the same file `skos schedule install`
     already sources for the crontab;
  3. the caller-supplied default (a safe placeholder, never a real value).

No real secret or PII value lives in this repo. The real values live only in
the mode-600 env file outside the tree. See docs/runbooks/skos-scheduler.md and
deploy/schedule/skos-schedule.env.example.
"""
from __future__ import annotations
import os
from functools import lru_cache
from pathlib import Path


def _env_file_path() -> Path:
    override = os.environ.get("SKOS_SCHEDULE_ENV")
    if override:
        return Path(override).expanduser()
    home = os.environ.get("SKCAPSTONE_HOME")
    base = Path(home) if home else (Path.home() / ".skcapstone")
    return base / "skos-schedule.env"


@lru_cache(maxsize=1)
def _file_values() -> dict[str, str]:
    """Parse KEY=VALUE lines from the operator env file (if present)."""
    path = _env_file_path()
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def resolve(name: str, default: str | None = None) -> str | None:
    """Return the value for ``name`` from env, then the env file, else default."""
    if name in os.environ:
        return os.environ[name]
    fv = _file_values().get(name)
    if fv is not None:
        return fv
    return default


def ensure(name: str) -> str | None:
    """Make ``name`` available to child processes.

    If it is already in os.environ, leave it. Otherwise, if the env file
    supplies it, copy it into os.environ so subprocesses (gog, psql, ...)
    inherit it. Never injects a hardcoded fallback. Returns the resolved value
    or None if unresolved (the child tool then fails with its own clear error).
    """
    if name in os.environ:
        return os.environ[name]
    val = _file_values().get(name)
    if val is not None:
        os.environ[name] = val
    return val


def accounts(name: str = "GTD_MAIL_ACCOUNTS") -> list[str]:
    """Resolve the operator's Gmail account list from ``name`` (comma-separated).

    Returns [] when unconfigured, so nothing personal is baked into the repo and
    the mail/status surfaces simply report no boxes until the env file is set.
    """
    raw = resolve(name, "") or ""
    return [a.strip() for a in raw.split(",") if a.strip()]
