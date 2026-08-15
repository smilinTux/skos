"""WD-8: watchdog findings become tracked work in the ONE unified GTD.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md section 9 ("Phase 2b:
GTD only. `gtd_ingest.upsert` with `source="watchdog"` and a stable
`source_ref` per finding. Upsert semantics mean a persisting finding updates
one item; `unchanged` writes nothing.") and section 13's freeze rule ("all
GTD/card writes stand down when the fleet is frozen; digest generation itself
keeps running under freeze").

This module is the ONLY place skwatchdog writes anything outside its own
cursors and digest artifacts, and it writes through exactly one port: the
gtd-ingest sink. There is no side file of "what we already filed", no second
list, no registry of findings. When this module needs to know whether a
finding is already tracked, it ASKS THE SINK (`_find_item`,
`_open_watchdog_items`), the same way `skos.adapters.order` reads its
in-flight orders straight off the store rather than keeping a registry.

Four properties carry the whole design:

1. **Off by default.** `SKWATCHDOG_GTD` is unset in production until Chef
   flips it. With it off, `file_findings` returns before touching the
   environment in any other way: it never resolves `gtd_dir()` (which would
   CREATE the store directory), never reads a list file, never writes. The
   published digest JSON is byte-identical either way.
2. **A stable `source_ref`.** `WatchdogEvent.ref` is deliberately NOT reused
   here. That field is the digest's per-occurrence dedupe key and most
   adapters bake a timestamp or a date into it (`fleet:{node}:{ts}:...`,
   `scheduler:{job}:stale:{date}`), so it identifies "this sighting", not
   "this problem". A GTD item must survive the calendar: a cron job that is
   still stalled tomorrow has to be the SAME item, not a second one. So the
   GTD identity is the finding's own coordinates, `source:kind:object`,
   every part of which the adapters already derive from the real object
   (a job name, an incident id, a `service@node`, a `repo#pr`) and none of
   which moves with the clock.
3. **Quiet on a no-change poll.** `upsert` performs NO WRITE when nothing
   changed, and that property is only worth anything if the capture we hand
   it is itself stable. So the item's text and its `meta.watchdog` block are
   built from the finding's stable coordinates only. The volatile half of a
   finding (a summary that counts days, a run's event totals) stays in the
   digest, where being current is the point. The first-observed summary is
   carried forward off the existing item precisely so a re-worded summary
   cannot turn every morning into an `updated`.
4. **Only problems file.** `notable` and `info` stay narrative in the digest.
   The 2026-08-08 coord flood (821 cards, open tasks to 1246, recovered only
   by a per-card validation sweep) is what filing an item per info event
   looks like. This filter is not a tuning knob; it is the flood discipline.

Auto-completion closes the loop: a tracked finding that no longer appears in
the run's problem set is upserted to `status="done"`, which archives it. Two
guards keep that honest. A finding is only completed when its source READ OK
in this same run (a source that was unavailable proves nothing about whether
its findings cleared), and completion is non-destructive: `upsert` searches
`archive.json` too, so if the same finding returns, the stable `source_ref`
resurrects the ORIGINAL item back out of the archive instead of opening a
new one.

Nothing here dispatches work. No coord cards, no staged lane, no board writes:
that is WD-9, behind its own flag.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from ..gtd_ingest import GtdCapture, upsert, _ALL_FILES, _find_item, _load
from .render import strip_banned_dashes

#: The feature flag, default OFF. Flipped in the scheduler env file when Chef
#: is ready (plan: "Flip: SKWATCHDOG_GTD=1 in the scheduler env file.
#: Rollback: unset it; existing items age out through normal GTD processing").
FLAG = "SKWATCHDOG_GTD"

#: The gtd_ingest `source` every item this module writes carries (spec 9).
GTD_SOURCE = "watchdog"

#: The namespaced meta key the block lands under on the item, mirroring the
#: order adapter's `meta.order`.
META_KEY = "watchdog"

#: Where a filed finding lands. `next` (not `inbox`) so a real fleet problem
#: shows up in `skcapstone gtd next` the same morning the digest names it.
DEFAULT_STATUS = "next"
DEFAULT_CONTEXT = "@ops"
DEFAULT_PRIORITY = "high"

#: The one severity that files (see module docstring, point 4).
FILING_SEVERITY = "problem"

_TRUTHY = {"1", "true", "yes", "on"}


def gtd_enabled() -> bool:
    """True only when `SKWATCHDOG_GTD` is explicitly set to a truthy value.
    Anything else, including unset, empty, "0" and "false", reads as off."""
    return str(os.environ.get(FLAG, "")).strip().lower() in _TRUTHY


def fleet_frozen() -> bool:
    """The fleet-wide kill switch, read exactly the way every actuating path
    in the operator seat reads it: `skcapstone.fleet.store.is_frozen(
    default_paths())` (see operator_seat/actuator.py, loop.py, and the four
    app adapters). No second freeze concept is invented here, and no freeze
    file is ever written: the kill switch is the human's alone.

    skcapstone is an OPTIONAL sibling of skos (tests/conftest.py's
    `_HAVE_SKCAPSTONE`), so an ImportError means this node has no fleet
    control plane at all and therefore no kill switch to respect: not frozen.
    Any OTHER failure reads as FROZEN, mirroring the store's own rule that an
    unreadable freeze file counts as frozen: when in doubt, halt actuation.
    """
    try:
        from skcapstone.fleet.paths import default_paths
        from skcapstone.fleet import store
    except ImportError:
        return False
    try:
        return bool(store.is_frozen(default_paths()))
    except Exception:  # noqa: BLE001 - deliberate: in doubt, halt actuation
        return True


def _slug(value: Any) -> str:
    """Collapse whitespace so a source_ref is one unambiguous token per part.
    Never lowercases and never strips punctuation: the parts are real object
    identities (job names, incident ids, `service@node`) and mangling them
    would break the very stability this key exists to provide."""
    return "-".join(str(value or "").split())


def source_ref_for(event: Mapping) -> str:
    """The stable GTD identity of a finding: `source:kind:object`.

    Stable across days by construction, because every part is a coordinate of
    the real object and none of them is a clock reading. `WatchdogEvent.ref`
    is deliberately not used (module docstring, point 2).
    """
    parts = [_slug(event.get("source")) or "unknown",
             _slug(event.get("kind")) or "Finding"]
    obj = _slug(event.get("object"))
    if obj:
        parts.append(obj)
    return ":".join(parts)


def item_text(event: Mapping) -> str:
    """The item's one line, built from stable coordinates only so a re-worded
    summary never rewrites the item. Chef reads these, so the banned dashes
    are stripped here too, exactly as the renderer does for the digest."""
    source = str(event.get("source") or GTD_SOURCE)
    kind = str(event.get("kind") or "Finding")
    obj = str(event.get("object") or "")
    text = f"skwatchdog {source}: {kind} on {obj}" if obj else f"skwatchdog {source}: {kind}"
    return strip_banned_dashes(text)


def _meta_block(event: Mapping, existing: Mapping | None) -> dict:
    """The `meta.watchdog` block: the finding's coordinates plus its deep link
    (spec section 8's uri + http pair, so the GTD item points back at the real
    object). The first-observed summary is carried forward off the existing
    item rather than refreshed, so a summary that re-words itself daily cannot
    turn a `unchanged` poll into an `updated` write."""
    link = event.get("link") or {}
    block = {
        "source": str(event.get("source") or ""),
        "kind": str(event.get("kind") or ""),
        "object": str(event.get("object") or ""),
        "severity": str(event.get("severity") or FILING_SEVERITY),
        "link": {"uri": str(link.get("uri") or ""), "http": str(link.get("http") or "")},
    }
    prior = dict((existing or {}).get(META_KEY) or {})
    block["summary"] = (prior.get("summary")
                        or strip_banned_dashes(str(event.get("summary") or "")))
    return block


def _capture_for(event: Mapping, existing: Mapping | None) -> GtdCapture:
    """The capture for a currently-firing finding.

    `completed_at: None` rides in meta on purpose: when a finding returns
    after having been auto-completed, `upsert` finds the ARCHIVED item by its
    stable source_ref and pulls it back into the active list, and this clears
    the stale completion stamp on the way out. For an item that is merely
    still open the key is already absent-or-None, so it compares equal and
    costs nothing: the no-change poll stays a no-write poll."""
    return GtdCapture(
        text=item_text(event),
        source=GTD_SOURCE,
        source_ref=source_ref_for(event),
        context=DEFAULT_CONTEXT,
        priority=DEFAULT_PRIORITY,
        status=DEFAULT_STATUS,
        meta={META_KEY: _meta_block(event, existing), "completed_at": None},
    )


def _completion_capture(item: Mapping) -> GtdCapture:
    """The capture that closes a cleared finding. Everything except `status`
    is echoed back from the stored item, so the only difference `upsert` sees
    is `done`: one field changes, the item archives, and nothing else on it is
    rewritten in passing."""
    block = dict(item.get(META_KEY) or {})
    return GtdCapture(
        text=str(item.get("text") or ""),
        source=GTD_SOURCE,
        source_ref=str(item.get("source_ref") or ""),
        context=str(item.get("context") or DEFAULT_CONTEXT),
        priority=item.get("priority"),
        status="done",
        meta={META_KEY: block} if block else {},
    )


def _open_watchdog_items() -> list[dict]:
    """Every still-open item this module owns, read straight off the sink
    (archive excluded: an archived finding is already closed). This IS the
    "what have we filed" lookup; there is no parallel store to consult."""
    out: list[dict] = []
    for fname in _ALL_FILES:
        if fname == "archive.json":
            continue
        for it in _load(fname):
            if it.get("source") == GTD_SOURCE and it.get("source_ref"):
                out.append(it)
    return out


def _healthy_sources(digest: Mapping) -> set[str]:
    """The sources that actually read OK in this run (digest `per_source.ok`,
    spec 6.4). Only these may auto-complete their findings: a source that was
    unavailable proves nothing about whether its problems cleared."""
    per_source = digest.get("per_source") or {}
    return {name for name, st in per_source.items()
            if isinstance(st, Mapping) and st.get("ok")}


def file_findings(digest: Mapping) -> dict:
    """File this run's problem findings into the unified GTD and close the
    ones that cleared. NEVER raises: a GTD store problem must not take down a
    digest run that has already published, so every failure is recorded in
    the returned report instead (the same fail-safe posture as `collect_safe`).

    Returns `{enabled, skipped, filed, completed, unchanged, errors?}`.
    `filed` and `completed` hold `{source_ref, id, action}` records.
    """
    report: dict = {"enabled": False, "skipped": None,
                    "filed": [], "completed": [], "unchanged": 0}

    # Flag check FIRST, before anything that could touch the store: resolving
    # the GTD dir would create it, which is a side effect an off flag must not
    # have (requirement: with the flag off this card is invisible).
    if not gtd_enabled():
        report["skipped"] = "flag-off"
        return report
    report["enabled"] = True

    if fleet_frozen():
        report["skipped"] = "frozen"
        return report

    try:
        _file_findings_unguarded(digest, report)
    except Exception as exc:  # noqa: BLE001 - deliberate: filing never breaks the digest
        report.setdefault("errors", []).append(str(exc))
    return report


def _file_findings_unguarded(digest: Mapping, report: dict) -> None:
    # One capture per stable identity: several sightings of the same finding
    # inside one window are one item, first sighting wins.
    wanted: dict[str, Mapping] = {}
    for event in (digest.get("problems") or []):
        if not isinstance(event, Mapping):
            continue
        if str(event.get("severity") or "") != FILING_SEVERITY:
            continue
        wanted.setdefault(source_ref_for(event), event)

    for ref, event in wanted.items():
        _fname, _idx, existing, _items = _find_item(GTD_SOURCE, ref)
        try:
            iid, action = upsert(_capture_for(event, existing))
        except Exception as exc:  # noqa: BLE001 - one bad item never stops the rest
            report.setdefault("errors", []).append(f"{ref}: {exc}")
            continue
        if action == "unchanged":
            report["unchanged"] += 1
        else:
            report["filed"].append({"source_ref": ref, "id": iid, "action": action})

    healthy = _healthy_sources(digest)
    for item in _open_watchdog_items():
        ref = str(item.get("source_ref") or "")
        if ref in wanted:
            continue  # still firing
        block = item.get(META_KEY) or {}
        if str(block.get("source") or "") not in healthy:
            continue  # source silent or unavailable this run: not proof it cleared
        try:
            iid, action = upsert(_completion_capture(item))
        except Exception as exc:  # noqa: BLE001
            report.setdefault("errors", []).append(f"{ref}: {exc}")
            continue
        if action != "unchanged":
            report["completed"].append({"source_ref": ref, "id": iid, "action": action})


__all__ = [
    "FLAG", "GTD_SOURCE", "META_KEY", "FILING_SEVERITY",
    "gtd_enabled", "fleet_frozen", "source_ref_for", "item_text", "file_findings",
]
