"""Read-only skbrain retrieval API and ATLAS retriever adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from skos.brain.ops.embed import Embedder, MxbaiEmbedder
from skos.brain.ops.postgres import _connect, dsn_from_env


@dataclass(frozen=True)
class SearchHit:
    """Bounded, attribution-preserving retrieval result."""

    node_id: str
    kind: str
    title: str
    excerpt: str
    score: float


class OpsReader:
    """Fail-safe read surface using the dedicated read-only DSN."""

    def __init__(
        self,
        dsn: str,
        *,
        connect: Callable[[str], Any] = _connect,
        embedder: Embedder | None = None,
    ):
        self._dsn, self._connect, self._embedder = dsn, connect, embedder or MxbaiEmbedder()

    def search(self, query: str, *, limit: int = 8, kind: str | None = None) -> list[SearchHit]:
        """Hybrid-search ops canon with a strict result bound."""
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 25))
        vector = self._embedder.embed([query])[0]
        encoded = "[" + ",".join(map(str, vector)) + "]"
        with self._connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT node_id,kind,title,content,score "
                "FROM ops.hybrid_search_ops(%s,%s::public.vector,%s,%s)",
                (query, encoded, limit, kind),
            )
            return [
                SearchHit(str(r[0]), str(r[1]), str(r[2]), str(r[3]), float(r[4]))
                for r in cur.fetchall()
            ]

    def page(self, node_id: str) -> dict[str, Any] | None:
        """Return one page plus typed outgoing links, or None."""
        with self._connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id,kind,title,lifecycle,body_md,updated_at "
                "FROM ops.wiki_nodes WHERE id=%s",
                (node_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "SELECT dst,edge_type,provenance FROM ops.links "
                "WHERE src=%s ORDER BY dst,edge_type",
                (node_id,),
            )
            return {
                "id": row[0],
                "kind": row[1],
                "title": row[2],
                "lifecycle": row[3],
                "body_md": row[4],
                "updated_at": str(row[5]),
                "links": [list(x) for x in cur.fetchall()],
            }

    def backlinks(self, node_id: str) -> list[dict[str, str]]:
        """Return bounded incoming links."""
        with self._connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT src,edge_type,provenance FROM ops.links "
                "WHERE dst=%s ORDER BY src,edge_type LIMIT 100",
                (node_id,),
            )
            return [
                {"src": str(x[0]), "edge_type": str(x[1]), "provenance": str(x[2])}
                for x in cur.fetchall()
            ]

    def neighborhood(self, node_id: str, *, limit: int = 50) -> list[dict[str, str]]:
        """Return a bounded one-hop neighborhood from the relational graph mirror."""
        limit = max(1, min(int(limit), 100))
        with self._connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT src,dst,edge_type,provenance FROM ops.links
                   WHERE src=%s OR dst=%s ORDER BY src,dst,edge_type LIMIT %s""",
                (node_id, node_id, limit),
            )
            return [
                {
                    "src": str(x[0]),
                    "dst": str(x[1]),
                    "edge_type": str(x[2]),
                    "provenance": str(x[3]),
                }
                for x in cur.fetchall()
            ]


def build_retriever(*, dsn: str | None = None, embedder: Embedder | None = None):
    """Build ATLAS's read-only callable; errors propagate so RAG fails closed."""
    reader = OpsReader(dsn or dsn_from_env("SKBRAIN_PG_READER_DSN"), embedder=embedder)

    def retrieve(query: str, *, limit: int = 8, kind: str | None = None) -> list[dict[str, Any]]:
        return [asdict(hit) for hit in reader.search(query, limit=limit, kind=kind)]

    return retrieve
