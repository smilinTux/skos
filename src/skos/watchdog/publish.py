"""Publish the digest artifacts (WD-3): JSON + Markdown, dated plus latest/.

Spec section 6.1 + the WD-3 card: digest artifacts land at
``~/.skcapstone/watchdog/digests/YYYY-MM-DD.{json,md}`` plus a ``latest/``
publish dir, "using the SAME brief_publish atomic-write pattern and host as
the Atlas brief" (``skcapstone.operator_seat.brief_publish``). Reused, not
reimplemented: this module writes through
``skcoord.atomic_io.atomic_write_text``, the exact helper
``brief_publish.publish_brief`` already uses for ``index.html``/``brief.md``
under ``<fleet_root>/atlas/brief/`` on the same host. skcoord is an OPTIONAL
sibling package here, exactly like skcapstone is for the WD-2 source
adapters (imported lazily, never at module import time): when it is absent
this module falls back to an inline copy of the identical
write-temp-fsync-replace-fsync-dir sequence (the same one
``skos.watchdog.cursor.write_cursor`` already uses independently for the
cursor store), so publishing a digest never depends on an optional package
being installed -- it only PREFERS the shared helper when it is.

Both dated files (``<date>.json`` / ``<date>.md``) and the ``latest/`` files
(``digest.json`` / ``digest.md``) get the identical bytes; ``latest/`` is
what a served host (and the Flutter Digest tab, card C-9) fetches, the dated
file is the day's permanent record. The digest artifact is DERIVED and
regenerable (spec 6.1): deleting any of this loses nothing but the
rendering, since it is rebuilt from ``assemble_digest`` + the adapters'
``collect()`` output every run.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from .cursor import watchdog_home

DIGEST_JSON_NAME = "digest.json"
DIGEST_MD_NAME = "digest.md"


def _local_atomic_write_text(path: Path, text: str) -> None:
    """The write-temp-fsync-replace-fsync-dir sequence, inline, for when the
    skcoord sibling is not installed. Byte-identical algorithm to
    ``skcoord.atomic_io.atomic_write_text`` and to
    ``skos.watchdog.cursor.write_cursor``'s own copy of the same pattern; kept
    here only as the fail-safe path, never the preferred one (see module
    docstring: "reused, not reimplemented")."""
    directory = path.parent
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dir_fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _atomic_write_text(path: Path, text: str) -> None:
    """Prefer ``skcoord.atomic_io.atomic_write_text`` (brief_publish's own
    helper); fall back to the inline copy when skcoord is not installed."""
    try:
        from skcoord.atomic_io import atomic_write_text as _write
    except ImportError:
        _write = _local_atomic_write_text
    _write(path, text)


def digests_dir() -> Path:
    """``~/.skcapstone/watchdog/digests`` (or the SK_WATCHDOG_DIR override
    from ``cursor.watchdog_home()``): the same root the cursor store already
    resolves, so both halves of the collector's state (spec 6.1: "cursors ...
    and digest artifacts") live under one env-overridable root."""
    d = watchdog_home() / "digests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_dir() -> Path:
    d = digests_dir() / "latest"
    d.mkdir(parents=True, exist_ok=True)
    return d


def publish_digest(digest: Mapping, markdown: str, *, date: str | None = None) -> dict[str, Path]:
    """Write the digest JSON + Markdown, dated plus latest/, atomically.

    Args:
        digest: the assembled (and headline-finalized) digest dict, written
            byte-for-byte as JSON -- the exact shape card C-9 parses.
        markdown: the already-rendered Markdown (``render.render_markdown``).
        date: overrides the dated filename's date; defaults to
            ``digest["date"]``.

    Returns a dict of the four paths written: ``dated_json``, ``dated_md``,
    ``latest_json``, ``latest_md``. This is the point at which the spec's
    "a digest actually lands" is true (WD-1's ``advance()`` docstring); call
    it BEFORE advancing any source's cursor, never after.
    """
    d = date or str(digest.get("date") or "unknown-date")
    payload = json.dumps(digest, indent=2, ensure_ascii=False, sort_keys=True)

    dated_dir = digests_dir()
    dated_json = dated_dir / f"{d}.json"
    dated_md = dated_dir / f"{d}.md"
    _atomic_write_text(dated_json, payload)
    _atomic_write_text(dated_md, markdown)

    latest = latest_dir()
    latest_json = latest / DIGEST_JSON_NAME
    latest_md = latest / DIGEST_MD_NAME
    _atomic_write_text(latest_json, payload)
    _atomic_write_text(latest_md, markdown)

    return {
        "dated_json": dated_json, "dated_md": dated_md,
        "latest_json": latest_json, "latest_md": latest_md,
    }


__all__ = ["publish_digest", "digests_dir", "latest_dir", "DIGEST_JSON_NAME", "DIGEST_MD_NAME"]
