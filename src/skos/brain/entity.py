"""skos.brain.entity — EntityNode: the typed-Markdown-node contract.

Every component in the sovereign AI-OS (agent, skill, workflow, rule, tool,
knowledge, project, output, memory, command, namespace) is expressed as an
EntityNode: a YAML frontmatter block carrying identity, lifecycle, edges and
runtime pointers, plus a Markdown body for human/agent reading.

Frontmatter schema:
  id:               unique slug (kebab-case), e.g. "agent-meta-ads-strategist"
  type:             one of EntityType values
  namespace:        grouping namespace (kebab-case), e.g. "meta-ads"
  lifecycle_state:  draft | reviewed | canon
  summary:          one-line human+agent summary (used in indexes)
  runtime_adapters: list of adapter names (optional)
  tools:            list of tool-entity ids this node depends on (optional)
  edges:            list of {target, type, weight} edge records (optional)
  state_stored_at:  URI or path for operational state (optional, not in git)

Edge types: depends_on | cites | defines | requires | relates_to
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Union

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    agent = "agent"
    skill = "skill"
    workflow = "workflow"
    rule = "rule"
    tool = "tool"
    knowledge = "knowledge"
    project = "project"
    output = "output"
    memory = "memory"
    command = "command"
    namespace = "namespace"


class EdgeType(str, Enum):
    depends_on = "depends_on"
    cites = "cites"
    defines = "defines"
    requires = "requires"
    relates_to = "relates_to"


class LifecycleState(str, Enum):
    draft = "draft"
    reviewed = "reviewed"
    canon = "canon"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class Edge(BaseModel):
    """A directed edge to another entity node."""
    target: str = Field(..., description="Target entity id or slug")
    type: EdgeType = Field(default=EdgeType.relates_to)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)

    model_config = {"use_enum_values": True}


class EntityNode(BaseModel):
    """The canonical typed-Markdown-node contract.

    Parse from markdown with ``parse(md_text)``; render back with ``render(node)``.
    The ``body`` field holds everything after the closing ``---`` delimiter.
    """
    id: str = Field(..., description="Unique entity slug, kebab-case")
    type: EntityType = Field(..., description="Ontology entity type")
    namespace: str = Field(..., description="Grouping namespace, kebab-case")
    lifecycle_state: LifecycleState = Field(default=LifecycleState.draft)
    summary: str = Field(default="", description="One-line summary for indexes")
    runtime_adapters: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    state_stored_at: str = Field(default="", description="Operational state URI/path")
    body: str = Field(default="", description="Markdown body (everything after frontmatter)")

    model_config = {"use_enum_values": True}

    @field_validator("id")
    @classmethod
    def id_must_be_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", v):
            raise ValueError(
                f"id must be kebab-case (a-z, 0-9, hyphens, no leading/trailing hyphen): {v!r}"
            )
        return v

    @field_validator("namespace")
    @classmethod
    def namespace_must_be_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$|^[a-z0-9]$", v):
            raise ValueError(
                f"namespace must be kebab-case: {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# ParseError
# ---------------------------------------------------------------------------

class ParseError(ValueError):
    """Raised when markdown cannot be parsed as a valid EntityNode."""


# ---------------------------------------------------------------------------
# parse() — frontmatter + body → EntityNode
# ---------------------------------------------------------------------------

# Match the YAML frontmatter block: --- ... ---
_FM_RE = re.compile(r"^\s*---\n(.*?)\n---\n?(.*)", re.DOTALL)


def parse(md_text: str) -> EntityNode:
    """Parse a Markdown string (frontmatter + body) into an EntityNode.

    Raises:
        ParseError: if the frontmatter is missing, malformed, or fails schema validation.
    """
    m = _FM_RE.match(md_text)
    if not m:
        raise ParseError(
            "No YAML frontmatter block found. "
            "Document must start with --- ... --- frontmatter."
        )
    fm_text, body = m.group(1), m.group(2)

    try:
        fm: dict[str, Any] = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ParseError(f"YAML parse error in frontmatter: {exc}") from exc

    if not isinstance(fm, dict):
        raise ParseError(f"Frontmatter must be a YAML mapping, got {type(fm).__name__}")

    # Normalise edges: accept both raw dicts and Edge objects
    raw_edges = fm.get("edges") or []
    edges: list[dict] = []
    for e in raw_edges:
        if isinstance(e, dict):
            edges.append(e)
        elif isinstance(e, str):
            # shorthand: "target:type" or just "target"
            parts = e.split(":", 1)
            edges.append({"target": parts[0], "type": parts[1] if len(parts) > 1 else "relates_to"})
        else:
            raise ParseError(f"Unexpected edge format: {e!r}")
    fm["edges"] = edges
    fm["body"] = body.lstrip("\n")

    from pydantic import ValidationError
    try:
        return EntityNode.model_validate(fm)
    except ValidationError as exc:
        raise ParseError(f"EntityNode validation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# render() — EntityNode → Markdown
# ---------------------------------------------------------------------------

def _str(v: Any) -> str:
    """Coerce any value (including str-Enum) to a plain str."""
    return v.value if isinstance(v, Enum) else str(v)


def render(node: EntityNode) -> str:
    """Render an EntityNode back to canonical Markdown with YAML frontmatter.

    The output is round-trip stable: parse(render(node)) == node (modulo body whitespace).
    """
    # Explicitly coerce enum instances to plain strings so yaml.dump never emits
    # Python-object tags (pydantic 2.x use_enum_values is unreliable for str-Enum defaults).
    fm: dict[str, Any] = {
        "id": node.id,
        "type": _str(node.type),
        "namespace": node.namespace,
        "lifecycle_state": _str(node.lifecycle_state),
        "summary": node.summary,
    }
    if node.runtime_adapters:
        fm["runtime_adapters"] = list(node.runtime_adapters)
    if node.tools:
        fm["tools"] = list(node.tools)
    if node.edges:
        fm["edges"] = [
            {
                "target": e.target,
                "type": _str(e.type),
                "weight": e.weight,
            }
            for e in node.edges
        ]
    if node.state_stored_at:
        fm["state_stored_at"] = node.state_stored_at

    fm_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
    body = node.body if node.body.startswith("\n") else ("\n" + node.body if node.body else "")
    return f"---\n{fm_yaml}---\n{body}"
