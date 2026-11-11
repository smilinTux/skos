"""Tests for the CmdbDriftBounded rule (card 5dcf04d1)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skos.brain.ops.cmdb_drift import BASELINE_PATH, compute_cmdb_drift

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _artifact(age_hours: float = 1, dno: int = 19, **overrides) -> dict:
    artifact = {
        "ended_at": (NOW - timedelta(hours=age_hours)).isoformat(),
        "scope": "local",
        "completeness": {"complete": True},
        "reconcile": {"validation_failures": []},
        "drift": {
            "count": dno + 5,
            "by_kind": {"declared_not_observed": dno, "stored_not_discovered": 5},
            "by_severity": {"high": dno, "medium": 5, "low": 0},
        },
    }
    artifact.update(overrides)
    return artifact


@pytest.fixture
def canon(tmp_path: Path) -> Path:
    path = tmp_path / BASELINE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"local": {"declared_not_observed": 19}}))
    return tmp_path


def _compute(canon: Path, *artifacts: dict):
    return compute_cmdb_drift(
        canon, artifact_loader=lambda _home: list(artifacts), now=NOW
    )


def test_true_states_age_scope_and_counts(canon):
    result = _compute(canon, _artifact())
    assert result.status == "True"
    for expected in (
        "age 3600s",
        "scope local",
        "declared_not_observed=19",
        "baseline 19",
    ):
        assert expected in result.reason


def test_unknown_when_no_artifact_verifies(canon):
    result = _compute(canon)
    assert result.status == "Unknown"
    assert "no checksum-verified" in result.reason


def test_unknown_when_reader_fails(canon):
    def boom(_home):
        raise ModuleNotFoundError("skcoord")

    result = compute_cmdb_drift(canon, artifact_loader=boom, now=NOW)
    assert result.status == "Unknown"
    assert "reader failed" in result.reason


@pytest.mark.parametrize(
    ("artifact", "needle"),
    [
        (_artifact(age_hours=7.5), "older than"),
        (_artifact(completeness={"complete": False}), "incomplete"),
        (
            _artifact(reconcile={"validation_failures": [{"reason": "x"}]}),
            "validation failures",
        ),
        (_artifact(drift={"count": 1, "by_severity": {"high": 1}}), "no drift summary"),
        ({k: v for k, v in _artifact().items() if k != "drift"}, "no drift summary"),
        (_artifact(dno=20), "above baseline 19"),
    ],
)
def test_false_cases(canon, artifact, needle):
    result = _compute(canon, artifact)
    assert result.status == "False"
    assert needle in result.reason


def test_stale_artifact_is_false_even_with_good_counts(canon):
    assert _compute(canon, _artifact(age_hours=360)).status == "False"


def test_seven_hours_exactly_is_still_fresh(canon):
    assert _compute(canon, _artifact(age_hours=7)).status == "True"


def test_newest_is_chosen_by_ended_at_not_list_order(canon):
    assert _compute(canon, _artifact(age_hours=20), _artifact()).status == "True"
    assert _compute(canon, _artifact(dno=99), _artifact(age_hours=20)).status == "False"


def test_only_declared_not_observed_gates(canon):
    artifact = _artifact()
    artifact["drift"]["by_kind"]["stored_not_discovered"] = 100000
    assert _compute(canon, artifact).status == "True"


def test_no_verifiable_timestamp_is_false(canon):
    assert _compute(canon, _artifact(ended_at="not-a-date")).status == "False"
    assert _compute(canon, _artifact(ended_at="2026-11-06T11:00:00")).status == "False"


@pytest.mark.parametrize(
    "baseline",
    [
        "not json",
        "[]",
        json.dumps({"local": {}}),
        json.dumps({"local": {"declared_not_observed": -1}}),
        json.dumps({"local": {"declared_not_observed": True}}),
        json.dumps({"local": {"declared_not_observed": "19"}}),
    ],
)
def test_invalid_baseline_is_false(canon, baseline):
    (canon / BASELINE_PATH).write_text(baseline)
    result = _compute(canon, _artifact())
    assert result.status == "False"
    assert "baseline" in result.reason


def test_missing_baseline_is_false(canon):
    (canon / BASELINE_PATH).unlink()
    result = _compute(canon, _artifact())
    assert result.status == "False"
    assert "missing" in result.reason


# -- per scope: each named scope against its own baseline (2026-11-11) --------


def _baseline_file(canon: Path, value: dict) -> None:
    (canon / BASELINE_PATH).write_text(json.dumps(value))


def test_a_newer_unnamed_scope_does_not_mask_the_gated_one(canon):
    """Live case: recording network shadows flipped the local gate red because
    the newest artifact of ANY scope was judged."""
    result = _compute(
        canon, _artifact(age_hours=2), _artifact(age_hours=0.1, scope="network")
    )
    assert result.status == "True"
    assert "scope local" in result.reason
    assert "scope network" in result.reason and "not gated" in result.reason


def test_an_ungated_named_scope_is_reported_not_gating(canon):
    _baseline_file(
        canon,
        {
            "local": {"declared_not_observed": 19},
            "network": {"declared_not_observed": 2, "gated": False},
        },
    )
    network = _artifact(age_hours=30, dno=50, scope="network")
    result = _compute(canon, _artifact(), network)
    assert result.status == "True"
    assert "network" in result.reason and "not gated" in result.reason
    assert "above baseline 2" in result.reason


def test_every_gated_scope_must_pass(canon):
    _baseline_file(
        canon,
        {
            "local": {"declared_not_observed": 19},
            "network": {"declared_not_observed": 2},
        },
    )
    good_network = _artifact(dno=2, scope="network")
    assert _compute(canon, _artifact(), good_network).status == "True"
    bad = _compute(canon, _artifact(), _artifact(dno=3, scope="network"))
    assert bad.status == "False" and "scope network" in bad.reason
    stale = _compute(canon, _artifact(), _artifact(dno=2, age_hours=8, scope="network"))
    assert stale.status == "False" and "older than" in stale.reason


def test_a_gated_scope_with_no_artifact_is_false_not_unknown(canon):
    _baseline_file(
        canon,
        {
            "local": {"declared_not_observed": 19},
            "network": {"declared_not_observed": 2},
        },
    )
    result = _compute(canon, _artifact())
    assert result.status == "False"
    assert "no verified artifact for scope 'network'" in result.reason


def test_only_an_unnamed_scope_present_is_false(canon):
    result = _compute(canon, _artifact(scope="network"))
    assert result.status == "False"
    assert "no verified artifact for scope 'local'" in result.reason


def test_newest_is_chosen_per_scope(canon):
    old_bad = _artifact(age_hours=5, dno=99)
    assert _compute(canon, old_bad, _artifact(age_hours=1)).status == "True"


@pytest.mark.parametrize(
    "baseline",
    [
        {"local": {"declared_not_observed": 19, "gated": "no"}},
        {"local": {"declared_not_observed": 19, "gated": False}},
    ],
)
def test_no_valid_gated_scope_is_false(canon, baseline):
    _baseline_file(canon, baseline)
    result = _compute(canon, _artifact())
    assert result.status == "False"
    assert "baseline" in result.reason
