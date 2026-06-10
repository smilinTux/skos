"""Tests for skos.brain.canon — write_node / read_node / promote / wiki_path."""
import pytest
from pathlib import Path
from skos.brain.entity import EntityNode, EntityType, LifecycleState, Edge, EdgeType
from skos.brain.canon import (
    CanonError,
    wiki_path,
    write_node,
    read_node,
    promote,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def lumina(tmp_path) -> tuple[EntityNode, Path]:
    """Returns (EntityNode, wiki_root) for a test agent node."""
    node = EntityNode(
        id="agent-lumina",
        type=EntityType.agent,
        namespace="agents",
        lifecycle_state=LifecycleState.draft,
        summary="Queen of SKWorld, DevOps engineer.",
    )
    return node, tmp_path


# ---------------------------------------------------------------------------
# wiki_path
# ---------------------------------------------------------------------------

def test_wiki_path(tmp_path):
    node = EntityNode(id="tool-pg", type=EntityType.tool, namespace="tools")
    p = wiki_path(node, wiki_root=tmp_path)
    assert p == tmp_path / "pages" / "entities" / "tools" / "tool-pg.md"


# ---------------------------------------------------------------------------
# write_node
# ---------------------------------------------------------------------------

def test_write_node_creates_file(lumina):
    node, root = lumina
    path = write_node(node, wiki_root=root)
    assert path.exists()
    content = path.read_text()
    assert "agent-lumina" in content
    assert "Queen of SKWorld" in content


def test_write_node_creates_parent_dirs(tmp_path):
    node = EntityNode(id="skill-deep-research", type=EntityType.skill, namespace="skills")
    path = write_node(node, wiki_root=tmp_path)
    assert path.parent.is_dir()


def test_write_node_overwrites(lumina):
    node, root = lumina
    write_node(node, wiki_root=root)
    node2 = node.model_copy(update={"summary": "Updated summary.", "lifecycle_state": LifecycleState.reviewed})
    write_node(node2, wiki_root=root)
    content = wiki_path(node, wiki_root=root).read_text()
    assert "Updated summary." in content
    assert "reviewed" in content


def test_write_node_no_commit_is_safe(lumina):
    """write_node with commit=False should not call git (no git repo in tmp_path)."""
    node, root = lumina
    # Should not raise even though tmp_path is not a git repo
    path = write_node(node, wiki_root=root, commit=False)
    assert path.exists()


# ---------------------------------------------------------------------------
# read_node
# ---------------------------------------------------------------------------

def test_read_node_roundtrip(lumina):
    node, root = lumina
    write_node(node, wiki_root=root)
    p = wiki_path(node, wiki_root=root)
    node2 = read_node(p)
    assert node2.id == node.id
    assert node2.type == node.type
    assert node2.namespace == node.namespace
    assert node2.lifecycle_state == node.lifecycle_state
    assert node2.summary == node.summary


def test_read_node_with_edges(tmp_path):
    node = EntityNode(
        id="workflow-ingest",
        type=EntityType.workflow,
        namespace="workflows",
        summary="Ingestion workflow.",
        edges=[Edge(target="tool-skmemory-cli", type=EdgeType.depends_on, weight=0.9)],
    )
    write_node(node, wiki_root=tmp_path)
    p = wiki_path(node, wiki_root=tmp_path)
    node2 = read_node(p)
    assert len(node2.edges) == 1
    assert node2.edges[0].target == "tool-skmemory-cli"
    assert node2.edges[0].type == "depends_on"


def test_read_node_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_node(tmp_path / "nonexistent.md")


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def test_promote_draft_to_reviewed(lumina):
    node, root = lumina
    write_node(node, wiki_root=root)
    p = wiki_path(node, wiki_root=root)
    promote(p, "reviewed")
    content = p.read_text()
    assert "lifecycle_state: reviewed" in content


def test_promote_reviewed_to_canon(lumina):
    node, root = lumina
    node2 = node.model_copy(update={"lifecycle_state": LifecycleState.reviewed})
    write_node(node2, wiki_root=root)
    p = wiki_path(node2, wiki_root=root)
    promote(p, "canon")
    content = p.read_text()
    assert "lifecycle_state: canon" in content


def test_promote_draft_to_canon_skipping_reviewed(lumina):
    """Skipping a lifecycle step (draft → canon) is allowed; no backward-only rule."""
    node, root = lumina
    write_node(node, wiki_root=root)
    p = wiki_path(node, wiki_root=root)
    promote(p, "canon")
    content = p.read_text()
    assert "lifecycle_state: canon" in content


def test_promote_backward_raises(lumina):
    node, root = lumina
    node2 = node.model_copy(update={"lifecycle_state": LifecycleState.canon})
    write_node(node2, wiki_root=root)
    p = wiki_path(node2, wiki_root=root)
    with pytest.raises(CanonError, match="backwards"):
        promote(p, "draft")


def test_promote_invalid_state_raises(lumina):
    node, root = lumina
    write_node(node, wiki_root=root)
    p = wiki_path(node, wiki_root=root)
    with pytest.raises(CanonError, match="Invalid lifecycle_state"):
        promote(p, "published")


def test_promote_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        promote(tmp_path / "ghost.md", "reviewed")


def test_promote_no_commit_is_safe(lumina):
    """promote with commit=False should not call git."""
    node, root = lumina
    write_node(node, wiki_root=root)
    p = wiki_path(node, wiki_root=root)
    promote(p, "reviewed", commit=False)
    content = p.read_text()
    assert "lifecycle_state: reviewed" in content
