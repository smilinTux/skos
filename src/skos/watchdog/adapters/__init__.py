"""skwatchdog collector adapters, registered on the watchdog-source port
(``skos.watchdog.port.registry``). Spec:
docs/specs/2026-08-10-skwatchdog-architecture.md.

Phase 1 (WD-2), section 6.3's table:

    fleet_events    fleet/events.py::read() per node
    scheduler       the cron run-ledger (skharness.jobs staleness rule)
    itil            ITILManager: incidents, problems, changes + CAB/deploy windows
    coord_autocode  coordination/tasks + card_events + the autopilot run journal
    atlas           Atlas's published brief + parked decisions
    git             git log + gh pr list across configured repos

Phase 2 (WD-7 + WD-6), sections 6.5 and 7:

    grading         Lumina's outbound skchat/Telegram replies, graded
                     against a versioned rubric via skos.watchdog.grader
    chat.skchat     skchat thread activity (list_threads + the window read)
    chat.telegram   the Telegram window via `skcapstone telegram poll`
    email           the 4-C Gmail lanes via the gog CLI

Phase 3 (WD-12), section 11:

    sites           reachability + outbound-link checks over the configured
                     static marketing sites (`SKWATCHDOG_SITES`), the only
                     adapter that makes a network request; see
                     adapters/sites.py's module docstring for the run-budget
                     and blip-vs-outage rules that come with that

Card 04ad64d7 ("Tier A does not mean running"):

    systemd_tier_a  a STATE-BASED reconcile of every SERVICE_UNIT_STANDARD
                     Tier A unit against "should be running". Tier A's
                     `Restart=on-failure` does not cover a deliberate
                     `systemctl stop`, so the one source here that cannot be
                     event-driven: a stopped unit emits nothing, so nothing
                     fires, so it must be periodically COMPARED instead of
                     waited for

The three WD-6 sources above carry SUMMARIES AND REFS ONLY: counts, labels,
thread/chat identity and a deep link back to the real message. No message
body, no email body, no subject line and no thread title ever reaches a
WatchdogEvent, because each of those adapters drops content at its READ
BOUNDARY rather than filtering it on the way out. The digest is published
and served over HTTP; a body copied into it is a copy of Chef's private
correspondence sitting in a served file. See each module's docstring.

Every adapter here is READ-ONLY: no adapter writes to any source and no
adapter creates a store (the only state skwatchdog owns anywhere is the
cursor store in ``skos.watchdog.cursor``, WD-1's concern, untouched by
these). Each imports its optional sibling package (``skcapstone`` /
``skharness`` / ``skchat``), when it needs one, lazily inside ``collect()``
rather than at module import time, so an absent sibling degrades that ONE
adapter to a ``SourceUnavailable`` digest line via
``skos.watchdog.port.collect_safe`` instead of silently vanishing the
adapter from the registry, or, worse, breaking import of this whole package
on a box that only has skos installed.

    from skos.watchdog.adapters import load_all
    load_all()   # registers all eleven; each class is also importable
                 # directly, e.g. `from skos.watchdog.adapters.git import
                 # GitAdapter`
"""
from __future__ import annotations

#: name -> the source's one-line role, for anything that wants to render the
#: roster without importing every adapter module (e.g. a future `skos
#: watchdog status`).
PHASE1_SOURCES = (
    "fleet",
    "scheduler",
    "itil",
    "coord_autocode",
    "atlas",
    "git",
)

#: Phase 2 additions: WD-7's `grading`, plus WD-6's chat and email sources.
PHASE2_SOURCES = (
    "grading",
    "chat.skchat",
    "chat.telegram",
    "email",
)

#: Phase 3 additions: WD-12's `sites` (the only network-reaching adapter).
PHASE3_SOURCES = (
    "sites",
)

#: Card 04ad64d7: the Tier A liveness reconcile (the only state-based source).
TIER_A_SOURCES = (
    "systemd_tier_a",
)


def load_all() -> list[str]:
    """Import and register every adapter on the watchdog-source port
    (PHASE1_SOURCES + PHASE2_SOURCES + PHASE3_SOURCES + TIER_A_SOURCES).

    Safe to call more than once (re-importing an already-imported module is a
    no-op; re-registering a name just overwrites the registry entry with the
    same class). Returns the registered names, sorted.
    """
    from . import (atlas, chat_skchat, chat_telegram, coord_autocode, email,  # noqa: F401
                   fleet_events, git, grading, itil, scheduler, sites,  # noqa: F401
                   systemd_tier_a)  # noqa: F401
    from ..port import registry

    return registry.available_for("watchdog-source")
