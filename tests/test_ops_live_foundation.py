"""Offline contract tests for the skbrain P1 live foundation."""

from __future__ import annotations

from pathlib import Path

import pytest
from skos.brain.ops.doctor import run_checks
from skos.brain.ops.read_api import OpsReader, build_retriever
from skos.brain.ops.secrets import lint_text, lint_tree
from skos.packs.loader import load_manifest_dict


class Cursor:
    def __init__(self, responses):
        self.responses = responses
        self.rows = []
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self.rows = self.responses.pop(0)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, responses):
        self.cur = Cursor(responses)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return self.cur


class Embedder:
    def embed(self, texts):
        return [[0.0] * 1024 for _ in texts]


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
    conn = Connection(
        [[("ops.wiki_nodes", "ops.wiki_chunks", "ops.links")], [(True, True)], [(3, 60)]]
    )
    checks = run_checks(canon=tmp_path, reader_dsn="redacted", connect=lambda dsn: conn)
    by_name = {c.name: c for c in checks}
    assert by_name["skbrain:schema"].ok
    assert by_name["skbrain:grants"].ok
    assert by_name["skbrain:projector"].ok


def test_doctor_all_checks_green_when_every_dependency_is_healthy(tmp_path: Path):
    """A fully hermetic "everything is green" scenario: an injected kedb_loader
    stands in for the optional skcapstone/skcoord sibling (absent in CI), so
    skbrain:kedb, like every other check here, is exercised without touching a
    real ~/.skcapstone or requiring the sibling package to be installed."""
    conn = Connection(
        [[("ops.wiki_nodes", "ops.wiki_chunks", "ops.links")], [(True, True)], [(3, 60)]]
    )
    checks = run_checks(
        canon=tmp_path,
        reader_dsn="redacted",
        connect=lambda dsn: conn,
        kedb_loader=lambda home: [],  # vacuous: no authoritative entries to cover
    )
    by_name = {c.name: c for c in checks}
    assert by_name["skbrain:kedb"].ok, by_name["skbrain:kedb"].detail
    assert by_name["skbrain:adapter"].ok, by_name["skbrain:adapter"].detail
    # skbrain:cron reflects real host fleet-object state (not injectable the
    # way DB/kedb are: it reads installed files, not a live connection), so it
    # is asserted for shape, not a fixed verdict, here.
    assert isinstance(by_name["skbrain:cron"].ok, bool)


# ---------------------------------------------------------------------------
# Manifest <-> doctor contract (card 105315b6, task 2b)
# ---------------------------------------------------------------------------
#
# The signed manifest's `doctor` install step (skos/packs/skbrain/
# skworld.module.json) declares the check NAMES a fully provisioned skbrain
# pack promises to run. Before this card, 3 of the 7 declared names
# (skbrain:kedb, skbrain:adapter, skbrain:cron) had no implementation at all
# -- `skbrain doctor` could never report on them, so "doctor green" could not
# reflect their health. This test locks in the fix in the direction that
# actually caused the bug: every check the manifest DECLARES must have a real
# implementation. It intentionally does NOT require the reverse (every
# IMPLEMENTED check must be declared): skbrain:secret-lint is implemented but
# not declared, which is harmless (doctor simply runs one more check than
# ATLAS requires) and predates this card / is out of its scope.


def test_doctor_implements_every_manifest_declared_check(tmp_path: Path):
    declared = {
        check
        for step in load_manifest_dict("skbrain")["install"]["steps"]
        if step.get("kind") == "doctor"
        for check in step.get("checks", [])
    }
    assert declared, "sanity: the manifest's doctor step declares at least one check"

    conn = Connection(
        [[("ops.wiki_nodes", "ops.wiki_chunks", "ops.links")], [(True, True)], [(3, 60)]]
    )
    implemented = {
        c.name
        for c in run_checks(
            canon=tmp_path,
            reader_dsn="redacted",
            connect=lambda dsn: conn,
            kedb_loader=lambda home: [],
        )
    }

    missing = declared - implemented
    assert not missing, (
        f"the manifest declares doctor check(s) {sorted(missing)} that "
        "skos.brain.ops.doctor.run_checks() never emits -- ATLAS can never see "
        "their health. Either implement them or remove them from "
        "skos/packs/skbrain/skworld.module.json."
    )
