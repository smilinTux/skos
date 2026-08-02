"""Weekly proposal-funnel digest.

Aggregates the four funnel counts (``submitted``, ``in_review``, ``approved``,
``rejected``) over the trailing 7 days from a list of proposal status
transitions, and formats them into a human-readable report.

Standalone: takes transitions as plain data (dicts or :class:`ProposalTransition`)
and does no I/O of its own, so it has no dependency on skos.autopilot (a thin
shim over the optional skharness.autocode engine) or its status/digest path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

FUNNEL_STAGES: tuple[str, ...] = ("submitted", "in_review", "approved", "rejected")
WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class ProposalTransition:
    """One proposal status transition: ``proposal_id`` moved to ``status`` at ``timestamp``."""

    proposal_id: str
    status: str
    timestamp: datetime


def _as_transition(t: Any) -> ProposalTransition:
    if isinstance(t, ProposalTransition):
        return t
    ts = t["timestamp"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ProposalTransition(proposal_id=t["proposal_id"], status=t["status"], timestamp=ts)


def generate_weekly_digest(
    transitions: Iterable[Any] = (),
    *,
    reference_time: datetime | None = None,
) -> str:
    """Return a formatted digest of funnel counts over the 7 days up to ``reference_time``.

    ``transitions`` are dicts with ``proposal_id``/``status``/``timestamp`` keys
    (or :class:`ProposalTransition`); ``status`` values outside
    :data:`FUNNEL_STAGES` are ignored. Categories with no activity are
    zero-filled rather than omitted or erroring.
    """
    now = reference_time if reference_time is not None else datetime.now(timezone.utc)
    window_start = now - WINDOW

    counts = dict.fromkeys(FUNNEL_STAGES, 0)
    for raw in transitions:
        t = _as_transition(raw)
        if window_start <= t.timestamp <= now and t.status in counts:
            counts[t.status] += 1

    lines = [
        f"Weekly Proposal Digest ({window_start.date().isoformat()} to {now.date().isoformat()})",
        *(f"  {stage}: {counts[stage]}" for stage in FUNNEL_STAGES),
    ]
    return "\n".join(lines)
