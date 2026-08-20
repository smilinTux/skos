"""skos.brain.ops — the skbrain OPERATIONS namespace projection (SB1.x).

The ops instance of the brain architecture: canon lives in the ``skbrain-ops``
git repo (Karpathy layout, ``[[wikilinks]]``); this package projects it into the
dedicated ``ops`` schema in skmem-pg (ops.wiki_nodes / ops.wiki_chunks / ops.links,
+ the ops_brain AGE graph) for hybrid RAG.

SB1.1 (this deliverable) is the PURE foundation, IO isolated behind interfaces:
  - parser  : ops-page markdown -> OpsPage (slug, kind, edges, wikilinks)   [pure]
  - chunker : OpsPage body -> OpsChunk list (512-token mxbai discipline)     [pure]
  - embed   : Embedder protocol + mxbai HTTP client                         [IO, stubbable]
  - writer  : content-hash-diffed UpsertPlan + WriterBackend protocol       [plan pure, IO stubbable]

Live sync, retrieval, secret lint and health checks are wired through the
``skbrain`` CLI while keeping database and embedding I/O injectable.
"""

from skos.brain.ops.chunker import chunk_body, chunk_page
from skos.brain.ops.embed import Embedder, MxbaiEmbedder, embed_chunks
from skos.brain.ops.models import (
    ALL_KINDS,
    CANON_KINDS,
    STATE_KINDS,
    OpsChunk,
    OpsEdge,
    OpsPage,
)
from skos.brain.ops.parser import (
    OpsParseError,
    extract_wikilinks,
    parse_file,
    parse_page,
    slug_from_path,
    walk_pages,
)
from skos.brain.ops.writer import (
    NodeState,
    UpsertPlan,
    WriterBackend,
    apply_plan,
    build_plan,
)

__all__ = [
    # models
    "OpsPage",
    "OpsChunk",
    "OpsEdge",
    "CANON_KINDS",
    "STATE_KINDS",
    "ALL_KINDS",
    # parser
    "parse_page",
    "parse_file",
    "walk_pages",
    "extract_wikilinks",
    "slug_from_path",
    "OpsParseError",
    # chunker
    "chunk_body",
    "chunk_page",
    # embed
    "Embedder",
    "MxbaiEmbedder",
    "embed_chunks",
    # writer
    "build_plan",
    "apply_plan",
    "UpsertPlan",
    "NodeState",
    "WriterBackend",
]
