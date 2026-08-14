"""Resolve the `gog` (gogcli) binary: one place, for every skos caller.

Three modules each carried their own
``GOG = os.environ.get("GOG", "/home/linuxbrew/.linuxbrew/bin/gog")``. That
default was wrong in three separate ways:

* **A fresh install has no Homebrew.** skos would fail deep inside an adapter
  with a "no such file" on a path the operator never chose, instead of saying
  gogcli is not installed and where to get it.
* **It pinned us to whoever put a binary there.** On this box that was the
  `openclaw/tap` Homebrew tap, shipping v0.12.0 while upstream was on v0.37.0.
  Being 25 versions behind is how the calendar adapter silently ingested 10 of
  234 events: the truncation hint that would have exposed it landed upstream
  four months after the pinned build.
* **Three copies drift.** Move the binary and all three break independently.
  Same shape as the GTD file-set constant (card 3df69da1), where a lookup
  universe and a dedupe universe disagreed because each module defined its own.

Order: explicit ``GOG`` env override, then ``PATH``, then known install
locations. PATH before the fallbacks on purpose, so an operator's own install
always beats a packaged one.

Upstream: https://github.com/steipete/gogcli (official release binaries, with
checksums, for linux/darwin amd64+arm64).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

#: Last-resort locations, tried in order and only when PATH has nothing.
#: Deliberately NOT authoritative: an operator's PATH install wins over these.
_FALLBACKS: tuple[str, ...] = (
    str(Path.home() / ".local" / "bin" / "gog"),
    str(Path.home() / ".skenv" / "bin" / "gog"),
    "/usr/local/bin/gog",
    "/home/linuxbrew/.linuxbrew/bin/gog",  # legacy Homebrew install
)

_INSTALL_HINT = (
    "gogcli (the `gog` binary) was not found. Install it from "
    "https://github.com/steipete/gogcli (official release binaries with "
    "checksums), put it on PATH, or point the GOG environment variable at it."
)


class GogNotInstalled(RuntimeError):
    """gogcli is not installed, or not where skos can see it."""


def find_gog() -> str | None:
    """The gog binary path, or None. Never raises."""
    override = (os.environ.get("GOG") or "").strip()
    if override:
        # An explicit override is honoured even if it is not executable: the
        # operator said to use this, so a broken one should surface as ITSELF
        # rather than be silently replaced by something else.
        return override

    found = shutil.which("gog")
    if found:
        return found

    for candidate in _FALLBACKS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_gog() -> str:
    """The gog binary path, raising :class:`GogNotInstalled` with a fix."""
    found = find_gog()
    if found is None:
        raise GogNotInstalled(_INSTALL_HINT)
    return found


def gog_available() -> bool:
    """True when gog can be resolved. For doctor/status checks."""
    return find_gog() is not None
