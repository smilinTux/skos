"""KedbCanonCovered: cross-reference SKBrain's canon known-error pages against
the authoritative Known Error Database (KEDB) fold (coord card 8c6e05e3).

The authoritative KEDB is NOT a skos concept. It is folded, write-once JSON
records under ``<SKCAPSTONE_HOME>/coordination/itil/kedb/*.json``, owned and
written by ``skcoord.itil.ITILManager`` (re-exported byte-identically as
``skcapstone.itil``, see that shim's docstring). ``skcapstone``/``skcoord`` are
OPTIONAL sibling packages to skos -- absent in CI, present on a fully
provisioned node -- so every read here is lazy and defensively typed, mirroring
the exact pattern already used by ``skos.watchdog.adapters.itil.ItilAdapter``
(the only other place skos reads ITIL state).

This module never writes. ``ITILManager.search_kedb("")`` is used to enumerate
every fold entry through the manager's PUBLIC read API (an empty query is a
substring of everything, so it matches every entry) rather than its private
``_load_kedb`` -- the read-only adapter contract must not depend on a
leading-underscore implementation detail of a sibling package.

Coverage is bidirectional and enumerable, not a single boolean:

  * ``missing_from_canon`` -- authoritative KEDB ids with no SKBrain canon
    known-error page whose ``state_refs.kedb`` cites them. These are real
    known errors ATLAS/operators have folded that SKBrain has not yet
    canonized into a runbook-linked wiki page.
  * ``dangling_canon_refs`` -- SKBrain canon known-error page slugs whose
    ``state_refs.kedb`` is missing or cites an id the authoritative fold does
    not contain (a stale or typo'd reference).

``KedbCoverage.status`` is ``"Unknown"`` ONLY when the authoritative fold
itself is genuinely unreachable (the optional sibling package is not
installed, or its ITIL directories have never been initialized on this host,
or the fold directory cannot be listed). Every other outcome -- including a
missing or unparsable SKBrain canon -- is answered ``"True"``/``"False"``: the
authoritative side was reachable, so a real (if partial) verdict is owed
rather than a permanent "Unknown" escape hatch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from skos.brain.ops.parser import OpsParseError, walk_pages


def _skcapstone_home() -> Path:
    """The skcapstone home dir (``$SKCAPSTONE_HOME`` or ``~/.skcapstone``).

    Mirrors ``skos.watchdog.adapters.itil._skcapstone_home`` exactly: same env
    var, same default, so the two ITIL readers can never disagree about where
    the fold lives.
    """
    return Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))).expanduser()


def _default_kedb_ids(home: Path) -> list[str]:
    """Enumerate every authoritative KEDB entry id via the manager's public API.

    Raises:
        ModuleNotFoundError: the optional ``skcapstone``/``skcoord`` sibling is
            not installed in this environment (the realistic "unreachable"
            case on CI or a bare dev checkout -- see
            ``skos.watchdog.adapters.itil`` for the identical precedent).
        FileNotFoundError: the sibling IS installed but this host has never
            initialized its ITIL kedb directory (``ensure_dirs`` never ran).
        OSError: the kedb directory exists but cannot be listed (e.g. a
            permission error) -- a real but unreachable fold.
    """
    from skcapstone.itil import ITILManager  # optional sibling; see module docstring

    mgr = ITILManager(home)
    if not mgr.kedb_dir.is_dir():
        raise FileNotFoundError(str(mgr.kedb_dir))
    return [entry.id for entry in mgr.search_kedb("")]


@dataclass(frozen=True)
class KedbCoverage:
    """The result of comparing SKBrain canon against the authoritative KEDB.

    Attributes:
        status: ``"True"`` (full bidirectional coverage), ``"False"`` (the
            fold was reachable but a gap exists), or ``"Unknown"`` (the fold
            itself is genuinely unreachable -- see module docstring).
        reason: A one-line, human-readable summary (bounded gap preview).
        authoritative_count: Entries in the authoritative fold, when reachable.
        canon_count: SKBrain canon known-error pages inspected.
        missing_from_canon: Authoritative ids no canon page cites (sorted).
        dangling_canon_refs: Canon known-error page slugs whose kedb ref is
            missing or unresolved (sorted).
    """

    status: str
    reason: str
    authoritative_count: int = 0
    canon_count: int = 0
    missing_from_canon: tuple[str, ...] = field(default_factory=tuple)
    dangling_canon_refs: tuple[str, ...] = field(default_factory=tuple)


def _preview(items: tuple[str, ...], limit: int = 5) -> str:
    shown = ", ".join(items[:limit])
    more = f", +{len(items) - limit} more" if len(items) > limit else ""
    return f"{shown}{more}"


def compute_kedb_coverage(
    canon: Path,
    *,
    skcapstone_home: Path | None = None,
    kedb_loader: Callable[[Path], list[str]] = _default_kedb_ids,
) -> KedbCoverage:
    """Compute real KEDB canon coverage, or an honest Unknown when unreachable.

    Args:
        canon: The SKBrain canon root (as passed to ``walk_pages``).
        skcapstone_home: Override for the skcapstone home dir (tests inject a
            tmp_path); defaults to :func:`_skcapstone_home`.
        kedb_loader: Override for enumerating authoritative KEDB ids (tests
            inject a fake so the suite runs without the optional sibling
            installed); defaults to :func:`_default_kedb_ids`.

    Returns:
        A :class:`KedbCoverage` verdict. Never raises.
    """
    home = skcapstone_home or _skcapstone_home()
    try:
        authoritative_ids = kedb_loader(home)
    except ModuleNotFoundError as exc:
        return KedbCoverage(
            status="Unknown",
            reason=(
                "authoritative KEDB fold is unavailable: the optional skcapstone/"
                f"skcoord sibling package is not installed in this environment "
                f"(missing module {exc.name!r})"
            ),
        )
    except FileNotFoundError as exc:
        return KedbCoverage(
            status="Unknown",
            reason=(
                "authoritative KEDB fold is unavailable: the ITIL kedb directory "
                f"has not been initialized on this host ({exc})"
            ),
        )
    except OSError as exc:
        return KedbCoverage(
            status="Unknown",
            reason=(
                "authoritative KEDB fold is unavailable: "
                f"{type(exc).__name__} while listing it"
            ),
        )

    authoritative_set = set(authoritative_ids)

    try:
        pages = walk_pages(canon)
    except FileNotFoundError:
        return KedbCoverage(
            status="False",
            reason=(
                f"SKBrain canon is unavailable (canon dir missing at {canon}); "
                f"{len(authoritative_set)} authoritative KEDB entries are unverified"
            ),
            authoritative_count=len(authoritative_set),
        )
    except OpsParseError as exc:
        return KedbCoverage(
            status="False",
            reason=(
                f"SKBrain canon known-error pages could not be parsed ({exc}); "
                f"{len(authoritative_set)} authoritative KEDB entries are unverified"
            ),
            authoritative_count=len(authoritative_set),
        )

    canon_refs: dict[str, str | None] = {}
    for page in pages:
        if page.kind != "known-error":
            continue
        state_refs = page.frontmatter.get("state_refs")
        ref = state_refs.get("kedb") if isinstance(state_refs, dict) else None
        canon_refs[page.slug] = str(ref) if ref else None

    referenced = {ref for ref in canon_refs.values() if ref}
    missing_from_canon = tuple(sorted(authoritative_set - referenced))
    dangling_canon_refs = tuple(
        sorted(slug for slug, ref in canon_refs.items() if not ref or ref not in authoritative_set)
    )

    covered = len(authoritative_set) - len(missing_from_canon)
    reason = f"{covered}/{len(authoritative_set)} authoritative KEDB entries covered by canon"
    if missing_from_canon:
        reason += f"; {len(missing_from_canon)} uncovered ({_preview(missing_from_canon)})"
    if dangling_canon_refs:
        reason += (
            f"; {len(dangling_canon_refs)} canon page(s) with an unresolved kedb ref "
            f"({_preview(dangling_canon_refs)})"
        )

    status = "True" if not missing_from_canon and not dangling_canon_refs else "False"
    return KedbCoverage(
        status=status,
        reason=reason,
        authoritative_count=len(authoritative_set),
        canon_count=len(canon_refs),
        missing_from_canon=missing_from_canon,
        dangling_canon_refs=dangling_canon_refs,
    )


__all__ = ["KedbCoverage", "compute_kedb_coverage"]
