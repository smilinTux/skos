"""Transactional skmem-pg writer for the skbrain ops projection."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from skos.brain.ops.models import OpsChunk, OpsEdge, OpsPage


class BackendError(RuntimeError):
    """Raised when the live backend is unavailable or violates its contract."""


def _connect(dsn: str):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - installation dependent
        raise BackendError("psycopg is required for live skbrain database access") from exc
    return psycopg.connect(dsn)


def dsn_from_env(name: str) -> str:
    """Read a DSN from one explicit environment variable without logging it."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise BackendError(f"{name} is not configured")
    return value


class PostgresWriterBackend:
    """Atomic relational projection backend; one transaction per sync run."""

    def __init__(self, dsn: str, *, connect: Callable[[str], Any] = _connect):
        self._conn = connect(dsn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()

    def existing_hashes(self) -> dict[str, str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id, content_hash FROM ops.wiki_nodes")
            return {str(row[0]): str(row[1]) for row in cur.fetchall()}

    def upsert_node(self, node: OpsPage, chunks: list[OpsChunk], links: list[OpsEdge]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ops.wiki_nodes
                   (id, kind, title, namespace, origin, lifecycle, frontmatter, body_md, content_hash)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET kind=EXCLUDED.kind,title=EXCLUDED.title,
                   namespace=EXCLUDED.namespace,origin=EXCLUDED.origin,
                   lifecycle=EXCLUDED.lifecycle,frontmatter=EXCLUDED.frontmatter,
                   body_md=EXCLUDED.body_md,content_hash=EXCLUDED.content_hash,updated_at=now()""",
                (node.slug, node.kind, node.title, node.namespace, node.origin,
                 node.lifecycle, json.dumps(node.frontmatter, default=str), node.body_md,
                 node.content_hash),
            )
            cur.execute("DELETE FROM ops.wiki_chunks WHERE node_id=%s", (node.slug,))
            for chunk in chunks:
                vector = None if chunk.embedding is None else "[" + ",".join(map(str, chunk.embedding)) + "]"
                cur.execute(
                    "INSERT INTO ops.wiki_chunks(node_id,ord,content,embedding) VALUES (%s,%s,%s,%s::public.vector)",
                    (node.slug, chunk.ord, chunk.content, vector),
                )
            cur.execute("DELETE FROM ops.links WHERE src=%s", (node.slug,))
            for edge in links:
                cur.execute(
                    "INSERT INTO ops.links(src,dst,edge_type,provenance) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (edge.src, edge.dst, edge.edge_type, edge.provenance),
                )
            self._upsert_graph(cur, node, links)

    def delete_node(self, node_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM ops.links WHERE src=%s OR dst=%s", (node_id, node_id))
            cur.execute("DELETE FROM ops.wiki_nodes WHERE id=%s", (node_id,))
            graph_id = json.dumps(node_id)
            cur.execute(
                "SELECT * FROM ag_catalog.cypher('ops_brain', %s) AS (v ag_catalog.agtype)",
                (f"MATCH (n:OpsEntity {{id: {graph_id}}}) DETACH DELETE n RETURN 1",),
            )

    @staticmethod
    def _upsert_graph(cur: Any, node: OpsPage, links: list[OpsEdge]) -> None:
        """Mirror a node and its outgoing edges into AGE inside the transaction."""
        node_id, title, kind = map(json.dumps, (node.slug, node.title, node.kind))
        query = (
            f"MERGE (n:OpsEntity {{id: {node_id}}}) "
            f"SET n.title = {title}, n.kind = {kind}, n.content_hash = {json.dumps(node.content_hash)} "
            "RETURN n"
        )
        cur.execute(
            "SELECT * FROM ag_catalog.cypher('ops_brain', %s) AS (v ag_catalog.agtype)",
            (query,),
        )
        cur.execute(
            "SELECT * FROM ag_catalog.cypher('ops_brain', %s) AS (v ag_catalog.agtype)",
            (f"MATCH (n:OpsEntity {{id: {node_id}}})-[r]->() DELETE r RETURN n",),
        )
        for edge in links:
            edge_label = re.sub(r"[^A-Za-z0-9_]", "_", edge.edge_type).upper()
            if not edge_label or not edge_label[0].isalpha():
                edge_label = "LINKS_TO"
            dst = json.dumps(edge.dst)
            provenance = json.dumps(edge.provenance)
            query = (
                f"MATCH (s:OpsEntity {{id: {node_id}}}) "
                f"MERGE (d:OpsEntity {{id: {dst}}}) "
                f"MERGE (s)-[r:{edge_label} {{provenance: {provenance}}}]->(d) RETURN r"
            )
            cur.execute(
                "SELECT * FROM ag_catalog.cypher('ops_brain', %s) AS (v ag_catalog.agtype)",
                (query,),
            )
