"""skos.brain — Infinite Brain entity-graph ontology.

Every component of the sovereign AI-OS is a typed Markdown node (EntityNode):
frontmatter carries identity, lifecycle, edges, and runtime pointers;
the body stays human-readable and agent-usable.

The three-layer model:
  - git wiki (canon)  — definitions, source of truth
  - skmem-pg          — retrieval projection (embeddings + BM25 + AGE)
  - promotion loop    — draft → reviewed → canon, closing the memory cycle
"""

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
from skos.brain.index import IndexEntry, build_index, read_index
from skos.brain.canon import (
    CanonError,
    write_node,
    read_node,
    promote,
    wiki_path,
)

__all__ = [
    # entity
    "EntityNode",
    "EntityType",
    "EdgeType",
    "Edge",
    "LifecycleState",
    "ParseError",
    "parse",
    "render",
    # index
    "IndexEntry",
    "build_index",
    "read_index",
    # canon
    "CanonError",
    "write_node",
    "read_node",
    "promote",
    "wiki_path",
]
