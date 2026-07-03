"""gtd-ingest PULL adapters — poll an external source, emit GtdCapture, drain into
the unified GTD via the capture() sink. Registered on the `gtd-ingest` port.

    skos ingest calendar     # drain the calendar adapter once
    skos ingest telegram
"""
from __future__ import annotations

from ..gtd_ingest import registry


def _adapters() -> dict:
    # imported lazily so a missing optional dep doesn't break the whole package
    from .calendar import CalendarAdapter
    from .telegram import TelegramAdapter
    from .email import EmailAdapter
    out = {}
    for cls in (CalendarAdapter, TelegramAdapter, EmailAdapter):
        try:
            registry.register(cls)
        except Exception:
            pass
        out[cls.name] = cls
    return out


def drain(name: str) -> int:
    """Instantiate the named pull adapter and drain it once. Returns # captured."""
    cls = _adapters().get(name)
    if cls is None:
        raise SystemExit(f"unknown gtd-ingest adapter: {name} (have: {', '.join(_adapters())})")
    return len(cls().drain())
