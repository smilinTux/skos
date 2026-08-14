"""The cursor store: the ONLY state skwatchdog owns.

Spec section 6.1: "the collector therefore keeps only two kinds of state:
cursors ... and digest artifacts." This module is the cursors half. One tiny
JSON file per source under `~/.skcapstone/watchdog/cursors/<source>.json`,
holding the last-digested-at mark for that source. There is no event
database here and this module never grows one: losing a cursor means
re-reading a window on the next run, never data loss, because everything
downstream dedupes on WatchdogEvent.ref.

Digest artifacts (the other half of section 6.1) are a WD-3 concern
(rendering + publish); this module produces the read window an adapter
needs, and advances the mark once a digest run has consumed it. It never
writes a digest artifact itself.

    from skos.watchdog.cursor import window_since, advance

    window = window_since("fleet")            # since last digest, to now
    events = collect_safe(adapter, window)
    ...                                        # fold events into a digest
    advance("fleet", window.until)             # only after the digest lands
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .port import Window, now_iso

#: A source with no prior cursor is read back this far on its first ever run.
DEFAULT_LOOKBACK = timedelta(hours=24)


def watchdog_home() -> Path:
    """Resolve (and create) the watchdog state root.

    Precedence: `SK_WATCHDOG_DIR` (explicit override, mirrors gtd_ingest's
    `SK_GTD_DIR`) > `<SKCAPSTONE_HOME>/watchdog` > `~/.skcapstone/watchdog`,
    matching the spec's `~/.skcapstone/watchdog/...` layout."""
    env = os.environ.get("SK_WATCHDOG_DIR")
    if env:
        d = Path(env).expanduser()
    else:
        home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
        d = home / "watchdog"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cursors_dir() -> Path:
    d = watchdog_home() / "cursors"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cursor_path(source: str) -> Path:
    # source names are adapter-controlled identifiers (fleet, chat.skchat,
    # ...), safe as filenames on every skos-supported platform; no escaping
    # needed beyond a defensive strip of path separators.
    safe = source.replace("/", "_").replace(os.sep, "_")
    return cursors_dir() / f"{safe}.json"


def read_cursor(source: str) -> Optional[str]:
    """The last-digested-at mark for `source`, or None if it has never been
    digested (or the cursor file is missing/corrupt). Never raises: a
    corrupt or absent cursor reads as "start of history", which is safe by
    construction (re-reading a window loses nothing, see module docstring)."""
    p = _cursor_path(source)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
        ts = data.get("last_digested_at")
        return str(ts) if ts else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def write_cursor(source: str, ts: str) -> None:
    """Atomically persist the last-digested-at mark for `source`. Same
    write-temp-fsync-replace-fsync-dir discipline as gtd_ingest._save, so a
    crash mid-write leaves either the old mark or the new one, never a
    truncated file."""
    d = cursors_dir()
    target = _cursor_path(source)
    payload = json.dumps({"source": source, "last_digested_at": ts}, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(target))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(str(d), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def advance(source: str, ts: str) -> None:
    """Advance `source`'s cursor to `ts`. Call this only after a digest run
    has actually consumed the window ending at `ts`; if the process crashes
    before this call, the next run's window_since() replays the same
    since-bound (idempotent, by design: downstream dedupes on `ref`)."""
    write_cursor(source, ts)


def window_since(source: str, *, now: Optional[str] = None,
                  lookback: timedelta = DEFAULT_LOOKBACK) -> Window:
    """The read window for `source`: from its cursor (or `lookback` before
    now, on a source's first ever run) to now. Does not touch the cursor;
    call advance() separately once the window's events have been folded
    into a digest that actually landed."""
    until = now or now_iso()
    since = read_cursor(source)
    if not since:
        until_dt = _parse(until) or datetime.now(timezone.utc)
        since = (until_dt - lookback).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Window(since=since, until=until)


def _parse(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
