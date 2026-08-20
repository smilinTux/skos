"""Offline contract tests for the skbrain P1 live foundation."""

from __future__ import annotations

from pathlib import Path

import pytest

from skos.brain.ops.doctor import run_checks
from skos.brain.ops.read_api import OpsReader, build_retriever
from skos.brain.ops.secrets import lint_text, lint_tree


class Cursor:
    def __init__(self, responses):
        self.responses = responses
        self.rows = []
        self.calls = []

    def __enter__(self): return self
    def __exit__(self, *args): return None
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self.rows = self.responses.pop(0)
    def fetchall(self): return list(self.rows)
    def fetchone(self): return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, responses): self.cur = Cursor(responses)
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def cursor(self): return self.cur


class Embedder:
    def embed(self, texts): return [[0.0] * 1024 for _ in texts]


def test_secret_lint_redacts_value(tmp_path: Path):
    secret = "super-secret-value-123"
    page = tmp_path / "page.md"
    page.write_text(f"password={secret}\n", encoding="utf-8")
    findings = lint_tree(tmp_path)
    assert [(f.line, f.rule) for f in findings] == [(1, "credential-assignment")]
    assert secret not in repr(findings)


def test_secret_lint_detects_private_key_and_uri():
    findings = lint_text("-----BEGIN PRIVATE KEY-----\npostgres://u:password123@db/x")
    assert {f.rule for f in findings} == {"private-key", "uri-credential"}


def test_reader_bounds_limit_and_attributes_results():
    conn = Connection([[("runbook-x", "runbook", "Recover X", "Verify first", 0.7)]])
    reader = OpsReader("redacted", connect=lambda dsn: conn, embedder=Embedder())
    hits = reader.search("x", limit=999)
    assert hits[0].node_id == "runbook-x"
    assert conn.cur.calls[0][1][2] == 25


def test_build_retriever_requires_reader_dsn(monkeypatch):
    monkeypatch.delenv("SKBRAIN_PG_READER_DSN", raising=False)
    with pytest.raises(Exception, match="not configured"):
        build_retriever()


def test_doctor_fails_closed_without_database(tmp_path: Path):
    (tmp_path / "page.md").write_text("safe content", encoding="utf-8")
    checks = run_checks(canon=tmp_path, reader_dsn=None)
    by_name = {c.name: c for c in checks}
    assert by_name["skbrain:content"].ok
    assert by_name["skbrain:secret-lint"].ok
    assert not by_name["skbrain:schema"].ok


def test_doctor_checks_schema_grants_and_population(tmp_path: Path):
    (tmp_path / "page.md").write_text("safe content", encoding="utf-8")
    conn = Connection([[("ops.wiki_nodes", "ops.wiki_chunks", "ops.links")],
                       [(True, True)], [(3, 60)]])
    checks = run_checks(canon=tmp_path, reader_dsn="redacted", connect=lambda dsn: conn)
    assert all(c.ok for c in checks)
