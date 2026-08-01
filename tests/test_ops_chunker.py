"""Tests for skos.brain.ops.chunker — heading-aware chunking (SB1.1, pure).

Reuses chunk.py's discipline (mxbai 512-token ceiling ~ 1200 chars) but splits
on markdown headings first so runbook sections (Steps / Verify / Rollback) stay
retrievable as coherent units.
"""

from skos.brain.ops.chunker import DEFAULT_MAX_CHARS, chunk_body, chunk_page
from skos.brain.ops.models import OpsPage


def test_empty_body_yields_no_chunks():
    assert chunk_body("") == []
    assert chunk_body("   \n\n  ") == []


def test_short_body_is_one_chunk():
    chunks = chunk_body("A single short paragraph of ops text.")
    assert len(chunks) == 1
    assert "single short paragraph" in chunks[0]


def test_splits_on_headings():
    body = (
        "## Preconditions\n\nfleet not frozen.\n\n"
        "## Steps\n\nrestart the unit.\n\n"
        "## Verify\n\ncheck is-active.\n"
    )
    chunks = chunk_body(body)
    # three headed sections, each small -> at least one chunk carrying each heading
    joined = "\n".join(chunks)
    assert "Preconditions" in joined
    assert "Steps" in joined
    assert "Verify" in joined
    # a heading's body travels WITH its heading (not orphaned into the prior chunk)
    steps_chunk = next(c for c in chunks if "## Steps" in c)
    assert "restart the unit" in steps_chunk


def test_oversized_section_is_split_under_ceiling():
    big = "word " * 1000  # ~5000 chars, well over the ceiling
    body = f"## Big\n\n{big}\n"
    chunks = chunk_body(body)
    assert len(chunks) >= 2
    assert all(len(c) <= DEFAULT_MAX_CHARS for c in chunks)


def test_all_chunks_respect_ceiling_on_real_runbook_shape():
    body = "\n\n".join(
        f"## Section {i}\n\n" + ("detail sentence. " * 40) for i in range(6)
    )
    chunks = chunk_body(body)
    assert chunks
    assert all(len(c) <= DEFAULT_MAX_CHARS for c in chunks)


def test_custom_max_chars():
    body = "para one is here.\n\npara two is here.\n\npara three is here."
    chunks = chunk_body(body, max_chars=25)
    assert len(chunks) >= 2
    assert all(len(c) <= 25 or " " not in c for c in chunks)


def test_chunk_page_builds_ordered_opschunks():
    page = OpsPage(
        slug="runbook-x",
        kind="runbook",
        title="X",
        body_md="## A\n\nalpha text.\n\n## B\n\nbravo text.\n",
    )
    chunks = chunk_page(page)
    assert all(c.node_id == "runbook-x" for c in chunks)
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    assert all(c.embedding is None for c in chunks)


def test_chunk_page_empty_body_no_chunks():
    page = OpsPage(slug="node-x", kind="node", title="X", body_md="")
    assert chunk_page(page) == []
