"""Tests for the obsidian surface — a markdown-vault read/write adapter."""
import pytest

from skos.brain.entity import EntityNode, EntityType, parse, render
from skos.interface.base import SurfaceError, SurfaceCapabilities
from skos.interface.obsidian import ObsidianSurface
from skos.interface.conformance import assert_surface_conforms


def _node(node_id="agent-lumina", namespace="agents"):
    return EntityNode(
        id=node_id,
        type=EntityType.agent,
        namespace=namespace,
        summary="Queen of SKWorld.",
        body="## Overview\n\nLumina is the primary agent.\n",
    )


@pytest.fixture
def vault(tmp_path):
    return ObsidianSurface(vault_root=tmp_path / "vault")


def test_name_and_capability(vault):
    assert vault.name == "obsidian"
    assert vault.capability == "surface"


def test_write_then_read_round_trip(vault):
    node = _node()
    vault.write(node)
    got = vault.read("agent-lumina")
    assert got.id == node.id
    assert got.type == node.type
    assert got.namespace == node.namespace
    assert got.summary == node.summary
    assert "Lumina is the primary agent." in got.body


def test_write_creates_markdown_file(vault, tmp_path):
    vault.write(_node())
    f = tmp_path / "vault" / "agents" / "agent-lumina.md"
    assert f.exists()
    # The file is a valid EntityNode round-trip
    reparsed = parse(f.read_text(encoding="utf-8"))
    assert reparsed.id == "agent-lumina"


def test_read_missing_node_raises(vault):
    with pytest.raises(SurfaceError):
        vault.read("does-not-exist")


def test_list_empty_vault(vault):
    assert vault.list() == []


def test_list_returns_node_ids(vault):
    vault.write(_node("agent-lumina", "agents"))
    vault.write(_node("agent-jarvis", "agents"))
    vault.write(_node("skill-render", "skills"))
    assert vault.list() == ["agent-jarvis", "agent-lumina", "skill-render"]


def test_list_ignores_index_files(vault, tmp_path):
    vault.write(_node())
    # Drop an obsidian/brain index file that is NOT an entity node
    ns = tmp_path / "vault" / "agents"
    (ns / "_index.md").write_text("# not an entity\n", encoding="utf-8")
    assert vault.list() == ["agent-lumina"]


def test_overwrite_updates_node(vault):
    vault.write(_node())
    updated = _node()
    updated.summary = "Updated summary."
    vault.write(updated)
    assert vault.read("agent-lumina").summary == "Updated summary."


def test_capabilities(vault):
    caps = vault.capabilities()
    assert isinstance(caps, SurfaceCapabilities)
    assert caps.name == "obsidian"
    assert caps.readable and caps.writable and caps.listable
    assert caps.planned is False


def test_conformance(vault):
    assert_surface_conforms(vault)
