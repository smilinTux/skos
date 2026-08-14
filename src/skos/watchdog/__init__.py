"""skwatchdog core: the fleet narrative watchdog's foundation module.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, sections 3 to 5.
Plan: docs/plans/2026-08-10-skwatchdog-implementation.md, Phase 1 (WD-1).

This package (`skos.watchdog`) is the skeleton every source adapter, the
renderer, and the schedule cutover plug into. It builds exactly four things:

  1. WatchdogEvent (events.py): one normalized shape for every source.
  2. The adapter port + registry (port.py): WatchdogSourceAdapter,
     the `watchdog-source` AdapterRegistry, and the fail-safe collect_safe()
     wrapper.
  3. The cursor store (cursor.py): the only state this package owns, one
     JSON file per source holding its last-digested-at mark.
  4. Digest assembly (digest.py): assemble_digest() folds events into the
     digest shape the Code section's Digest tab already parses (card C-9).

WD-3 adds the four pieces this module skipped: the Markdown renderer
(render.py), the JSON+Markdown publisher beside the Atlas brief
(publish.py), the skgateway-backed headline with its deterministic fallback
(headline.py), and DM delivery through the existing Hermes path
(deliver.py) -- tied together by run.py's `run_digest_and_deliver`, what
`skos watchdog digest` calls. No schedule cutover (WD-4), no GTD/card
write-out (WD-8/WD-9), and no model call anywhere except headline.py's one
narrated-summary pass.
"""
from __future__ import annotations

from .events import WatchdogEvent, WatchdogLink, WatchdogEventError, SEVERITIES, source_unavailable
from .port import WatchdogSourceAdapter, Window, registry, collect_safe, source_ok, now_iso
from .cursor import (
    watchdog_home, cursors_dir, read_cursor, write_cursor, advance, window_since,
    DEFAULT_LOOKBACK,
)
from .digest import assemble_digest, render_headline
from .render import render_markdown, strip_banned_dashes, link_of
from .publish import publish_digest, digests_dir, latest_dir
from .headline import render_headline_llm
from .deliver import format_dm, send_digest_dm
from .run import collect_all, run_digest_and_deliver

__all__ = [
    "WatchdogEvent", "WatchdogLink", "WatchdogEventError", "SEVERITIES", "source_unavailable",
    "WatchdogSourceAdapter", "Window", "registry", "collect_safe", "source_ok", "now_iso",
    "watchdog_home", "cursors_dir", "read_cursor", "write_cursor", "advance", "window_since",
    "DEFAULT_LOOKBACK",
    "assemble_digest", "render_headline",
    "render_markdown", "strip_banned_dashes", "link_of",
    "publish_digest", "digests_dir", "latest_dir",
    "render_headline_llm",
    "format_dm", "send_digest_dm",
    "collect_all", "run_digest_and_deliver",
]
