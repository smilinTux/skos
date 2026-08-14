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

from .. import gogbin
from ..gtd_ingest import GtdCapture, GtdSourceAdapter

from ..secret_env import accounts as _mail_accounts

# Resolved, never assumed: GOG env override, then PATH, then known
# locations. See skos.gogbin for why the old hardcoded Homebrew path
# was wrong (fresh installs, a stale pinned tap, and three copies that
# drift independently).
GOG = gogbin.find_gog() or "gog"
# accounts whose calendars hold the operator's actionable commitments. Resolved
# from GTD_CAL_ACCOUNTS, falling back to the shared mail-account list; no personal
# address is hardcoded in this public repo.
_cal_raw = os.environ.get("GTD_CAL_ACCOUNTS", "")
CAL_ACCOUNTS = [a.strip() for a in _cal_raw.split(",") if a.strip()] or _mail_accounts()
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
                    # --all-pages, not a big --max: gog defaults to --max 10 and
                    # (on v0.12.0) prints NO truncation hint, so this silently
                    # ingested the first 10 events as if they were the calendar.
                    # Measured 2026-08-14: 10 seen vs 234 real, a 120-day window.
                    # A magic --max would just move the cliff to whoever has more
                    # events than the number, and move it silently again.
                    [GOG, "calendar", "events", "-a", acct, "--all", "--all-pages",
                     "--days", str(DAYS), "-j"],
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
