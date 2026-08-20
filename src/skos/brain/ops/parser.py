"""skos.brain.ops.parser — the ops-wiki markdown parser (SB1.1, pure).

Given a Karpathy-style ops page (YAML frontmatter + markdown body with
``[[wikilink]]`` refs), extract the bare slug, kind, title, body, typed
frontmatter edges, and outbound wikilinks into an ``OpsPage``.

Slug convention (spec 3.4): the bare slug is the lowercased filename stem, and
it is the flat-namespace id that every ``[[wikilink]]`` resolves against. If the
page carries a frontmatter ``id``, it MUST equal the filename slug (this is the
maintenance-scan invariant that kills the gbrain dark-graph bug by construction).

Pure: ``parse_page`` and ``extract_wikilinks`` do no I/O. ``parse_file`` and
``walk_pages`` are the thin filesystem wrappers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from skos.brain.ops.models import OpsEdge, OpsPage

# Frontmatter block: --- ... --- then body.
_FM_RE = re.compile(r"^\s*---\n(.*?)\n---\n?(.*)", re.DOTALL)

# [[slug]] or [[slug|display]] — capture the bare slug (left of an optional pipe).
_WIKILINK_RE = re.compile(r"\[\[\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]")


class OpsParseError(ValueError):
    """Raised when an ops page cannot be parsed or violates the slug invariant."""


def slug_from_path(path: str | Path) -> str:
    """Bare slug = lowercased filename stem (spec 3.4).

    Directory placement is presentation only and never enters the slug.
    """
    stem = Path(path).stem
    return stem.lower()


def extract_wikilinks(body: str) -> list[str]:
    """Return the ordered, deduplicated bare-slug targets of ``[[wikilinks]]``.

    ``[[slug|display]]`` resolves to ``slug``. Targets are lowercased so link
    resolution is case-insensitive against the flat namespace id set.
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).strip().lower()
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def parse_page(text: str, *, slug: str) -> OpsPage:
    """Parse ops-page markdown into an ``OpsPage`` (pure; no I/O).

    Args:
        text: full page markdown (frontmatter + body).
        slug: the bare slug (from the filename); the id every wikilink resolves
            against and the ops.wiki_nodes primary key.

    Raises:
        OpsParseError: on missing/invalid frontmatter, a missing ``type``, or a
            frontmatter ``id`` that disagrees with ``slug`` (the 3.4 invariant).
    """
    m = _FM_RE.match(text)
    if not m:
        raise OpsParseError(
            "No YAML frontmatter block found; ops pages must start with --- ... --- frontmatter."
        )
    fm_text, body = m.group(1), m.group(2)

    try:
        fm: Any = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise OpsParseError(f"YAML parse error in frontmatter: {exc}") from exc
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        raise OpsParseError(f"Frontmatter must be a YAML mapping, got {type(fm).__name__}")

    # Slug invariant (spec 3.4): a declared id must equal the filename slug.
    declared_id = fm.get("id")
    if declared_id is not None and str(declared_id).lower() != slug.lower():
        raise OpsParseError(
            f"Frontmatter id {declared_id!r} does not match filename slug {slug!r} "
            "(spec 3.4: one flat slug space; filename stem IS the id)."
        )

    kind = fm.get("type")
    if not kind:
        raise OpsParseError(f"Page {slug!r} is missing required frontmatter field 'type'.")

    body = body.lstrip("\n")
    title = fm.get("title") or slug
    namespace = fm.get("namespace") or "ops"
    lifecycle = fm.get("lifecycle") or "canon"
    origin = fm.get("origin") or "git"

    edges = _parse_edges(slug, fm.get("edges"))
    wikilinks = extract_wikilinks(body)

    return OpsPage(
        slug=slug,
        kind=str(kind),
        title=str(title),
        body_md=body,
        namespace=str(namespace),
        origin=str(origin),
        lifecycle=str(lifecycle),
        frontmatter=fm,
        edges=edges,
        wikilinks=wikilinks,
    )


def _parse_edges(src_slug: str, raw: Any) -> list[OpsEdge]:
    """Normalise the frontmatter ``edges:`` list into typed OpsEdge rows."""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise OpsParseError(f"Page {src_slug!r}: frontmatter 'edges' must be a list.")
    edges: list[OpsEdge] = []
    for e in raw:
        if isinstance(e, dict):
            target = e.get("target")
            if not target:
                raise OpsParseError(f"Page {src_slug!r}: an edge is missing 'target'.")
            etype = e.get("type") or "links_to"
            edges.append(
                OpsEdge(
                    src=src_slug,
                    dst=str(target).lower(),
                    edge_type=str(etype),
                    provenance="definition",
                )
            )
        elif isinstance(e, str):
            # shorthand "target:type" or "target"
            parts = e.split(":", 1)
            edges.append(
                OpsEdge(
                    src=src_slug,
                    dst=parts[0].strip().lower(),
                    edge_type=(parts[1].strip() if len(parts) > 1 else "links_to"),
                    provenance="definition",
                )
            )
        else:
            raise OpsParseError(f"Page {src_slug!r}: unexpected edge format {e!r}.")
    return edges


# ---------------------------------------------------------------------------
# Thin filesystem wrappers (the only I/O in this module)
# ---------------------------------------------------------------------------


def parse_file(path: str | Path) -> OpsPage:
    """Read an ops page file and parse it, deriving the slug from the filename."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Ops page file not found: {p}")
    return parse_page(p.read_text(encoding="utf-8"), slug=slug_from_path(p))


def walk_pages(root: str | Path) -> list[OpsPage]:
    """Parse every ``*.md`` page under *root* recursively (sorted, index files skipped).

    Files whose stem starts with ``_`` (``_index.md``) or the repo catalog files
    (``index.md``, ``log.md``, ``README.md``, ``CLAUDE.md``) are skipped: they are
    navigation, not entity pages. Unparseable pages raise, so a malformed page is
    a loud failure the maintenance scan can act on, not a silent drop.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise FileNotFoundError(f"Ops pages root not found: {root_path}")
    skip_stems = {"index", "log", "readme", "claude", "agents"}
    pages: list[OpsPage] = []
    for md in sorted(root_path.rglob("*.md")):
        if md.stem.startswith("_") or md.stem.lower() in skip_stems:
            continue
        pages.append(parse_file(md))
    return pages
