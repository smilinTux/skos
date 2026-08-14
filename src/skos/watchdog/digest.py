"""Digest assembly: fold WatchdogEvents into the one daily digest shape.

Spec section 6.4. Assembly is deterministic: bucket events by severity,
dedupe by `ref` (a source's collect() may legitimately overlap windows
across a crash-replay, spec 6.1), compute per-source info counts, keep every
link. This module renders no model output and calls no model: the spec's
"exactly one model call renders the headline narrative ... through skgateway
sk-default, with a strict no-model fallback to a pure template" is WD-3's
renderer. What WD-3 falls back to IS the deterministic template this module
produces, so a digest here is already a complete, valid digest on its own,
never a partial one waiting on a model.

Output shape (spec 6.4):
    {date, window: {from, to}, headline, problems: [...], notable: [...],
     info_counts, per_source: {name: {ok, events, cursor}}}

The Code section's Digest tab (card C-9, merged, see
skworld-app/packages/skcode_client/lib/src/skcode_digest.dart) parses
exactly `date`, `headline`, `problems`, `notable`, `info_counts` out of this
shape. Nothing here may rename or restructure those five keys.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional

from .events import WatchdogEvent
from .port import Window


def _sort_key(e: WatchdogEvent):
    try:
        dt = datetime.fromisoformat(e.ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        dt = datetime.min.replace(tzinfo=timezone.utc)
    return (dt, e.ref, e.source)


def _dedupe(events: Iterable[WatchdogEvent]) -> list[WatchdogEvent]:
    """First occurrence wins per `ref`; events without a ref are never
    collapsed against each other (an adapter that omits ref is opting out of
    dedupe, not asking for its events to vanish)."""
    seen: set[str] = set()
    out: list[WatchdogEvent] = []
    for e in events:
        if e.ref:
            if e.ref in seen:
                continue
            seen.add(e.ref)
        out.append(e)
    return out


def render_headline(problems: list[dict], notable: list[dict],
                     info_counts: Mapping[str, int]) -> str:
    """The pure-template headline: no model, never fails, always renders.
    This is exactly the fallback WD-3's model-backed renderer degrades to
    when skgateway is unreachable, so it must stand on its own as a
    reasonable one-line summary of the day."""
    n_problems = len(problems)
    n_notable = len(notable)
    n_info = sum(info_counts.values()) if info_counts else 0
    n_sources = len({e.get("source") for e in problems + notable} | set(info_counts))
    if n_problems == 0 and n_notable == 0 and n_info == 0:
        return "No events since the last digest."
    parts = []
    parts.append(f"{n_problems} problem{'s' if n_problems != 1 else ''}")
    parts.append(f"{n_notable} notable item{'s' if n_notable != 1 else ''}")
    parts.append(f"{n_info} quiet info event{'s' if n_info != 1 else ''}")
    src_note = f" across {n_sources} source{'s' if n_sources != 1 else ''}" if n_sources else ""
    return f"{', '.join(parts)}{src_note}."


def assemble_digest(events: Iterable[WatchdogEvent], *,
                     date: Optional[str] = None,
                     window: Optional[Window] = None,
                     per_source: Optional[Mapping[str, dict]] = None) -> dict:
    """Fold a flat list of WatchdogEvents (from however many sources) into
    one digest dict. Pure and deterministic: same input, same output, no I/O,
    no model call.

    `per_source`, when given, is passed through as-is (spec 6.4's
    `{name: {ok, events, cursor}}`); callers that run adapters via
    skos.watchdog.port.collect_safe supply it, but assembly does not require
    it and defaults to an empty mapping so this function stays testable with
    a bare list of events.
    """
    ordered = sorted(_dedupe(events), key=_sort_key)

    problems = [e.to_dict() for e in ordered if e.severity == "problem"]
    notable = [e.to_dict() for e in ordered if e.severity == "notable"]

    info_counts: dict[str, int] = {}
    for e in ordered:
        if e.severity == "info":
            info_counts[e.source] = info_counts.get(e.source, 0) + 1

    resolved_date = date
    if not resolved_date:
        if window is not None:
            resolved_date = window.until[:10]
        elif ordered:
            resolved_date = ordered[-1].ts[:10]
        else:
            resolved_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return {
        "date": resolved_date,
        "window": window.to_dict() if window is not None else {},
        "headline": render_headline(problems, notable, info_counts),
        "problems": problems,
        "notable": notable,
        "info_counts": info_counts,
        "per_source": dict(per_source) if per_source else {},
    }
