"""DM delivery (WD-3): the existing Hermes path, reused, not a second notifier.

Spec section 6.4: "Delivery: sk-alert DM (absorbing the 07:45 ops-report
slot) plus the published latest/ artifact beside the Atlas brief." The WD-3
card: "DM delivery through the existing path (the same one `sk-status
report` uses to reach Hermes)." `skos.status.run`'s ``report`` command
already does exactly this:

    subprocess.run(["hermes", "send", "--to", HERMES_DM,
                    "--subject", subject, body], ...)

This module shells to the identical binary and target, formatted for a
one-shot digest message rather than the counts report. `HERMES_DM` is
resolved the same way status.py resolves it: `skos.secret_env.resolve`, the
gitignored operator env file, never a hardcoded destination.

The sender is injectable (mirrors
`skcapstone.operator_seat.notify.notify_report`'s `sender` parameter) so
tests never shell out to a real `hermes` binary.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Mapping, Optional

from .. import secret_env
from .render import link_of, strip_banned_dashes

HERMES_DM = secret_env.resolve("HERMES_DM", "")  # operator DM target, set in env file

#: How many problem/notable lines to inline in the DM body itself before
#: pointing the reader at the full published digest instead. Telegram
#: messages are read on a phone; the full narrative lives in the published
#: latest/ artifact, this is the "here's what mattered, go look" ping.
MAX_DM_LINES_PER_BUCKET = 8


def _dm_event_line(event: Mapping) -> str:
    """One DM line: reuses `render.strip_banned_dashes` / `render.link_of`,
    the same helpers `render_markdown` uses, rather than a second
    implementation of either concern for the DM's own line format."""
    summary = strip_banned_dashes(str(event.get("summary") or "(no summary)"))
    link = link_of(event)
    line = f"  - {summary}"
    if link:
        line += f" -> {link}"
    return line


def format_dm(digest: Mapping) -> str:
    """Render the digest to a Telegram-ready DM body: headline, then capped
    Problems/Notable line lists (each with its deep link), then counts. Pure
    and deterministic; never raises on a partial digest."""
    date = str(digest.get("date") or "")
    headline = strip_banned_dashes(str(digest.get("headline") or ""))
    problems = list(digest.get("problems") or [])
    notable = list(digest.get("notable") or [])
    info_counts = dict(digest.get("info_counts") or {})
    total_info = sum(info_counts.values()) if info_counts else 0

    lines = [f"\U0001f4cb skwatchdog digest: {date}", ""]
    if headline:
        lines += [headline, ""]

    if problems:
        lines.append(f"Problems ({len(problems)}):")
        lines += [_dm_event_line(e) for e in problems[:MAX_DM_LINES_PER_BUCKET]]
        if len(problems) > MAX_DM_LINES_PER_BUCKET:
            lines.append(f"  ... and {len(problems) - MAX_DM_LINES_PER_BUCKET} more.")
        lines.append("")

    if notable:
        lines.append(f"Notable ({len(notable)}):")
        lines += [_dm_event_line(e) for e in notable[:MAX_DM_LINES_PER_BUCKET]]
        if len(notable) > MAX_DM_LINES_PER_BUCKET:
            lines.append(f"  ... and {len(notable) - MAX_DM_LINES_PER_BUCKET} more.")
        lines.append("")

    if not problems and not notable:
        lines.append("Nothing firing or notable.")
        lines.append("")

    lines.append(f"({total_info} quiet info event(s). Full digest published to latest/.)")
    return "\n".join(lines).rstrip() + "\n"


def default_sender(text: str) -> bool:
    """Send one message via the same `hermes send --to HERMES_DM` path
    `sk-status report` uses. Never raises; returns success."""
    try:
        r = subprocess.run(
            ["hermes", "send", "--to", HERMES_DM, "--subject", "skwatchdog digest", text],
            capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def send_digest_dm(digest: Mapping, *, sender: Optional[Callable[[str], bool]] = None) -> bool:
    """Format and send the digest DM. Returns the sender's success flag;
    never raises, so a Hermes/Telegram outage never blocks a digest run that
    has already published its artifacts (the artifact IS the digest of
    record; the DM is a ping on top of it).

    `sender` defaults to `default_sender`, resolved by name at CALL time
    (not bound as a default-argument value) so `run_digest_and_deliver`'s
    unqualified `send_digest_dm(digest)` still honors a test's
    `monkeypatch.setattr(deliver, "default_sender", ...)` -- a default
    argument evaluated once at import time would silently pin the ORIGINAL
    function object and never see that patch.
    """
    return (sender or default_sender)(format_dm(digest))


__all__ = ["format_dm", "send_digest_dm", "default_sender", "HERMES_DM"]
