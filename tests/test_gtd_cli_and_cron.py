"""Tests for the `skos gtd` CLI sub-app and the sk-cron-run.sh failure path.

Card 0f9d3aca: every GTD writer must go through the locked skos.gtd_ingest
library. sk-cron-run.sh no longer manipulates JSON inline; it shells out to
`skos gtd capture` (or the library fallback), which dedupes across the WHOLE
store by (source, source_ref).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skos.cli import app
from skos.gtd_ingest import GtdCapture, capture, gtd_dir

REPO = Path(__file__).resolve().parents[1]
CRON_SH = REPO / "scripts" / "sk-cron-run.sh"

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    yield


# ── skos gtd capture ─────────────────────────────────────────────────────────

def test_cli_capture_creates_item():
    res = runner.invoke(app, ["gtd", "capture", "fix the thing",
                              "--source", "cron", "--source-ref", "cron:x@2026-07-17",
                              "--context", "@ops", "--priority", "high"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith("captured ")
    items = json.loads((gtd_dir() / "inbox.json").read_text())
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "cron" and it["source_ref"] == "cron:x@2026-07-17"
    assert it["context"] == "@ops" and it["priority"] == "high"


def test_cli_capture_dedupes_whole_store_not_just_inbox():
    # existing item lives in next-actions.json, NOT inbox.json
    assert capture(GtdCapture(text="already tracked", source="cron",
                              source_ref="cron:job@today", status="next"))
    res = runner.invoke(app, ["gtd", "capture", "dup attempt",
                              "--source", "cron", "--source-ref", "cron:job@today"])
    assert res.exit_code == 0, res.output
    assert "duplicate" in res.output
    inbox = json.loads((gtd_dir() / "inbox.json").read_text()) \
        if (gtd_dir() / "inbox.json").exists() else []
    assert inbox == []


def test_cli_capture_rejects_bad_meta():
    res = runner.invoke(app, ["gtd", "capture", "x", "--meta", "not-json"])
    assert res.exit_code != 0


def test_cli_upsert_create_then_update():
    r1 = runner.invoke(app, ["gtd", "upsert", "order shipped",
                             "--source", "order", "--source-ref", "ord-1",
                             "--status", "waiting"])
    assert r1.exit_code == 0 and r1.output.startswith("created ")
    r2 = runner.invoke(app, ["gtd", "upsert", "order delivered",
                             "--source", "order", "--source-ref", "ord-1",
                             "--status", "done"])
    assert r2.exit_code == 0 and r2.output.startswith("completed ")
    archive = json.loads((gtd_dir() / "archive.json").read_text())
    assert any(i.get("source_ref") == "ord-1" for i in archive)


# ── sk-cron-run.sh failure capture goes through the library ─────────────────

def test_cron_script_has_no_inline_json_manipulation():
    body = CRON_SH.read_text()
    # the run-ledger JSONL append may json.dumps a record, but nothing may
    # load or rewrite the GTD store files directly
    assert "json.dump(" not in body
    assert "json.load(" not in body
    assert "GTD_INBOX" not in body
    assert "gtd capture" in body


def _run_cron(env, *cmd):
    return subprocess.run(["bash", str(CRON_SH), *cmd],
                          capture_output=True, text=True, env=env)


@pytest.mark.skipif(not CRON_SH.exists(), reason="cron wrapper missing")
def test_forced_cron_failure_captures_exactly_once(tmp_path):
    env = dict(os.environ)
    env["SK_GTD_DIR"] = os.environ["SK_GTD_DIR"]
    env["PY"] = sys.executable
    env["SKOS_BIN"] = "/nonexistent-skos"          # force the library fallback
    env["HOME"] = str(tmp_path)                    # keep ledger + alerts sandboxed
    env["PATH"] = os.environ.get("PATH", "")

    r1 = _run_cron(env, "unit-test-job", "false")
    assert r1.returncode == 1                      # wrapped exit code passes through
    r2 = _run_cron(env, "unit-test-job", "false")  # same job, same day -> dedupe
    assert r2.returncode == 1

    items = json.loads((gtd_dir() / "inbox.json").read_text())
    cron_items = [i for i in items if i["source"] == "cron"
                  and i["source_ref"].startswith("cron:unit-test-job@")]
    assert len(cron_items) == 1, items
    assert cron_items[0]["priority"] == "high" and cron_items[0]["context"] == "@ops"


@pytest.mark.skipif(not Path(os.path.expanduser("~/.skenv/bin/skos")).exists(),
                    reason="skos console script not installed")
def test_forced_cron_failure_via_cli_binary(tmp_path):
    env = dict(os.environ)
    env["SK_GTD_DIR"] = os.environ["SK_GTD_DIR"]
    env["PY"] = sys.executable
    env["SKOS_BIN"] = os.path.expanduser("~/.skenv/bin/skos")
    env["HOME"] = str(tmp_path)
    env["PATH"] = os.environ.get("PATH", "")

    r1 = _run_cron(env, "unit-test-cli", "false")
    assert r1.returncode == 1
    r2 = _run_cron(env, "unit-test-cli", "false")
    assert r2.returncode == 1

    items = json.loads((gtd_dir() / "inbox.json").read_text())
    refs = [i["source_ref"] for i in items
            if i["source_ref"].startswith("cron:unit-test-cli@")]
    assert len(refs) == 1, items
