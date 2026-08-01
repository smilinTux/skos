"""skos.brain.ops.models — the ops-namespace data contract (SB1.1, pure).

These dataclasses mirror the LIVE skmem-pg `ops` schema
(skmemory/deploy/skmem-pg/03-ops-namespace.sql) exactly enough for the
projector to build idempotent upsert plans without a DB in the loop:

    OpsPage  -> ops.wiki_nodes  (one row per canon page / projected record)
    OpsChunk -> ops.wiki_chunks (retrieval unit, <=512 mxbai tokens)
    OpsEdge  -> ops.links       (resolved wikilinks + typed edges)

Pure by construction: no I/O, no DB, no HTTP. The parser builds OpsPage, the
chunker builds OpsChunk, the writer turns them into an UpsertPlan.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# Valid `kind` values (ops.wiki_nodes.kind). Canon page kinds + projected
# state kinds, matching the schema comment verbatim.
CANON_KINDS = frozenset(
    {"runbook", "ci", "service", "node", "known-error", "postmortem", "synthesis"}
)
STATE_KINDS = frozenset({"incident", "problem", "change"})
ALL_KINDS = CANON_KINDS | STATE_KINDS

# ops.wiki_nodes.origin CHECK constraint.
VALID_ORIGINS = frozenset({"git", "repo", "itil", "cmdb"})
# ops.wiki_nodes.lifecycle CHECK constraint.
VALID_LIFECYCLES = frozenset({"draft", "reviewed", "canon"})
# ops.links.provenance CHECK constraint.
VALID_PROVENANCE = frozenset({"definition", "observed"})


@dataclass(frozen=True)
class OpsEdge:
    """A typed edge -> one row in ops.links.

    From a page's frontmatter ``edges:`` (typed) or a body ``[[wikilink]]``
    (edge_type ``links_to``). ``provenance`` is ``definition`` for canon-derived
    edges; ``observed`` is reserved for state-store projections (SB1.2).
    """

    src: str
    dst: str
    edge_type: str = "links_to"
    provenance: str = "definition"


@dataclass
class OpsPage:
    """One canon page -> one ops.wiki_nodes row.

    ``slug`` is the bare filename slug (spec 3.4): the flat namespace id that
    every ``[[wikilink]]`` resolves against. ``edges`` are the typed frontmatter
    edges; ``wikilinks`` are the bare-slug targets found in the body.
    """

    slug: str
    kind: str
    title: str
    body_md: str
    namespace: str = "ops"
    origin: str = "git"
    lifecycle: str = "canon"
    frontmatter: dict[str, Any] = field(default_factory=dict)
    edges: list[OpsEdge] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        """Stable sha256 over the projector-relevant content.

        The idempotence key for ops.wiki_nodes.content_hash: any change to
        body, title, kind, lifecycle, origin, frontmatter, edges, or wikilinks
        yields a new hash, so the projector re-embeds exactly when it must and
        no-ops otherwise.
        """
        payload = {
            "slug": self.slug,
            "kind": self.kind,
            "title": self.title,
            "body_md": self.body_md,
            "namespace": self.namespace,
            "origin": self.origin,
            "lifecycle": self.lifecycle,
            "frontmatter": self.frontmatter,
            "edges": [(e.src, e.dst, e.edge_type, e.provenance) for e in self.edges],
            "wikilinks": self.wikilinks,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def all_links(self) -> list[OpsEdge]:
        """Typed frontmatter edges + body wikilinks (as ``links_to`` edges).

        Deduplicated on (src, dst, edge_type, provenance) to match the
        ops.links primary key; a wikilink that duplicates a typed edge does not
        produce a second row.
        """
        out: list[OpsEdge] = []
        seen: set[tuple[str, str, str, str]] = set()
        for e in self.edges:
            key = (e.src, e.dst, e.edge_type, e.provenance)
            if key not in seen:
                seen.add(key)
                out.append(e)
        for target in self.wikilinks:
            edge = OpsEdge(
                src=self.slug, dst=target, edge_type="links_to", provenance="definition"
            )
            key = (edge.src, edge.dst, edge.edge_type, edge.provenance)
            if key not in seen:
                seen.add(key)
                out.append(edge)
        return out


@dataclass(frozen=True)
class OpsChunk:
    """A retrieval unit -> one ops.wiki_chunks row.

    ``embedding`` is None until the embedder fills it (SB1.1 keeps parse/chunk
    pure; embedding is the one isolated I/O step).
    """

    node_id: str
    ord: int
    content: str
    embedding: list[float] | None = None
