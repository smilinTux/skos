"""skwatchdog Phase-1 collector adapters (WD-2): the first six read-only
sources registered on the watchdog-source port (``skos.watchdog.port.
registry``). Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section
6.3's Phase-1 adapter table.

    fleet_events    fleet/events.py::read() per node
    scheduler       the cron run-ledger (skharness.jobs staleness rule)
    itil            ITILManager: incidents, problems, changes + CAB/deploy windows
    coord_autocode  coordination/tasks + card_events + the autopilot run journal
    atlas           Atlas's published brief + parked decisions
    git             git log + gh pr list across configured repos

Every adapter here is READ-ONLY: no adapter writes to any source and no
adapter creates a store (the only state skwatchdog owns anywhere is the
cursor store in ``skos.watchdog.cursor``, WD-1's concern, untouched by
these). Each imports its optional sibling package (``skcapstone`` /
``skharness``), when it needs one, lazily inside ``collect()`` rather than
at module import time, so an absent sibling degrades that ONE adapter to a
``SourceUnavailable`` digest line via ``skos.watchdog.port.collect_safe``
instead of silently vanishing the adapter from the registry, or, worse,
breaking import of this whole package on a box that only has skos
installed.

    from skos.watchdog.adapters import load_all
    load_all()   # registers all six; each class is also importable directly,
                 # e.g. `from skos.watchdog.adapters.git import GitAdapter`
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


def load_all() -> list[str]:
    """Import and register every Phase-1 adapter on the watchdog-source port.

    Safe to call more than once (re-importing an already-imported module is a
    no-op; re-registering a name just overwrites the registry entry with the
    same class). Returns the registered names, sorted.
    """
    from . import atlas, coord_autocode, fleet_events, git, itil, scheduler  # noqa: F401
    from ..port import registry

    return registry.available_for("watchdog-source")
