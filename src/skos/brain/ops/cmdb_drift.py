"""CmdbDriftBounded: judge CMDB drift from checksum-verified reconcile artifacts
(coord card 5dcf04d1).

Evidence comes only from artifacts whose ``.sha256`` sidecar verifies, read
through ``skcoord.cmdb_reconcile.read_verified_run_artifacts``. ``skcoord`` is an
OPTIONAL sibling package to skos (see ``kedb_coverage``), so the read is lazy.
This module never writes and never runs a live scan.

The verdict is judged per scope. The baseline file names scopes; each named
scope gates unless it says ``"gated": false``. For every gated scope, the newest
verified artifact OF THAT SCOPE (by ``ended_at``) is checked against that
scope's own baseline. Artifacts of other scopes, and of named scopes that do
not gate, are reported in the reason but never decide it. Before this, the
newest artifact of any scope was judged, so recording a network shadow turned
the local gate red (2026-11-11).

  * ``Unknown`` -- no artifact verifies, or the reader failed.
  * ``False`` -- for some gated scope: no verified artifact of that scope, or
    its newest is older than :data:`MAX_ARTIFACT_AGE`, incomplete, has
    validation failures or no drift summary, or ``declared_not_observed`` is
    above that scope's baseline. Also False when the baseline is missing,
    invalid, or names no gated scope.
  * ``True`` -- every gated scope passes. The reason states age, scope and
    per-kind counts for each scope seen.

Only ``declared_not_observed`` gates. Other kinds are reported, not gated: local
scope cannot see remote CIs, and ``stored_not_discovered`` moves daily.

The baseline lives in canon at :data:`BASELINE_PATH`, keyed by artifact scope:
``{"local": {"declared_not_observed": 2},
"network": {"declared_not_observed": 2, "gated": false}}``. Lower it as findings
are fixed; only Chef raises it. A scope joins the gate (``gated`` removed or
true) only once its runs are scheduled, or the age rule turns it red between
manual runs. A ``True`` from local scope alone is not a fleet-wide claim.
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
    return Path(
        os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))
    ).expanduser()


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


def _load_baseline(canon: Path) -> dict[str, tuple[int | str, bool]] | str:
    """``{scope: (allowed count or a problem string, gated)}``, or a file-level problem."""
    path = canon / BASELINE_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return f"baseline {path} is missing or unreadable"
    except ValueError:
        return f"baseline {path} is invalid"
    if not isinstance(raw, dict) or not raw:
        return f"baseline {path} is invalid"
    out: dict[str, tuple[int | str, bool]] = {}
    for scope, entry in raw.items():
        if not isinstance(entry, dict) or _GATED_KIND not in entry:
            out[scope] = (
                f"baseline {path} is invalid or has no {_GATED_KIND} for scope {scope!r}",
                True,
            )
            continue
        gated = entry.get("gated", True)
        if not isinstance(gated, bool):
            out[scope] = (
                f"baseline {path} has an invalid gated flag for scope {scope!r}",
                True,
            )
            continue
        allowed = entry[_GATED_KIND]
        if isinstance(allowed, bool) or not isinstance(allowed, int) or allowed < 0:
            out[scope] = (
                f"baseline {path} has an invalid {_GATED_KIND} for scope {scope!r}",
                gated,
            )
            continue
        out[scope] = (allowed, gated)
    if not any(gated for _allowed, gated in out.values()):
        return f"baseline {path} names no gated scope"
    return out


def _judge(
    artifact: dict, allowed: int | str | None, now: datetime
) -> tuple[list[str], str]:
    """Failures for one scope's newest artifact, plus its evidence string."""
    ended = _ended_at(artifact)
    age = now - ended if ended else timedelta(0)
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
    if gated is None:
        failures.append("newest artifact has no drift summary")
    elif isinstance(allowed, str):
        failures.append(allowed)
    elif isinstance(allowed, int):
        if gated > allowed:
            failures.append(f"{_GATED_KIND} {gated} is above baseline {allowed}")
    counts = (
        ", ".join(f"{kind}={by_kind[kind]}" for kind in sorted(by_kind))
        if isinstance(by_kind, dict)
        else "no counts"
    )
    return failures, f"age {int(age.total_seconds())}s, scope {scope}, {counts}"


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
        return CmdbDrift(
            "Unknown", f"CMDB artifact reader failed: {type(exc).__name__}"
        )
    if not artifacts:
        return CmdbDrift(
            "Unknown", "no checksum-verified CMDB reconcile artifact exists"
        )

    newest: dict[str, tuple[datetime, dict]] = {}
    for artifact in artifacts:
        ended = _ended_at(artifact)
        if ended is None:
            continue
        scope = str(artifact.get("scope") or "unscoped")
        if scope not in newest or ended > newest[scope][0]:
            newest[scope] = (ended, artifact)
    if not newest:
        return CmdbDrift("False", "no verified CMDB artifact has a usable ended_at")

    current = now or datetime.now(timezone.utc)
    baseline = _load_baseline(canon)
    if isinstance(baseline, str):
        seen = "; ".join(_judge(a, None, current)[1] for _e, a in newest.values())
        return CmdbDrift("False", f"{baseline} ({seen})")

    failed: list[str] = []
    passed: list[str] = []
    for scope, (allowed, gated) in sorted(baseline.items()):
        if not gated:
            continue
        if scope not in newest:
            failed.append(f"no verified artifact for scope {scope!r}")
            continue
        failures, evidence = _judge(newest[scope][1], allowed, current)
        if failures:
            failed.append(f"{'; '.join(failures)} ({evidence})")
        else:
            passed.append(f"{evidence}; {_GATED_KIND} within baseline {allowed}")

    reported: list[str] = []
    for scope in sorted(newest):
        allowed, gated = baseline.get(scope, (None, False))
        if gated:
            continue
        failures, evidence = _judge(newest[scope][1], allowed, current)
        verdict = "; ".join(failures) if failures else "ok"
        reported.append(f"{evidence}: {verdict}")
    tail = f" | not gated: {' | '.join(reported)}" if reported else ""

    if failed:
        return CmdbDrift("False", " | ".join(failed + passed) + tail)
    return CmdbDrift("True", " | ".join(passed) + tail)
