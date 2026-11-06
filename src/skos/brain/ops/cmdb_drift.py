"""CmdbDriftBounded: judge CMDB drift from checksum-verified reconcile artifacts
(coord card 5dcf04d1).

Evidence comes only from artifacts whose ``.sha256`` sidecar verifies, read
through ``skcoord.cmdb_reconcile.read_verified_run_artifacts``. ``skcoord`` is an
OPTIONAL sibling package to skos (see ``kedb_coverage``), so the read is lazy.
This module never writes and never runs a live scan.

The verdict:

  * ``Unknown`` -- no artifact verifies, or the reader failed.
  * ``False`` -- the newest verified artifact (by ``ended_at``) is older than
    :data:`MAX_ARTIFACT_AGE`, incomplete, has validation failures or no drift
    summary, or ``declared_not_observed`` is above the baseline, or the baseline
    is missing or invalid.
  * ``True`` -- otherwise. The reason states age, scope and per-kind counts.

Only ``declared_not_observed`` gates. Other kinds are reported, not gated: local
scope cannot see remote CIs, and ``stored_not_discovered`` moves daily.

The baseline lives in canon at :data:`BASELINE_PATH`, keyed by artifact scope:
``{"local": {"declared_not_observed": 19}}``. Lower it as findings are fixed;
only Chef raises it. A ``True`` from local scope is not a fleet-wide claim.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

#: Two 3h timer intervals plus grace. The CMDB adapter's 4h CmdbReconcileFresh
#: is a separate, stricter signal for a different consumer.
MAX_ARTIFACT_AGE = timedelta(hours=7)
BASELINE_PATH = Path("baselines") / "cmdb-drift.json"
_GATED_KIND = "declared_not_observed"


def _skcapstone_home() -> Path:
    """Same env var and default as ``kedb_coverage._skcapstone_home``."""
    return Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))).expanduser()


def _default_artifacts(home: Path) -> list[dict]:
    from skcoord.cmdb_reconcile import read_verified_run_artifacts  # optional sibling

    return read_verified_run_artifacts(home)


@dataclass(frozen=True)
class CmdbDrift:
    """``status`` is ``"True"``, ``"False"`` or ``"Unknown"``; ``reason`` says why."""

    status: str
    reason: str


def _ended_at(artifact: dict) -> datetime | None:
    try:
        ended = datetime.fromisoformat(artifact["ended_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return ended if ended.tzinfo else None


def _baseline(canon: Path, scope: str) -> int | str:
    """Return the allowed count for ``scope``, or a string naming the problem."""
    path = canon / BASELINE_PATH
    try:
        allowed = json.loads(path.read_text(encoding="utf-8"))[scope][_GATED_KIND]
    except OSError:
        return f"baseline {path} is missing or unreadable"
    except (ValueError, KeyError, TypeError):
        return f"baseline {path} is invalid or has no {_GATED_KIND} for scope {scope!r}"
    if isinstance(allowed, bool) or not isinstance(allowed, int) or allowed < 0:
        return f"baseline {path} has an invalid {_GATED_KIND} for scope {scope!r}"
    return allowed


def compute_cmdb_drift(
    canon: Path,
    *,
    skcapstone_home: Path | None = None,
    artifact_loader: Callable[[Path], list[dict]] = _default_artifacts,
    now: datetime | None = None,
) -> CmdbDrift:
    """Compute the condition from verified evidence. Never raises."""
    try:
        artifacts = artifact_loader(skcapstone_home or _skcapstone_home())
    except Exception as exc:  # any reader failure is "no evidence", never a guess
        return CmdbDrift("Unknown", f"CMDB artifact reader failed: {type(exc).__name__}")
    if not artifacts:
        return CmdbDrift("Unknown", "no checksum-verified CMDB reconcile artifact exists")

    dated = [(ended, a) for a in artifacts if (ended := _ended_at(a)) is not None]
    if not dated:
        return CmdbDrift("False", "no verified CMDB artifact has a usable ended_at")
    ended, artifact = max(dated, key=lambda item: item[0])

    age = (now or datetime.now(timezone.utc)) - ended
    scope = str(artifact.get("scope") or "unscoped")
    failures: list[str] = []
    if age > MAX_ARTIFACT_AGE:
        failures.append(f"newest artifact is older than {MAX_ARTIFACT_AGE}")
    if (artifact.get("completeness") or {}).get("complete") is not True:
        failures.append("newest artifact is incomplete")
    reconcile = artifact.get("reconcile") or {}
    if not isinstance(reconcile, dict) or reconcile.get("validation_failures"):
        failures.append("newest artifact has validation failures")

    by_kind = (artifact.get("drift") or {}).get("by_kind")
    gated = by_kind.get(_GATED_KIND, 0) if isinstance(by_kind, dict) else None
    allowed: int | str | None = None
    if gated is None:
        failures.append("newest artifact has no drift summary")
    else:
        allowed = _baseline(canon, scope)
        if isinstance(allowed, str):
            failures.append(allowed)
        elif gated > allowed:
            failures.append(f"{_GATED_KIND} {gated} is above baseline {allowed}")

    counts = (
        ", ".join(f"{kind}={by_kind[kind]}" for kind in sorted(by_kind))
        if isinstance(by_kind, dict)
        else "no counts"
    )
    evidence = f"age {int(age.total_seconds())}s, scope {scope}, {counts}"
    if failures:
        return CmdbDrift("False", f"{'; '.join(failures)} ({evidence})")
    return CmdbDrift("True", f"{evidence}; {_GATED_KIND} within baseline {allowed}")
