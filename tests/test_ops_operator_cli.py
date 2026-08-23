"""Contract tests for `skbrain operator` (explain/observe/act) -- card 105315b6.

Hermetic: SKCAPSTONE_HOME and SKBRAIN_PG_READER_DSN are pointed at a tmp_path /
unset respectively, so nothing here touches a real ~/.skcapstone, a real
skbrain-ops canon checkout, or a live database.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from skos.brain.ops.cli import _CONDITIONS, app

runner = CliRunner()


def _invoke_json(monkeypatch, tmp_path, *args):
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "skcapstone-home"))
    monkeypatch.delenv("SKBRAIN_PG_READER_DSN", raising=False)
    result = runner.invoke(app, ["operator", *args, "--json"])
    assert result.exit_code in (0, 3), result.output
    return json.loads(result.output)


def test_explain_declares_all_four_conditions():
    result = runner.invoke(app, ["operator", "explain", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["conditions"] == list(_CONDITIONS)
    assert "CmdbDriftBounded" in payload["conditions"]
    assert "KedbCanonCovered" in payload["conditions"]


def test_observe_emits_every_condition_explain_declares(monkeypatch, tmp_path):
    explain = json.loads(runner.invoke(app, ["operator", "explain", "--json"]).output)
    observe = _invoke_json(monkeypatch, tmp_path, "observe", "--canon", str(tmp_path / "canon"))

    explained_types = explain["conditions"]
    observed_types = [c["type"] for c in observe["conditions"]]
    assert observed_types == explained_types, (
        "operator explain and operator observe must emit the identical condition "
        f"set in the identical order; explain={explained_types} observe={observed_types}"
    )


def test_observe_kedb_canon_covered_has_enumerable_gaps(monkeypatch, tmp_path):
    observe = _invoke_json(monkeypatch, tmp_path, "observe", "--canon", str(tmp_path / "canon"))
    kedb = next(c for c in observe["conditions"] if c["type"] == "KedbCanonCovered")
    assert "gaps" in kedb
    assert set(kedb["gaps"]) == {"missing_from_canon", "dangling_canon_refs"}
    assert isinstance(kedb["gaps"]["missing_from_canon"], list)
    assert isinstance(kedb["gaps"]["dangling_canon_refs"], list)


def test_observe_kedb_canon_covered_is_unknown_when_fold_uninitialized(monkeypatch, tmp_path):
    # SKCAPSTONE_HOME points at a fresh tmp dir: the skcapstone/skcoord sibling
    # may or may not be importable in this test env, but either way its ITIL
    # kedb directory has never been created under this tmp home, so the fold
    # is genuinely unreachable and the condition must say so explicitly.
    observe = _invoke_json(monkeypatch, tmp_path, "observe", "--canon", str(tmp_path / "canon"))
    kedb = next(c for c in observe["conditions"] if c["type"] == "KedbCanonCovered")
    assert kedb["status"] == "Unknown"
    assert kedb["reason"]  # non-empty, explicit


def test_observe_cmdb_drift_bounded_is_untouched():
    """CmdbDriftBounded stays owned by card 5dcf04d1 -- assert the literal is
    byte-identical to what it was before this card, so a future edit here is a
    loud, deliberate diff rather than an accidental drive-by change."""
    result = runner.invoke(app, ["operator", "observe", "--json"])
    payload = json.loads(result.output)
    cmdb = next(c for c in payload["conditions"] if c["type"] == "CmdbDriftBounded")
    assert cmdb == {
        "type": "CmdbDriftBounded",
        "status": "Unknown",
        "reason": "CMDB evidence is owned by the CMDB adapter",
    }


def test_act_refuses_and_exits_nonzero():
    result = runner.invoke(app, ["operator", "act", "--json"])
    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload == {
        "performed": False,
        "reason": "skbrain operator facet is observation-only",
    }
