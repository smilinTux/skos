"""The digest run (WD-3): collect every registered source, assemble, render,
publish, advance cursors, deliver. This is what `skos watchdog digest` calls.

Order matters and is deliberate (spec 6.1's crash-safety rule, and WD-3's
"advance cursors only after a digest actually lands"):

    1. collect every source (fail-safe per source, WD-2's `collect_safe`)
    2. assemble the deterministic digest (WD-1's `assemble_digest`)
    3. render the headline: skgateway when it answers, the deterministic
       template otherwise (headline.py)
    4. render Markdown (render.py)
    5. publish JSON + Markdown, dated + latest/ (publish.py) -- the digest
       "lands" here
    6. ONLY NOW advance every source's cursor (WD-1's `cursor.advance`)
    6b. file problem findings into the unified GTD (WD-8's `gtd.file_findings`)
       -- flagged off by default, freeze-aware, and fail-safe: it never
       raises, so it can neither delay nor break a digest that has landed
    7. send the DM (deliver.py) -- best-effort, never gates the cursor
       advance in step 6; a Hermes outage must not cause tomorrow's window
       to silently swallow today's events

If the process dies before step 5 completes, no cursor has moved: the next
run's `window_since` recomputes the identical `since` bound and replays the
same window (spec 6.1; downstream dedupes on `WatchdogEvent.ref`, so a
replay never double-counts in the rendered digest). If it dies after step 5
but before step 7, the digest is already published and correct; only the DM
ping is lost, which is recoverable by hand (`skos watchdog digest --no-send`
re-publishes idempotently... note: a second run after cursors already
advanced reads a NEW window, so a lost DM is not literally re-sendable by
re-running; it is recoverable by reading the published latest/ artifact
directly, which is the artifact of record).
"""
from __future__ import annotations

from typing import Optional

from .adapters import load_all
from .cursor import advance, window_since
from .deliver import send_digest_dm
from .digest import assemble_digest
from .gtd import file_findings
from .headline import render_headline_llm
from .port import AdapterRegistry, Window, collect_safe, now_iso, registry as _default_registry
from .port import source_ok
from .publish import publish_digest
from .render import render_markdown


def collect_all(*, now: Optional[str] = None,
                 registry: Optional[AdapterRegistry] = None) -> tuple[dict, list, str]:
    """Run every registered watchdog-source adapter over its own cursor
    window and return `(digest, source_names, run_until)`. Pure collection +
    assembly: no publish, no cursor advance, no DM -- callers (tests, or
    `run_digest_and_deliver` below) decide what happens with the result.

    `registry` defaults to the real shared `skos.watchdog.port.registry` (and
    triggers `load_all()` so the Phase-1 six are always present on that
    default path). Tests that want to exercise the pipeline against
    controlled fake sources -- without touching the shared registry every
    other test module registers real adapters onto -- pass their own
    isolated `AdapterRegistry` instead, in which case `load_all()` is
    skipped (nothing to load into an isolated registry).
    """
    if registry is None:
        load_all()
        registry = _default_registry
    sources = registry.available_for("watchdog-source")
    run_until = now or now_iso()

    all_events = []
    per_source: dict[str, dict] = {}
    earliest_since: Optional[str] = None

    for name in sources:
        window = window_since(name, now=run_until)
        if earliest_since is None or window.since < earliest_since:
            earliest_since = window.since
        adapter_cls = registry.lookup("watchdog-source", name)
        events = collect_safe(adapter_cls(), window)
        all_events.extend(events)
        per_source[name] = {
            "ok": source_ok(events, name),
            "events": len(events),
            "cursor": window.until,
        }

    overall_window = Window(since=earliest_since or run_until, until=run_until)
    digest = assemble_digest(all_events, window=overall_window, per_source=per_source)
    return digest, sources, run_until


def run_digest_and_deliver(*, date: Optional[str] = None, now: Optional[str] = None,
                            dry_run: bool = False, send: bool = True,
                            headline_timeout: float = 20.0,
                            registry: Optional[AdapterRegistry] = None) -> dict:
    """The full WD-3 pipeline. Returns a report dict:
    `{digest, markdown, sources, published, sent, artifacts}`.

    `dry_run=True` collects, assembles, and renders (including the headline
    call) but writes nothing and sends nothing -- a preview, matching the
    spec's `skos watchdog digest --dry-run`. `send=False` (`--no-send`)
    still publishes and advances cursors, only skipping the DM. `registry`
    is the same test seam `collect_all` exposes.
    """
    digest, sources, run_until = collect_all(now=now, registry=registry)
    if date:
        digest["date"] = date

    fallback_headline = str(digest.get("headline") or "")
    digest["headline"] = render_headline_llm(
        digest, fallback=fallback_headline, timeout=headline_timeout)
    markdown = render_markdown(digest)

    report = {
        "digest": digest, "markdown": markdown, "sources": sources,
        "published": False, "sent": False, "artifacts": {}, "gtd": {},
    }
    if dry_run:
        return report

    report["artifacts"] = publish_digest(digest, markdown, date=date)
    report["published"] = True

    # Cursors advance ONLY after the digest has actually landed (the publish
    # call above). This is the one line in this module that must never move
    # earlier than publish_digest().
    for name in sources:
        advance(name, run_until)

    # WD-8: problem findings become tracked GTD work, behind SKWATCHDOG_GTD
    # (default OFF) and standing down under fleet freeze. Runs after the
    # digest has landed and never raises, so filing can neither delay nor
    # break the report; with the flag off it writes nothing and the published
    # digest is byte-identical.
    report["gtd"] = file_findings(digest)

    if send:
        report["sent"] = send_digest_dm(digest)

    return report


__all__ = ["collect_all", "run_digest_and_deliver"]
