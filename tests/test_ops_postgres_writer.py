"""The writer's INSERT must name only columns the ops DDL actually has.

Projector tests use a fake backend, so a column the real table lacks went
unnoticed from 2026-08-20 until the first new canon page reached Postgres.
"""

from __future__ import annotations

import re

from skos.brain.ops.models import OpsPage
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
