"""The fresh-node gate mounts the skmem-pg init source that holds schema.sql.

skmemory #171 moved schema.sql and the migrations from deploy/skmem-pg into
the wheel at skmemory/pg. The gate kept mounting deploy/skmem-pg, so the init
wrapper failed on a missing schema.sql, postgres exited, and the gate reported
a harness setup failure. These tests need no docker.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fresh-node-gate.sh"


def _repo(tmp_path: Path, schema_dir: str | None) -> Path:
    repo = tmp_path / "skmemory"
    init = repo / "deploy" / "skmem-pg" / "initdb"
    init.mkdir(parents=True)
    (init / "00-run-init.sh").write_text("#!/bin/sh\n")
    if schema_dir:
        d = repo / schema_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "schema.sql").write_text("-- schema\n")
    return repo


def _resolve(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        SKMEMORY_REPO=str(repo),
        FRESH_NODE_GATE_RESOLVE_ONLY="1",
        TMPDIR=str(tmp_path),
        EVIDENCE_DIR=str(tmp_path / "ev"),
    )
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )


def test_mounts_packaged_pg_dir(tmp_path):
    repo = _repo(tmp_path, "skmemory/pg")
    out = _resolve(repo, tmp_path)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(repo / "skmemory" / "pg")


def test_prefers_packaged_dir_over_stale_copy(tmp_path):
    repo = _repo(tmp_path, "skmemory/pg")
    (repo / "deploy" / "skmem-pg" / "schema.sql").write_text("-- stale\n")
    assert _resolve(repo, tmp_path).stdout.strip() == str(repo / "skmemory" / "pg")


def test_old_layout_still_works(tmp_path):
    repo = _repo(tmp_path, "deploy/skmem-pg")
    assert _resolve(repo, tmp_path).stdout.strip() == str(repo / "deploy" / "skmem-pg")


def test_missing_schema_fails_loudly(tmp_path):
    out = _resolve(_repo(tmp_path, None), tmp_path)
    assert out.returncode == 2
    assert "schema.sql" in out.stderr
