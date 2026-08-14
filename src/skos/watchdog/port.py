"""The watchdog-source adapter port + registry.

Copies the GtdSourceAdapter shape (skos.gtd_ingest) on the skos adapter
registry (skos.adapter.AdapterRegistry), the same pattern already used for
calendar/email/order/telegram. Adding a source is one adapter, registered
here, never a parallel store:

    from skos.watchdog.port import WatchdogSourceAdapter, registry, Window

    @registry.register
    class FleetEventsAdapter(WatchdogSourceAdapter):
        name = "fleet"
        def collect(self, window: Window) -> list[WatchdogEvent]:
            ...

This module builds no adapters itself (that is WD-2). It builds the port
every adapter plugs into, plus the fail-safe wrapper: spec section 6.3
requires that any exception raised out of an adapter's `collect()` degrades
to a single synthetic `SourceUnavailable` event rather than an exception or a
missing digest, mirroring the fail-safe posture already in
skos.operator_probe (every probe reports healthy rather than raising when its
source is unreachable).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..adapter import Adapter, AdapterRegistry
from .events import WatchdogEvent, source_unavailable


def now_iso() -> str:
    """UTC now, seconds precision, Z-suffixed: the timestamp shape used
    throughout the spec's WatchdogEvent examples."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Window:
    """The read window a digest run passes to every adapter: everything
    since the source's cursor, up to now. Both bounds are ISO8601 UTC
    strings so a Window round-trips into the digest's `window: {from, to}`
    field (spec 6.4) without conversion."""
    since: str
    until: str

    def to_dict(self) -> dict:
        return {"from": self.since, "to": self.until}


class WatchdogSourceAdapter(Adapter):
    """Base for every watchdog source. A PULL shape only: `collect(window)`
    returns everything new in that window. There is no push/emit side
    (unlike GtdSourceAdapter) because the watchdog never owns events; it
    only reads them from their owners at digest time."""
    capability = "watchdog-source"
    name = ""

    def collect(self, window: Window) -> list[WatchdogEvent]:  # pragma: no cover (abstract)
        raise NotImplementedError(f"{type(self).__name__} must implement collect()")


#: The port: source adapters register here, exactly like gtd_ingest.registry.
registry = AdapterRegistry()


def collect_safe(adapter: WatchdogSourceAdapter, window: Window) -> list[WatchdogEvent]:
    """Run one adapter's collect() and never let it raise, mirroring
    operator_probe's fail-safe posture. On any exception (including a
    misbehaving adapter returning something that is not a list), the source
    degrades to exactly one synthetic SourceUnavailable event so a broken
    source is a visible digest line, never a missing digest and never an
    unhandled exception that would take the whole run down with it."""
    try:
        events = adapter.collect(window)
        if events is None:
            return []
        return list(events)
    except Exception as exc:  # noqa: BLE001 - deliberate: any adapter failure fails safe
        return [source_unavailable(adapter.name or type(adapter).__name__,
                                    ts=now_iso(), error=str(exc))]


def source_ok(events: list[WatchdogEvent], source: str) -> bool:
    """A source read as healthy unless its own collect_safe() run produced
    the synthetic SourceUnavailable marker for it. Used to fill the digest's
    per_source.<name>.ok field (spec 6.4)."""
    return not any(e.source == source and e.kind == "SourceUnavailable" for e in events)
