"""scheduler: the generalized staleness watchdog over the cron run-ledger.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.3: "failed/
slow/missing jobs; 'job X has not run in N days' (the generalized staleness
watchdog)". Freshness IS liveness here: there is no long-lived scheduler
daemon, so a ledger whose newest run for a job is older than that job's own
expected cadence reads as a stalled job.

``skharness.jobs`` already solved exactly this shape (card C-8): median-gap
cadence inference over a job's last 10 runs, a 3x-cadence stale threshold
with a 15 minute floor, a generous 24h fallback window for a job with fewer
than two parseable timestamps, and "fail toward stale on an unparseable
timestamp" (an unknown last-run time can never claim freshness). This
adapter REUSES ``skharness.jobs.read_job_runs`` rather than inventing a
second staleness rule, exactly per the WD-2 card's instruction. ``skharness``
is an OPTIONAL sibling package (the same one ``skos.autopilot`` re-exports),
imported lazily inside ``collect()`` so an absent skharness degrades this
one adapter to ``SourceUnavailable`` via ``collect_safe`` rather than
silently dropping it from the registry.

This is a DIFFERENT staleness reader than ``skos.operator_probe``'s
``SchedulerAlive`` condition (a single fleet-wide "is the pipeline alive at
all" boolean gated by one flat ``_SCHEDULER_MAX_AGE_S`` constant): this
adapter narrates PER-JOB staleness with each job's own inferred cadence, the
finer-grained signal the daily digest wants. Both read the same ledger file
by default (``SKOS_CRON_LEDGER`` env override, mirroring
``operator_probe._cron_ledger()``), so the two skos-owned staleness readers
never disagree about where the ledger lives, only about the grain of the
answer.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..events import WatchdogEvent, WatchdogLink
from ..port import Window, WatchdogSourceAdapter, registry


def _ledger_path() -> Path:
    env = os.environ.get("SKOS_CRON_LEDGER")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
    return home / "logs" / "cron-ledger.jsonl"


def _epoch(iso_ts: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def _fmt_age(seconds) -> str:
    if seconds is None:
        return "an unknown time"
    seconds = max(0.0, float(seconds))
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


@registry.register
class SchedulerAdapter(WatchdogSourceAdapter):
    name = "scheduler"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        # Optional sibling import, deliberately lazy (see module docstring).
        from skharness.jobs import read_job_runs

        path = _ledger_path()
        now_s = _epoch(window.until)
        rows = read_job_runs(path, now=now_s)

        out: list[WatchdogEvent] = []
        healthy = 0
        date = window.until[:10]
        for row in rows:
            if row.status == "failed":
                out.append(WatchdogEvent(
                    ts=window.until, source=self.name, kind="JobFailed",
                    object=row.job, severity="problem",
                    summary=(f"scheduler job {row.job} failed on its last run"
                             f"{f' on {row.host}' if row.host else ''}."),
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/scheduler/{row.job}", http=""),
                    ref=f"scheduler:{row.job}:failed:{row.last_start or date}",
                    meta={"host": row.host, "tail": row.tail, "dur_s": row.dur_s},
                ))
            elif row.stale:
                age = _fmt_age(row.staleness_s)
                threshold = _fmt_age(row.stale_threshold_s)
                out.append(WatchdogEvent(
                    ts=window.until, source=self.name, kind="JobStalled",
                    object=row.job, severity="problem",
                    summary=(f"scheduler job {row.job} has not run in {age} "
                             f"(expected roughly every {threshold})."),
                    link=WatchdogLink(uri=f"skworld://skos/watchdog/scheduler/{row.job}", http=""),
                    ref=f"scheduler:{row.job}:stale:{date}",
                    meta={"host": row.host, "staleness_s": row.staleness_s,
                          "stale_threshold_s": row.stale_threshold_s},
                ))
            else:
                healthy += 1
        if rows and not out:
            # Every known job ran on cadence and none failed: one quiet info
            # line rather than silence, so "nothing to report" is visible
            # too (spec 2.1's "sent always, so silence is never ambiguous").
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="SchedulerHealthy",
                object="scheduler", severity="info",
                summary=f"{healthy} scheduler job(s) ran on cadence, none failed.",
                link=WatchdogLink(uri="skworld://skos/watchdog/scheduler", http=""),
                ref=f"scheduler:summary:{date}",
            ))
        return out
