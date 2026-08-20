"""skos.brain.ops.writer — the idempotent upsert PLAN + backend (SB1.1).

Turns parsed pages into an ``UpsertPlan``: pure data describing which
ops.wiki_nodes rows to upsert (with their chunks and links), which are unchanged
(skipped by content-hash), and which to delete (present in the store, gone from
canon). No I/O: ``build_plan`` diffs against a plain ``{node_id: content_hash}``
map, so the whole projection decision is unit-testable without a database.

Execution is isolated behind the ``WriterBackend`` protocol and guarded by an
explicit ``commit`` flag, so a dry run touches nothing and the unit tests use an
in-memory stub. The live skmem-pg backend + ``skbrain sync`` CLI is SB1.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from skos.brain.ops.chunker import chunk_page
from skos.brain.ops.embed import Embedder, embed_chunks
from skos.brain.ops.models import OpsChunk, OpsEdge, OpsPage


@dataclass
class NodeUpsert:
    """One node to write: the page row + its chunk rows + its link rows."""

    node: OpsPage
    chunks: list[OpsChunk] = field(default_factory=list)
    links: list[OpsEdge] = field(default_factory=list)


@dataclass
class UpsertPlan:
    """The full projection decision for a batch of pages (pure data)."""

    upserts: list[NodeUpsert] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)


@dataclass
class NodeState:
    """A node's currently-projected identity, as reported by the backend."""

    node_id: str
    content_hash: str


@dataclass
class ApplyResult:
    """Summary of an ``apply_plan`` run (what was, or would be, written)."""

    upserted: int
    deleted: int
    unchanged: int
    committed: bool


@runtime_checkable
class WriterBackend(Protocol):
    """The projector's write surface onto the ops schema (SB1.3 implements it live)."""

    def existing_hashes(self) -> dict[str, str]: ...

    def upsert_node(self, node: OpsPage, chunks: list[OpsChunk], links: list[OpsEdge]) -> None: ...

    def delete_node(self, node_id: str) -> None: ...


def build_plan(pages: list[OpsPage], existing_hashes: dict[str, str]) -> UpsertPlan:
    """Diff *pages* against *existing_hashes* into an idempotent ``UpsertPlan`` (pure).

    - a page whose content_hash matches the stored hash is ``unchanged`` (skipped);
    - a new or changed page is an upsert, carrying its chunks (embeddings unset)
      and its links (typed frontmatter edges + body wikilinks, deduped);
    - a stored node_id absent from *pages* is a delete (canon removed it).

    Deterministic: upserts follow the input page order, deletes are sorted.
    """
    plan = UpsertPlan()
    seen: set[str] = set()
    for page in pages:
        seen.add(page.slug)
        if existing_hashes.get(page.slug) == page.content_hash:
            plan.unchanged.append(page.slug)
            continue
        plan.upserts.append(
            NodeUpsert(
                node=page,
                chunks=chunk_page(page),
                links=page.all_links(),
            )
        )
    plan.deletes = sorted(node_id for node_id in existing_hashes if node_id not in seen)
    return plan


def embed_plan(plan: UpsertPlan, embedder: Embedder) -> UpsertPlan:
    """Return a copy of *plan* with embeddings filled on every upsert's chunks.

    Isolated I/O (the injected embedder). Unchanged/deletes pass through; a plan
    with no upserts makes no embedder call.
    """
    new_upserts = [
        NodeUpsert(node=u.node, chunks=embed_chunks(u.chunks, embedder), links=u.links)
        for u in plan.upserts
    ]
    return UpsertPlan(
        upserts=new_upserts,
        unchanged=list(plan.unchanged),
        deletes=list(plan.deletes),
    )


def apply_plan(plan: UpsertPlan, backend: WriterBackend, *, commit: bool = False) -> ApplyResult:
    """Execute *plan* against *backend*, guarded by *commit*.

    ``commit=False`` (default) is a dry run: nothing is written, but the returned
    ``ApplyResult`` still reports what would happen. ``commit=True`` performs the
    upserts then the deletes. The caller owns the transaction boundary; this
    function only sequences the backend calls.
    """
    if commit:
        for up in plan.upserts:
            backend.upsert_node(up.node, up.chunks, up.links)
        for node_id in plan.deletes:
            backend.delete_node(node_id)
    return ApplyResult(
        upserted=len(plan.upserts),
        deleted=len(plan.deletes),
        unchanged=len(plan.unchanged),
        committed=commit,
    )


def project(
    pages: list[OpsPage],
    backend: WriterBackend,
    *,
    embedder: Embedder | None = None,
    commit: bool = False,
) -> ApplyResult:
    """Convenience: read existing hashes from *backend*, diff, embed, apply.

    Pure pieces (``build_plan``) stay separately testable; this wires them for a
    caller that has a backend in hand. Embedding runs only when there is work and
    an embedder is supplied. Still guarded by *commit*.
    """
    plan = build_plan(pages, backend.existing_hashes())
    if plan.upserts and embedder is not None:
        plan = embed_plan(plan, embedder)
    return apply_plan(plan, backend, commit=commit)
