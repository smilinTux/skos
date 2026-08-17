"""Card 99c33052: the model-fidelity adapter CONSUMES skgateway's artifact.

Why this adapter exists, so nobody later "simplifies" it into a live probe:
on 2026-08-16 node .100 was hard down for about 1h45m and nothing reported it,
because `sk-default` kept returning HTTP 200 served from NVIDIA's cloud. A 200
is not evidence that the thing you believe is answering is answering.

Why it CONSUMES rather than probes: skgateway already runs the probe. Two probes
of one fact drift apart, and then both keep answering confidently while
disagreeing. This fleet produced four separate instances of that shape in one
week, so a second prober was the one design we would not build.

NOTHING HERE READS THE OPERATOR'S REAL ARTIFACT. Every test injects a path under
`tmp_path`, and `test_no_test_reads_the_live_artifact_path` proves the seam is
the only door.
"""

from __future__ import annotations

import json

import pytest

from skos.watchdog.adapters import model_fidelity as mf
from skos.watchdog.adapters.model_fidelity import (
    SUPPORTED_ARTIFACT_VERSION,
    ArtifactUnusable,
    ModelFidelityAdapter,
)
from skos.watchdog.port import Window, collect_safe

NOW = "2026-08-17T12:00:00Z"
FRESH = "2026-08-17T11:00:00Z"        # 1h old, well inside the 26h window
STALE = "2026-08-14T11:00:00Z"        # ~73h old


def _window() -> Window:
    return Window(since="2026-08-16T12:00:00Z", until=NOW)


def _artifact(tmp_path, **overrides):
    """A REAL-shaped artifact, matching a genuine one taken off this fleet.

    Defaults deliberately mirror the live document rather than an idealised
    one: `drift: true` with `role_fidelity.alarm: false`, because on this fleet
    drift is the union of five checks and is routinely true for dead
    third-party models while role fidelity is perfectly healthy.
    """
    doc = {
        "artifact_version": SUPPORTED_ARTIFACT_VERSION,
        "finished_at": FRESH,
        "endpoint": "http://localhost:18780",
        "checked": True,
        "error": None,
        "drift": True,
        "role_fidelity": {
            "alarm": False,
            "mismatches": [],
            "entries": [
                {"role": "sk-default", "backend": "ornith", "expected": "ornith-1.0-9b",
                 "served": "ornith-1.0-9b", "error": None, "faithful": True},
                {"role": "sk-heavy", "backend": "anthropic", "expected": "claude-opus-5",
                 "served": "claude-opus-5", "error": None, "faithful": True},
            ],
        },
        "liveness": {"dead_count": 11, "dead": []},
        "failover": {"live_count": 2, "alarm": False},
    }
    doc.update(overrides)
    p = tmp_path / "catalog-verify.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _kinds(events):
    return [e.kind for e in events]


def _by_kind(events, kind):
    return [e for e in events if e.kind == kind]


# --- the healthy path -------------------------------------------------------


def test_a_faithful_run_reports_healthy_and_raises_no_problem(tmp_path):
    events = ModelFidelityAdapter(artifact_path=_artifact(tmp_path)).collect(_window())

    assert "RoleFidelityHealthy" in _kinds(events)
    assert not [e for e in events if e.severity == "problem"], (
        "a run where every role is faithful must not carry a problem"
    )


def test_drift_alone_does_NOT_raise_a_problem(tmp_path):
    """The single most important negative in this file.

    Top-level `drift` is the UNION of all five catalog checks. The real artifact
    carries `drift: true` on a day when role fidelity is perfectly healthy,
    because 11 advertised third-party models are dead. Keying a problem on
    `drift` would put an alarm in the digest every single morning, and a reader
    who sees the same alarm every morning stops reading the digest. That is how
    a watchdog dies.
    """
    path = _artifact(tmp_path, drift=True)
    events = ModelFidelityAdapter(artifact_path=path).collect(_window())

    assert not [e for e in events if e.severity == "problem"], (
        f"drift=true with role_fidelity.alarm=false must stay quiet, got "
        f"{[(e.kind, e.severity) for e in events]}"
    )


# --- the substitution condition, which is the whole point -------------------


def test_a_substituted_role_is_a_problem(tmp_path):
    """The .100 signature: the role answered, but not by its own model."""
    path = _artifact(tmp_path, role_fidelity={
        "alarm": True,
        "mismatches": [{"role": "sk-default", "expected": "ornith-1.0-9b",
                        "served": "meta/llama-3.3-70b-instruct"}],
        "entries": [
            {"role": "sk-default", "backend": "ornith", "expected": "ornith-1.0-9b",
             "served": "meta/llama-3.3-70b-instruct", "error": None, "faithful": False},
        ],
    })
    events = ModelFidelityAdapter(artifact_path=path).collect(_window())

    problems = [e for e in events if e.severity == "problem"]
    assert problems, "a role served by a different model must be a problem"
    assert any("sk-default" in (e.object or "") or "sk-default" in e.summary
               for e in problems)


def test_faithful_null_is_liveness_NOT_a_substitution(tmp_path):
    """`faithful: null` means the role ERRORED, so fidelity was not assessable.

    Reporting that as "a different model answered" would be a false accusation
    against a backend that simply did not respond. The producer chose null over
    false precisely so a consumer could tell them apart.
    """
    path = _artifact(tmp_path, role_fidelity={
        "alarm": False,
        "mismatches": [],
        "entries": [
            {"role": "sk-vision", "backend": "chiap08", "expected": "qwen3.8-27b",
             "served": None, "error": "connection refused", "faithful": None},
        ],
    })
    events = ModelFidelityAdapter(artifact_path=path).collect(_window())

    assert not _by_kind(events, "RoleModelSubstituted"), (
        "faithful=null is an unreachable role, not a substituted one"
    )


# --- the three null-ish states, which must stay distinguishable -------------


def test_role_fidelity_null_is_not_an_empty_entries_list(tmp_path):
    """`null` means no result exists. `[]` would mean checked and nothing wrong.

    Collapsing them would let "the fidelity check did not run" render as "every
    role is faithful", which is the exact substitution of absence for evidence
    this adapter exists to prevent.
    """
    path = _artifact(tmp_path, role_fidelity=None)
    events = ModelFidelityAdapter(artifact_path=path).collect(_window())

    assert not _by_kind(events, "RoleFidelityHealthy"), (
        "a null role_fidelity must never report the roles healthy"
    )


# --- absence of a signal is not evidence of health --------------------------


@pytest.mark.parametrize("bad", ["missing", "unparseable", "wrong_version", "no_finished_at"])
def test_an_unusable_artifact_is_a_GAP_never_an_all_clear(tmp_path, bad):
    if bad == "missing":
        path = tmp_path / "absent.json"
    elif bad == "unparseable":
        path = tmp_path / "catalog-verify.json"
        path.write_text("{not json at all", encoding="utf-8")
    elif bad == "wrong_version":
        path = _artifact(tmp_path, artifact_version=SUPPORTED_ARTIFACT_VERSION + 99)
    else:
        path = _artifact(tmp_path, finished_at=None)

    adapter = ModelFidelityAdapter(artifact_path=path)
    with pytest.raises(ArtifactUnusable):
        adapter.collect(_window())

    # and through the port it degrades to a visible line, not silence
    events = collect_safe(adapter, _window())
    assert events, "an unusable artifact must produce a digest line, never nothing"
    assert not [e for e in events if e.severity == "problem"]


def test_a_STALE_artifact_is_a_gap_and_this_is_what_would_have_caught_dot100(tmp_path):
    """Staleness is the CONSUMER's job, by the producer's deliberate design.

    The artifact carries no freshness verdict of its own, because a producer
    asserting its own freshness is another self-certifying gate. `finished_at`
    is always present, including on the unreachable path, so the reader ages it.

    A stale artifact describes a moment that has passed. Reading it as an
    all-clear is precisely how .100 stayed invisible for four hours.
    """
    path = _artifact(tmp_path, finished_at=STALE)

    with pytest.raises(ArtifactUnusable, match="STALE"):
        ModelFidelityAdapter(artifact_path=path).collect(_window())


def test_checked_false_is_a_deliberate_state_and_reads_as_a_gap(tmp_path):
    """An unreachable gateway still writes an artifact, on purpose.

    There is no state where the job runs, fails to check, and leaves behind
    something readable as clean. Honour that: checked=false is a gap.
    """
    path = _artifact(tmp_path, checked=False, error="ECONNREFUSED", drift=False)

    with pytest.raises(ArtifactUnusable, match="could not check"):
        ModelFidelityAdapter(artifact_path=path).collect(_window())


# --- the seam ---------------------------------------------------------------


def test_no_test_reads_the_live_artifact_path(tmp_path, monkeypatch):
    """The live path must be reachable ONLY through the injected seam.

    Guard the mechanism, not today's behaviour: make the default resolver
    explode, then assert an explicitly-pathed adapter still works. If any code
    path fell back to the operator's real artifact, this goes red.
    """
    def _boom():
        raise AssertionError("a test resolved the LIVE artifact path")

    monkeypatch.setattr(mf, "default_artifact_path", _boom)

    events = ModelFidelityAdapter(artifact_path=_artifact(tmp_path)).collect(_window())
    assert "RoleFidelityHealthy" in _kinds(events)
