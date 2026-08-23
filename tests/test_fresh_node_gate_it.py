"""Fresh-node E2E acceptance gate for skbrain (card d17892ae, epic fb3cc09d).

Card f3cb6231 says skbrain install/doctor/observe work is complete only after
a fresh-node observation passes. The 2026-08-23 audit found no such gate
existed anywhere in skcapstone/skcoord/skos/skharness/skdashboard -- the only
drill-like coverage was test_pack_provisioner.py, which drives the provisioner
with a mocked ``Effects`` protocol (dependency-injected fakes), never a real
install on a clean node.

This test is the CI entry point for the REAL gate: scripts/fresh-node-gate.sh
builds a throwaway container with no ~/.skcapstone, ~/.skenv, or any prior
pack state, boots a separate ephemeral skmem-pg instance, and runs
``skos install skbrain`` through the real, unmocked ``DefaultEffects``
(subprocess calls to the real ``skmemory`` CLI, real skvault file-backend
crypto, a real Postgres), then ``skbrain doctor`` and
``skbrain operator observe``. See docs/runbooks/skbrain-fresh-node-gate.md for
exactly what this isolation does and does not prove.

Gated behind RUN_FRESH_NODE_GATE=1 (heavy: builds a Docker image and boots a
full skmem-pg instance with BM25 + AGE + pgvector extensions, well outside a
default `pytest` run's time budget) plus docker + the skmem-pg image being
available locally, mirroring the RUN_SANDBOX_IT=1 precedent
(tests/test_sandbox_confinement_it.py).

EXPECTED RESULT as of 2026-08-23: this test asserts the HARNESS ran to
completion and produced an evidence artifact -- it does NOT assert the gate
itself is green. Two of skbrain's four operator conditions
(CmdbDriftBounded, KedbCanonCovered) are hardcoded "Unknown" literals in
src/skos/brain/ops/cli.py regardless of real state, so
``skos install skbrain --force`` genuinely running and
``skbrain operator observe`` genuinely reporting is the correct, useful
result even while gate_pass is False. Weakening this test to require
gate_pass=True would hide that finding instead of surfacing it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = REPO_ROOT / "scripts" / "fresh-node-gate.sh"

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_FRESH_NODE_GATE") or not shutil.which("docker"),
    reason="integration: set RUN_FRESH_NODE_GATE=1 and have docker + the skmem-pg image locally",
)


def test_fresh_node_gate_harness_runs_and_produces_evidence(tmp_path):
    """The harness itself must complete and write a machine-readable report.

    Deliberately does NOT assert report["gate_pass"] is True -- see module
    docstring. A future card that fixes the hardcoded Unknown operator
    conditions can tighten this to require gate_pass once it is genuinely
    achievable; doing so here would just be a second hardcoded "Unknown".
    """
    evidence_dir = tmp_path / "evidence"
    env = dict(os.environ)
    env["EVIDENCE_DIR"] = str(evidence_dir)

    proc = subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    print(proc.stdout)
    print(proc.stderr)

    # Exit code 2 means a HARNESS setup failure (docker missing, postgres
    # never came up, build failure) -- that's a real bug in the harness or
    # environment and should fail this test. 0 or 1 both mean the harness ran
    # to completion; 1 additionally means the gate did not pass.
    assert proc.returncode in (0, 1), (
        f"fresh-node-gate.sh harness setup failed (exit {proc.returncode}); "
        "see captured stdout/stderr above"
    )

    report_path = evidence_dir / "report.json"
    assert report_path.is_file(), "harness did not write an evidence report"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("harness_ok") is True
    assert "install" in report.get("steps", {})
    assert "doctor" in report.get("steps", {})
    assert "operator_observe" in report.get("steps", {})
    assert report.get("gate_conditions") is not None, (
        "operator observe produced no parseable conditions; the real "
        "install path likely never reached a state doctor could evaluate"
    )
