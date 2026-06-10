"""Tests for skos.brain.entity — parse/render roundtrip + validation."""
import pytest
from skos.brain.entity import (
    EntityNode,
    EntityType,
    EdgeType,
    Edge,
    LifecycleState,
    ParseError,
    parse,
    render,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_MD = """\
---
id: agent-lumina
type: agent
namespace: agents
---
"""

FULL_MD = """\
---
id: skill-wiki-ingest
type: skill
namespace: skills
lifecycle_state: reviewed
summary: Ingests raw sources into the wiki entity graph.
runtime_adapters:
- claude-code
- codex
tools:
- tool-skmemory-cli
edges:
- target: tool-skmemory-cli
  type: depends_on
  weight: 0.9
- target: knowledge-brain-ontology
  type: cites
  weight: 0.7
state_stored_at: postgres://skmem-pg/docs
---

## Overview

This skill handles raw source ingestion.
"""


# ---------------------------------------------------------------------------
# parse() — happy path
# ---------------------------------------------------------------------------

def test_parse_minimal():
    node = parse(MINIMAL_MD)
    assert node.id == "agent-lumina"
    assert node.type == "agent"
    assert node.namespace == "agents"
    assert node.lifecycle_state == "draft"
    assert node.summary == ""
    assert node.edges == []


def test_parse_full():
    node = parse(FULL_MD)
    assert node.id == "skill-wiki-ingest"
    assert node.type == "skill"
    assert node.namespace == "skills"
    assert node.lifecycle_state == "reviewed"
    assert "Ingests raw sources" in node.summary
    assert node.runtime_adapters == ["claude-code", "codex"]
    assert node.tools == ["tool-skmemory-cli"]
    assert len(node.edges) == 2
    assert node.edges[0].target == "tool-skmemory-cli"
    assert node.edges[0].type == "depends_on"
    assert abs(node.edges[0].weight - 0.9) < 1e-6
    assert node.state_stored_at == "postgres://skmem-pg/docs"
    assert "Overview" in node.body


def test_parse_body_preserved():
    md = MINIMAL_MD.rstrip() + "\n\n## Body\n\nHello world.\n"
    node = parse(md)
    assert "Hello world" in node.body


# ---------------------------------------------------------------------------
# parse() — error cases
# ---------------------------------------------------------------------------

def test_parse_no_frontmatter():
    with pytest.raises(ParseError, match="frontmatter"):
        parse("# Just a heading\n\nNo frontmatter here.\n")


def test_parse_missing_required_fields():
    with pytest.raises(ParseError):
        parse("---\nid: foo\n---\n")  # missing type and namespace


def test_parse_invalid_id_slug():
    with pytest.raises(ParseError, match="kebab-case"):
        parse("---\nid: InvalidSlug\ntype: agent\nnamespace: agents\n---\n")


def test_parse_invalid_type():
    with pytest.raises(ParseError):
        parse("---\nid: foo-bar\ntype: notavalidtype\nnamespace: ns\n---\n")


def test_parse_invalid_lifecycle():
    with pytest.raises(ParseError):
        parse("---\nid: foo-bar\ntype: agent\nnamespace: ns\nlifecycle_state: published\n---\n")


def test_parse_bad_yaml():
    with pytest.raises(ParseError, match="YAML"):
        parse("---\n: bad: yaml: [unclosed\n---\n")


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------

def test_render_minimal():
    node = EntityNode(id="tool-pg", type=EntityType.tool, namespace="tools")
    md = render(node)
    assert "---" in md
    assert "id: tool-pg" in md
    assert "type: tool" in md
    assert "namespace: tools" in md
    assert "lifecycle_state: draft" in md


def test_render_with_edges():
    node = EntityNode(
        id="agent-lumina",
        type=EntityType.agent,
        namespace="agents",
        lifecycle_state=LifecycleState.canon,
        summary="Queen of SKWorld.",
        edges=[Edge(target="tool-skmemory-cli", type=EdgeType.depends_on, weight=0.95)],
    )
    md = render(node)
    assert "depends_on" in md
    assert "tool-skmemory-cli" in md
    assert "lifecycle_state: canon" in md


# ---------------------------------------------------------------------------
# Roundtrip: parse → render → parse
# ---------------------------------------------------------------------------

def test_roundtrip_minimal():
    node = parse(MINIMAL_MD)
    rendered = render(node)
    node2 = parse(rendered)
    assert node2.id == node.id
    assert node2.type == node.type
    assert node2.namespace == node.namespace
    assert node2.lifecycle_state == node.lifecycle_state


def test_roundtrip_full():
    node = parse(FULL_MD)
    rendered = render(node)
    node2 = parse(rendered)
    assert node2.id == node.id
    assert node2.type == node.type
    assert node2.lifecycle_state == node.lifecycle_state
    assert node2.summary == node.summary
    assert node2.runtime_adapters == node.runtime_adapters
    assert node2.tools == node.tools
    assert len(node2.edges) == len(node.edges)
    assert node2.edges[0].target == node.edges[0].target
    assert node2.edges[0].type == node.edges[0].type
    assert abs(node2.edges[0].weight - node.edges[0].weight) < 1e-6
    assert node2.state_stored_at == node.state_stored_at


# ---------------------------------------------------------------------------
# EntityNode direct construction
# ---------------------------------------------------------------------------

def test_entity_node_direct():
    node = EntityNode(
        id="knowledge-brain-ontology",
        type=EntityType.knowledge,
        namespace="knowledge",
        lifecycle_state=LifecycleState.reviewed,
        summary="The Infinite Brain ontology spec.",
    )
    assert node.type == EntityType.knowledge.value
    assert node.lifecycle_state == LifecycleState.reviewed.value


def test_entity_node_all_types():
    for et in EntityType:
        node = EntityNode(id=f"{et.value}-test", type=et, namespace="test")
        assert node.type == et.value


def test_entity_node_all_edge_types():
    for et in EdgeType:
        edge = Edge(target="some-node", type=et)
        assert edge.type == et.value


def test_edge_weight_bounds():
    with pytest.raises(Exception):
        Edge(target="x", weight=-0.1)
    with pytest.raises(Exception):
        Edge(target="x", weight=1.1)
