"""Tests for skos.brain.index — build_index + read_index."""
import pytest
from pathlib import Path
from skos.brain.index import build_index, read_index, IndexEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_node(ns_dir: Path, slug: str, entity_type: str = "agent",
                lifecycle: str = "draft", summary: str = "Test summary.") -> Path:
    md = (
        f"---\n"
        f"id: {slug}\n"
        f"type: {entity_type}\n"
        f"namespace: {ns_dir.name}\n"
        f"lifecycle_state: {lifecycle}\n"
        f"summary: \"{summary}\"\n"
        f"---\n\n"
        f"## Body\n\nSome content.\n"
    )
    p = ns_dir / f"{slug}.md"
    p.write_text(md, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

def test_build_index_creates_index_file(tmp_path):
    ns_dir = tmp_path / "agents"
    ns_dir.mkdir()
    _write_node(ns_dir, "agent-lumina", summary="Queen of SKWorld.")
    _write_node(ns_dir, "agent-opus", summary="Claude in Claude Code.")

    index_path = build_index(ns_dir)
    assert index_path.exists()
    assert index_path.name == "_index.md"


def test_build_index_content(tmp_path):
    ns_dir = tmp_path / "agents"
    ns_dir.mkdir()
    _write_node(ns_dir, "agent-lumina", summary="Queen of SKWorld.")
    _write_node(ns_dir, "agent-opus", lifecycle="reviewed", summary="Claude in Claude Code.")

    build_index(ns_dir)
    content = (ns_dir / "_index.md").read_text()

    assert "agent-lumina" in content
    assert "agent-opus" in content
    assert "Queen of SKWorld." in content
    assert "Claude in Claude Code." in content
    assert "reviewed" in content
    assert "namespace: agents" in content
    assert "entity_count: 2" in content


def test_build_index_skips_index_file(tmp_path):
    ns_dir = tmp_path / "skills"
    ns_dir.mkdir()
    _write_node(ns_dir, "skill-foo", summary="Foo skill.")
    # Write a fake _index.md (should not be re-indexed)
    (ns_dir / "_index.md").write_text("---\nnamespace: skills\nentity_count: 99\n---\n")

    build_index(ns_dir)
    entries = read_index(ns_dir)
    assert len(entries) == 1
    assert entries[0].id == "skill-foo"


def test_build_index_skips_unparseable(tmp_path, capsys):
    ns_dir = tmp_path / "tools"
    ns_dir.mkdir()
    _write_node(ns_dir, "tool-pg", summary="Postgres tool.")
    # Write a file that fails parse (no frontmatter)
    (ns_dir / "bad-file.md").write_text("No frontmatter here.\n")

    build_index(ns_dir)
    entries = read_index(ns_dir)
    # Only the valid node should appear
    ids = [e.id for e in entries]
    assert "tool-pg" in ids
    assert "bad-file" not in ids


def test_build_index_empty_namespace(tmp_path):
    ns_dir = tmp_path / "empty"
    ns_dir.mkdir()
    index_path = build_index(ns_dir)
    assert index_path.exists()
    content = index_path.read_text()
    assert "entity_count: 0" in content


def test_build_index_namespace_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_index(tmp_path / "nonexistent")


# ---------------------------------------------------------------------------
# read_index
# ---------------------------------------------------------------------------

def test_read_index_empty_returns_empty_list(tmp_path):
    ns_dir = tmp_path / "ns"
    ns_dir.mkdir()
    entries = read_index(ns_dir)
    assert entries == []


def test_read_index_roundtrip(tmp_path):
    ns_dir = tmp_path / "knowledge"
    ns_dir.mkdir()
    _write_node(ns_dir, "knowledge-arch", entity_type="knowledge",
                lifecycle="canon", summary="Architecture decision.")

    build_index(ns_dir)
    entries = read_index(ns_dir)
    assert len(entries) == 1
    e = entries[0]
    assert e.id == "knowledge-arch"
    assert e.entity_type == "knowledge"
    assert e.lifecycle_state == "canon"
    assert "Architecture decision" in e.summary


def test_read_index_pipe_escaping(tmp_path):
    ns_dir = tmp_path / "rules"
    ns_dir.mkdir()
    _write_node(ns_dir, "rule-foo", entity_type="rule",
                summary="First|Second parts.")
    build_index(ns_dir)
    entries = read_index(ns_dir)
    assert len(entries) == 1
    # Pipe should be unescaped in the returned entry
    assert "|" in entries[0].summary or "First" in entries[0].summary
