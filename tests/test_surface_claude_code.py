"""Tests for the claude-code surface — the agent-facing brain surface (canon-backed)."""
import pytest

from skos.brain.entity import EntityNode, EntityType
from skos.interface.base import SurfaceError, SurfaceCapabilities
from skos.interface.claude_code import ClaudeCodeSurface
from skos.interface.conformance import assert_surface_conforms


def _node(node_id="agent-lumina", namespace="agents"):
    return EntityNode(
        id=node_id,
        type=EntityType.agent,
        namespace=namespace,
        summary="Queen of SKWorld.",
        body="## Overview\n\nLumina.\n",
    )


@pytest.fixture
def surface(tmp_path):
    return ClaudeCodeSurface(wiki_root=tmp_path / "wiki")


def test_name_and_capability(surface):
    assert surface.name == "claude-code"
    assert surface.capability == "surface"


def test_write_uses_canon_layout(surface, tmp_path):
    surface.write(_node())
    # canon path: <wiki>/pages/entities/<namespace>/<id>.md
    f = tmp_path / "wiki" / "pages" / "entities" / "agents" / "agent-lumina.md"
    assert f.exists()


def test_write_then_read_round_trip(surface):
    node = _node()
    surface.write(node)
    got = surface.read("agent-lumina")
    assert got.id == node.id
    assert got.summary == node.summary
    assert "Lumina." in got.body


def test_read_missing_raises(surface):
    with pytest.raises(SurfaceError):
        surface.read("ghost-node")


def test_list_across_namespaces(surface):
    surface.write(_node("agent-lumina", "agents"))
    surface.write(_node("skill-render", "skills"))
    assert surface.list() == ["agent-lumina", "skill-render"]


def test_capabilities(surface):
    caps = surface.capabilities()
    assert isinstance(caps, SurfaceCapabilities)
    assert caps.name == "claude-code"
    assert caps.planned is False


def test_conformance(surface):
    assert_surface_conforms(surface)
