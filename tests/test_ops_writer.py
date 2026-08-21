"""Tests for skos.brain.ops.writer — the idempotent upsert PLAN (SB1.1).

The plan is pure data (content-hash diffing; node/chunk/link ops as objects, no
I/O). ``apply_plan`` executes it against a ``WriterBackend`` and is guarded by an
explicit ``commit`` flag so a live DB is never required for the unit tests. Here
the backend is an in-memory stub.
"""

from skos.brain.ops.embed import Embedder  # noqa: F401  (protocol used via stub)
from skos.brain.ops.models import OpsChunk, OpsPage
from skos.brain.ops.parser import extract_wikilinks
from skos.brain.ops.writer import (
    UpsertPlan,
    WriterBackend,
    apply_plan,
    build_plan,
    embed_plan,
)


def _page(slug="runbook-x", body="## A\n\nalpha.\n\n## B\n\nbravo.\n", **kw):
    # Mirror the parser: body wikilinks are populated on the OpsPage.
    kw.setdefault("wikilinks", extract_wikilinks(body))
    return OpsPage(slug=slug, kind="runbook", title="X", body_md=body, **kw)


class StubBackend:
    """In-memory WriterBackend: records upserts/deletes, serves existing hashes."""

    def __init__(self, existing=None):
        self._existing = dict(existing or {})
        self.upserts: list[tuple[str, int, int]] = []  # (node_id, nchunks, nlinks)
        self.deletes: list[str] = []
        self.observed: list[str] = []
        self.committed = False

    def existing_hashes(self) -> dict[str, str]:
        return dict(self._existing)

    def upsert_node(self, node: OpsPage, chunks, links) -> None:
        self.upserts.append((node.slug, len(chunks), len(links)))

    def delete_node(self, node_id: str) -> None:
        self.deletes.append(node_id)

    def mark_observed(self, node_ids: list[str]) -> None:
        self.observed.extend(node_ids)


class StubEmbedder:
    def embed(self, texts):
        return [[float(i)] * 1024 for i, _ in enumerate(texts)]


# ---------------------------------------------------------------------------
# build_plan — content-hash diffing (pure)
# ---------------------------------------------------------------------------


def test_build_plan_all_new_when_no_existing():
    pages = [_page("runbook-a"), _page("runbook-b")]
    plan = build_plan(pages, existing_hashes={})
    assert isinstance(plan, UpsertPlan)
    assert {u.node.slug for u in plan.upserts} == {"runbook-a", "runbook-b"}
    assert plan.unchanged == []
    assert plan.deletes == []


def test_build_plan_skips_unchanged_by_hash():
    p = _page("runbook-a")
    plan = build_plan([p], existing_hashes={"runbook-a": p.content_hash})
    assert plan.upserts == []
    assert plan.unchanged == ["runbook-a"]
    assert plan.deletes == []


def test_build_plan_upserts_on_hash_change():
    p = _page("runbook-a")
    plan = build_plan([p], existing_hashes={"runbook-a": "stale-hash"})
    assert [u.node.slug for u in plan.upserts] == ["runbook-a"]
    assert plan.unchanged == []


def test_build_plan_deletes_removed_pages():
    p = _page("runbook-a")
    plan = build_plan(
        [p], existing_hashes={"runbook-a": p.content_hash, "runbook-gone": "h"}
    )
    assert plan.deletes == ["runbook-gone"]
    assert plan.unchanged == ["runbook-a"]


def test_build_plan_carries_chunks_and_links():
    p = _page("runbook-a", body="## Steps\n\ndo it. See [[ke-x]].\n")
    plan = build_plan([p], existing_hashes={})
    up = plan.upserts[0]
    assert up.chunks, "chunks should be generated from the body"
    assert all(isinstance(c, OpsChunk) for c in up.chunks)
    assert all(c.node_id == "runbook-a" for c in up.chunks)
    # the body wikilink becomes a links_to edge
    assert any(e.dst == "ke-x" and e.edge_type == "links_to" for e in up.links)


def test_build_plan_upsert_chunks_have_no_embeddings_yet():
    plan = build_plan([_page("runbook-a")], existing_hashes={})
    assert all(c.embedding is None for c in plan.upserts[0].chunks)


def test_build_plan_is_deterministic():
    pages = [_page("runbook-a"), _page("runbook-b")]
    a = build_plan(pages, existing_hashes={})
    b = build_plan(pages, existing_hashes={})
    assert [u.node.slug for u in a.upserts] == [u.node.slug for u in b.upserts]


# ---------------------------------------------------------------------------
# embed_plan — fills chunk embeddings on upserts (isolated I/O via stub)
# ---------------------------------------------------------------------------


def test_embed_plan_fills_embeddings():
    plan = build_plan([_page("runbook-a")], existing_hashes={})
    embedded = embed_plan(plan, StubEmbedder())
    up = embedded.upserts[0]
    assert up.chunks
    assert all(c.embedding is not None and len(c.embedding) == 1024 for c in up.chunks)
    # deletes/unchanged untouched
    assert embedded.deletes == plan.deletes
    assert embedded.unchanged == plan.unchanged


def test_embed_plan_no_upserts_is_noop():
    p = _page("runbook-a")
    plan = build_plan([p], existing_hashes={"runbook-a": p.content_hash})
    embedded = embed_plan(plan, StubEmbedder())
    assert embedded.upserts == []


# ---------------------------------------------------------------------------
# apply_plan — guarded by commit flag; stub backend (no live DB)
# ---------------------------------------------------------------------------


def test_stub_backend_conforms_to_protocol():
    assert isinstance(StubBackend(), WriterBackend)


def test_apply_plan_dry_run_makes_no_writes():
    plan = build_plan([_page("runbook-a")], existing_hashes={"runbook-gone": "h"})
    backend = StubBackend()
    result = apply_plan(plan, backend, commit=False)
    assert backend.upserts == []
    assert backend.deletes == []
    # result still summarises what WOULD happen
    assert result.upserted == 1
    assert result.deleted == 1
    assert result.committed is False


def test_apply_plan_commit_writes_upserts_and_deletes():
    p = _page("runbook-a")
    plan = build_plan([p], existing_hashes={"runbook-gone": "h"})
    backend = StubBackend()
    result = apply_plan(plan, backend, commit=True)
    assert [u[0] for u in backend.upserts] == ["runbook-a"]
    assert backend.deletes == ["runbook-gone"]
    assert result.committed is True
    assert result.upserted == 1
    assert result.deleted == 1


def test_apply_plan_uses_backend_existing_hashes_helper():
    # project() convenience: read existing from the backend, diff, (optionally) apply
    from skos.brain.ops.writer import project

    p = _page("runbook-a")
    backend = StubBackend(existing={"runbook-a": p.content_hash})
    result = project([p], backend, embedder=StubEmbedder(), commit=True)
    # unchanged -> no writes
    assert backend.upserts == []
    assert result.unchanged == 1
    assert backend.observed == ["runbook-a"]


def test_project_embeds_then_writes_changed():
    backend = StubBackend(existing={})
    result = project_page_list(backend)
    assert backend.upserts
    assert result.upserted >= 1


def project_page_list(backend):
    from skos.brain.ops.writer import project

    return project([_page("runbook-a")], backend, embedder=StubEmbedder(), commit=True)
