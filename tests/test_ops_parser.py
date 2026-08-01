"""Tests for skos.brain.ops.parser — the ops-wiki markdown parser (SB1.1).

Table-driven against the real skbrain-ops page shapes (copied into
tests/fixtures/skbrain_ops/). Pure: no I/O beyond reading the fixture files.
"""

from pathlib import Path

import pytest

from skos.brain.ops.parser import (
    OpsParseError,
    extract_wikilinks,
    parse_file,
    parse_page,
    slug_from_path,
    walk_pages,
)

FIXTURES = Path(__file__).parent / "fixtures" / "skbrain_ops"

RUNBOOK = FIXTURES / "runbooks" / "runbook-restart-telegram-bridge.md"
KE = FIXTURES / "known-errors" / "ke-telegram-wedge.md"
SERVICE = FIXTURES / "services" / "service-skcode.md"


# ---------------------------------------------------------------------------
# slug_from_path
# ---------------------------------------------------------------------------


def test_slug_from_path_is_lowercased_stem():
    assert slug_from_path(
        Path("pages/runbooks/Runbook-Restart-Telegram-Bridge.md")
    ) == ("runbook-restart-telegram-bridge")


def test_slug_from_path_str_input():
    assert slug_from_path("nodes/node-DOT41.md") == "node-dot41"


# ---------------------------------------------------------------------------
# extract_wikilinks
# ---------------------------------------------------------------------------


def test_extract_wikilinks_bare():
    links = extract_wikilinks("See [[ke-telegram-wedge]] and [[service-skchat]].")
    assert links == ["ke-telegram-wedge", "service-skchat"]


def test_extract_wikilinks_pipe_display_form():
    # [[slug|display]] resolves to the bare slug (left of the pipe)
    links = extract_wikilinks("chunk of [[node-dot41|raw/nodes/dot41]] here")
    assert links == ["node-dot41"]


def test_extract_wikilinks_dedup_preserves_order():
    links = extract_wikilinks("[[a-b]] then [[c-d]] then [[a-b]] again")
    assert links == ["a-b", "c-d"]


def test_extract_wikilinks_none():
    assert extract_wikilinks("no links here at all") == []


def test_extract_wikilinks_lowercased():
    assert extract_wikilinks("[[Service-SKChat]]") == ["service-skchat"]


# ---------------------------------------------------------------------------
# parse_page — happy path on real fixtures
# ---------------------------------------------------------------------------


def test_parse_runbook_core_fields():
    page = parse_page(
        RUNBOOK.read_text(encoding="utf-8"), slug="runbook-restart-telegram-bridge"
    )
    assert page.slug == "runbook-restart-telegram-bridge"
    assert page.kind == "runbook"
    assert page.namespace == "ops"
    assert page.lifecycle == "canon"
    assert page.origin == "git"
    assert page.title == "Recover a silently wedged telegram bridge"
    assert "Standard operator action" in page.body_md


def test_parse_runbook_frontmatter_edges():
    page = parse_page(
        RUNBOOK.read_text(encoding="utf-8"), slug="runbook-restart-telegram-bridge"
    )
    edge_pairs = {(e.dst, e.edge_type) for e in page.edges}
    assert ("ke-telegram-wedge", "remediates") in edge_pairs
    assert ("service-skchat", "touches") in edge_pairs
    assert ("node-noroc2027-158", "touches") in edge_pairs
    # all frontmatter edges default to definition provenance
    assert all(e.provenance == "definition" for e in page.edges)


def test_parse_runbook_body_wikilinks():
    page = parse_page(
        RUNBOOK.read_text(encoding="utf-8"), slug="runbook-restart-telegram-bridge"
    )
    # body links appear (bare slugs), deduped
    assert "ke-telegram-wedge" in page.wikilinks
    assert "service-skchat" in page.wikilinks


def test_parse_known_error_kind():
    page = parse_page(KE.read_text(encoding="utf-8"), slug="ke-telegram-wedge")
    assert page.kind == "known-error"
    assert page.slug == "ke-telegram-wedge"
    assert page.frontmatter["state_refs"]["kedb"] == "ke-telegram-wedge"


def test_parse_service_kind():
    page = parse_page(SERVICE.read_text(encoding="utf-8"), slug="service-skcode")
    assert page.kind == "service"
    assert page.title.startswith("skcode")


# ---------------------------------------------------------------------------
# id / slug agreement (spec 3.4 — kills the dark-graph bug by construction)
# ---------------------------------------------------------------------------


def test_parse_page_id_must_match_filename_slug():
    text = KE.read_text(encoding="utf-8")
    with pytest.raises(OpsParseError, match="does not match"):
        parse_page(text, slug="wrong-slug")


def test_parse_page_id_missing_falls_back_to_slug():
    text = "---\ntype: runbook\nnamespace: ops\ntitle: T\n---\n\nBody\n"
    page = parse_page(text, slug="runbook-x")
    assert page.slug == "runbook-x"


# ---------------------------------------------------------------------------
# error cases
# ---------------------------------------------------------------------------


def test_parse_page_no_frontmatter():
    with pytest.raises(OpsParseError, match="frontmatter"):
        parse_page("# just a heading\n\nno frontmatter\n", slug="x")


def test_parse_page_missing_type():
    with pytest.raises(OpsParseError, match="type"):
        parse_page("---\nnamespace: ops\ntitle: T\n---\nbody\n", slug="x")


def test_parse_page_bad_yaml():
    with pytest.raises(OpsParseError, match="YAML"):
        parse_page("---\n: bad: [unclosed\n---\nbody\n", slug="x")


def test_parse_page_title_defaults_to_slug():
    page = parse_page("---\ntype: node\nnamespace: ops\n---\nbody\n", slug="node-x")
    assert page.title == "node-x"


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------


def test_content_hash_deterministic():
    text = RUNBOOK.read_text(encoding="utf-8")
    a = parse_page(text, slug="runbook-restart-telegram-bridge")
    b = parse_page(text, slug="runbook-restart-telegram-bridge")
    assert a.content_hash == b.content_hash
    assert len(a.content_hash) == 64  # sha256 hex


def test_content_hash_changes_with_body():
    base = "---\ntype: node\nnamespace: ops\ntitle: T\n---\n\nbody one\n"
    changed = "---\ntype: node\nnamespace: ops\ntitle: T\n---\n\nbody two\n"
    h1 = parse_page(base, slug="node-x").content_hash
    h2 = parse_page(changed, slug="node-x").content_hash
    assert h1 != h2


# ---------------------------------------------------------------------------
# parse_file / walk_pages — thin I/O wrappers, slug from filename
# ---------------------------------------------------------------------------


def test_parse_file_uses_filename_slug():
    page = parse_file(RUNBOOK)
    assert page.slug == "runbook-restart-telegram-bridge"
    assert page.kind == "runbook"


def test_walk_pages_finds_all_fixtures():
    pages = walk_pages(FIXTURES)
    slugs = {p.slug for p in pages}
    assert "runbook-restart-telegram-bridge" in slugs
    assert "ke-telegram-wedge" in slugs
    assert "service-skcode" in slugs
    # every page carries a resolved slug and a kind
    assert all(p.slug and p.kind for p in pages)
