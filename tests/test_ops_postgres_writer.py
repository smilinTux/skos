"""The writer's INSERT must name only columns the ops DDL actually has.

Projector tests use a fake backend, so a column the real table lacks went
unnoticed from 2026-08-20 until the first new canon page reached Postgres.
"""

from __future__ import annotations

import json
import re

from skos.brain.ops.models import OpsEdge, OpsPage
from skos.brain.ops.postgres import PostgresWriterBackend

# ops.wiki_nodes in skmemory/deploy/skmem-pg/03-ops-namespace.sql.
WIKI_NODES_COLUMNS = {
    "id",
    "kind",
    "origin",
    "title",
    "lifecycle",
    "frontmatter",
    "body_md",
    "content_hash",
    "created_at",
    "updated_at",
}


class _Cursor:
    def __init__(self, statements: list[tuple[str, tuple]]):
        self._statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params=()):
        self._statements.append((sql, params))


class _Conn:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def cursor(self):
        return _Cursor(self.statements)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_upsert_names_only_real_columns_and_binds_every_placeholder():
    conn = _Conn()
    backend = PostgresWriterBackend("dsn", connect=lambda _dsn: conn)
    page = OpsPage(
        slug="ke-x",
        kind="known-error",
        title="X",
        origin="git",
        lifecycle="canon",
        frontmatter={},
        body_md="body",
    )

    backend.upsert_node(page, [], [])

    sql, params = next(s for s in conn.statements if "INSERT INTO ops.wiki_nodes" in s[0])
    columns = re.search(r"\((.*?)\)\s*VALUES", sql, re.S).group(1)
    named = {name.strip() for name in columns.split(",")}
    assert named <= WIKI_NODES_COLUMNS
    assert len(params) == sql.count("%s") == len(named)
    assert "namespace" not in sql


def test_age_operators_are_on_the_search_path():
    conn = _Conn()
    PostgresWriterBackend("dsn", connect=lambda _dsn: conn)
    assert "ag_catalog" in conn.statements[0][0]


def _cypher_statements(conn: _Conn) -> list[tuple[str, tuple]]:
    return [s for s in conn.statements if "cypher(" in s[0]]


def _page(title: str = "X") -> OpsPage:
    return OpsPage(
        slug="ke-x",
        kind="known-error",
        title=title,
        origin="git",
        lifecycle="canon",
        frontmatter={},
        body_md="body",
    )


def test_cypher_text_is_dollar_quoted_because_age_refuses_a_bound_query():
    conn = _Conn()
    backend = PostgresWriterBackend("dsn", connect=lambda _dsn: conn)

    backend.upsert_node(
        _page(), [], [OpsEdge(src="ke-x", dst="svc-y", edge_type="touches", provenance="canon")]
    )
    backend.delete_node("ke-x")

    statements = _cypher_statements(conn)
    assert len(statements) == 4
    for sql, params in statements:
        assert "cypher('ops_brain', $$" in sql
        assert sql.count("%s") == len(params) == 1


def test_page_values_travel_as_parameters_never_in_the_query_text():
    conn = _Conn()
    backend = PostgresWriterBackend("dsn", connect=lambda _dsn: conn)
    hostile = 'ends the quote $$ }) DETACH DELETE n // "'

    backend.upsert_node(_page(hostile), [], [])

    sql, params = _cypher_statements(conn)[0]
    assert hostile not in sql
    assert json.loads(params[0])["title"] == hostile
