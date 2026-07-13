"""Calendar → GTD pull adapter (gog-backed).

Captures real upcoming commitments (meetings/calls/appointments/classes in the
next ~2 days) as GTD next-actions so nothing slips. Excludes all-day entries and
routine noise (nootropic doses, affirmations, moon phases, holidays, birthdays).
Deduped by calendar event id, so re-runs never duplicate.
"""
from __future__ import annotations

import json
import os
import subprocess

from ..gtd_ingest import GtdCapture, GtdSourceAdapter

GOG = os.environ.get("GOG", "/home/linuxbrew/.linuxbrew/bin/gog")
# accounts whose calendars hold Chef's actionable commitments (primary by default;
# david.knestrick excluded from default because it carries the nootropic-dose noise)
CAL_ACCOUNTS = os.environ.get("GTD_CAL_ACCOUNTS", "chefboyrdave2.1@gmail.com").split(",")
DAYS = int(os.environ.get("GTD_CAL_DAYS", "2"))
_NOISE = ("dose", "affirmation", "wind-down", "phenylpiracetam", "neurogenesis",
          "moon", "flag day", "birthday", "lipsync", "🚫", "💊", "🌙", "⚡",
          "huperzine", "check-in", "reminder", "power hour", "focus block",
          "workout", "lunch", "standup")


class CalendarAdapter(GtdSourceAdapter):
    name = "calendar"

    def poll(self) -> list[GtdCapture]:
        caps: list[GtdCapture] = []
        for acct in CAL_ACCOUNTS:
            acct = acct.strip()
            try:
                out = subprocess.run(
                    [GOG, "calendar", "events", "-a", acct, "--all", "--days", str(DAYS), "-j"],
                    capture_output=True, text=True, timeout=60).stdout
                data = json.loads(out)
            except Exception:
                continue
            events = data.get("events") or data.get("items") or (data if isinstance(data, list) else [])
            for ev in events:
                summary = (ev.get("summary") or ev.get("SUMMARY") or "").strip()
                if not summary:
                    continue
                low = summary.lower()
                if any(k in low for k in _NOISE):
                    continue
                start = ev.get("start") or {}
                # only timed commitments: skip all-day (start has 'date' not 'dateTime')
                when = start.get("dateTime") if isinstance(start, dict) else start
                if not when or "T" not in str(when):
                    continue
                when = str(when)
                eid = ev.get("id") or ev.get("ID") or f"{acct}:{summary}:{when}"
                caps.append(GtdCapture(
                    text=f"[cal] {summary} @ {when[:16]}",
                    source="calendar", source_ref=str(eid), context="@calendar",
                    priority="medium", status="next",
                    meta={"cal_account": acct, "cal_when": when, "cal_summary": summary}))
        return caps
