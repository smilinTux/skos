"""Tests for the `skos operator` CLI and its explain / observe / act contract.

Hermetic: the health probe and the act runner are both injectable, so nothing
here touches a live skos, the cron ledger, the GTD store, systemd, or a network.
The contract shape is asserted directly and (when the optional sibling skcapstone
is installed) compared byte-for-byte against Atlas's skos adapter.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from skos import operator_probe as op
from skos.cli import app

runner = CliRunner()


# --- explain -----------------------------------------------------------------


def test_explain_shape_matches_contract():
    c = op.explain()
    assert c["kinds"] == ["scheduler", "gtd", "watchdog", "grading"]
    assert c["conditions"] == [
        "SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog",
    ]
    names = [a["name"] for a in c["actions"]]
    assert names == ["restart_service", "replay_errors"]
    for a in c["actions"]:
        # Every action carries the full metadata standard.
        assert set(a) == {"name", "standard", "reversible", "blast_radius",
                          "runbook", "kedb_refs"}
        assert a["standard"] is True
        assert a["reversible"] is True
        assert a["blast_radius"] == "low"
        assert isinstance(a["kedb_refs"], list)
    runbooks = {a["name"]: a["runbook"] for a in c["actions"]}
    assert runbooks["restart_service"] == "restart the skscheduler service"
    assert runbooks["replay_errors"] == "replay the skos error-recovery queue"


def test_cli_explain_emits_contract_json():
    r = runner.invoke(app, ["operator", "explain"])
    assert r.exit_code == 0, r.output
    c = json.loads(r.output)
    assert c["kinds"] == ["scheduler", "gtd", "watchdog", "grading"]
    assert c["conditions"] == [
        "SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog",
    ]
    assert [a["name"] for a in c["actions"]] == ["restart_service", "replay_errors"]


def test_explain_superset_of_adapter():
    """explain() must still carry every condition/kind Atlas's skos_adapter
    declares (a real drift check on the shared surface). It is allowed to be
    a strict superset until skcapstone lands its own WD-11 follow-up card
    (adding WatchdogDigestFresh + GradingBacklog to skos_adapter.py) -- that
    change is cross-repo and out of scope here. Actions are untouched by this
    card and must still match exactly."""
    skos_adapter = pytest.importorskip(
        "skcapstone.operator_seat.skos_adapter",
        reason="optional sibling skcapstone not installed",
    )
    c = op.explain()
    ac = skos_adapter.skos_explain()
    assert set(ac["conditions"]) <= set(c["conditions"])
    assert set(ac["kinds"]) <= set(c["kinds"])
    assert c["actions"] == ac["actions"]
    assert {"WatchdogDigestFresh", "GradingBacklog"} <= set(c["conditions"])


# --- observe -----------------------------------------------------------------


def _observe_types(doc):
    return [(c["type"], c["status"], c["object"]) for c in doc["conditions"]]


def test_observe_healthy_via_injected_probe():
    doc = op.observe(lambda: {
        "scheduler_alive": True, "gtd_draining": True,
        "digest_fresh": True, "grading_ok": True,
    })
    assert _observe_types(doc) == [
        ("SchedulerAlive", "True", "skscheduler"),
        ("GtdSinkDraining", "True", "gtd-sink"),
        ("WatchdogDigestFresh", "True", "watchdog-digest"),
        ("GradingBacklog", "True", "grading-loop"),
    ]


def test_observe_scheduler_firing():
    doc = op.observe(lambda: {"scheduler_alive": False, "gtd_draining": True})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["SchedulerAlive"] == "False"
    assert by["GtdSinkDraining"] == "True"


def test_observe_gtd_sink_firing():
    doc = op.observe(lambda: {"scheduler_alive": True, "gtd_draining": False})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["SchedulerAlive"] == "True"
    assert by["GtdSinkDraining"] == "False"


def test_observe_digest_stale_firing():
    doc = op.observe(lambda: {"digest_fresh": False})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["WatchdogDigestFresh"] == "False"
    # untouched keys still default healthy, per observe()'s own .get(..., True)
    assert by["SchedulerAlive"] == "True"


def test_observe_grading_backlog_firing():
    doc = op.observe(lambda: {"grading_ok": False})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["GradingBacklog"] == "False"


def test_observe_shared_conditions_match_adapter():
    """op.observe() is byte-compatible with Atlas's skos_adapter.skos_observe
    on every condition the adapter already knows about; the two NEW WD-11
    conditions are additions skos carries ahead of that mirror (see
    test_explain_superset_of_adapter) until skcapstone's own follow-up card
    lands -- cross-repo, out of scope here."""
    skos_adapter = pytest.importorskip(
        "skcapstone.operator_seat.skos_adapter",
        reason="optional sibling skcapstone not installed",
    )
    healthy = {
        "scheduler_alive": True, "gtd_draining": True,
        "digest_fresh": True, "grading_ok": True,
    }
    ours = {c["type"]: c for c in op.observe(lambda: healthy)["conditions"]}
    theirs = {c["type"]: c for c in skos_adapter.skos_observe(lambda: healthy)["conditions"]}
    for cond_type, cond in theirs.items():
        assert ours[cond_type] == cond
    assert set(ours) - set(theirs) == {"WatchdogDigestFresh", "GradingBacklog"}


def test_cli_observe_emits_conditions_json(monkeypatch):
    monkeypatch.setattr(
        op, "_default_probe",
        lambda: {
            "scheduler_alive": True, "gtd_draining": True,
            "digest_fresh": True, "grading_ok": True,
        },
    )
    r = runner.invoke(app, ["operator", "observe"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert {c["type"] for c in doc["conditions"]} == {
        "SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog",
    }


# --- default probe: real signals, fail safe ----------------------------------


def test_default_probe_fails_safe_when_unreachable(tmp_path, monkeypatch):
    """No ledger + empty GTD store -> healthy (fail safe), never a false alarm.

    SK_WATCHDOG_DIR is isolated too (a bare tmp dir with no digest ever
    published), even though this test does not assert on digest_fresh /
    grading_ok: by design a never-published digest reads as STALE, not
    unknown (see test_digest_age_is_infinite_when_never_published below), so
    asserting it healthy here would assert the wrong thing. This isolation
    only exists so the test never touches the real fleet's watchdog dir."""
    monkeypatch.setenv("SKOS_CRON_LEDGER", str(tmp_path / "nope.jsonl"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "empty-gtd"))
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    st = op._default_probe()
    assert st["scheduler_alive"] is True
    assert st["gtd_draining"] is True


def test_scheduler_alive_pure_rule():
    assert op._scheduler_alive(None) is True          # unknown -> safe (alive)
    assert op._scheduler_alive(60.0) is True           # fresh run -> alive
    assert op._scheduler_alive(op._SCHEDULER_MAX_AGE_S + 1) is False  # stale -> firing


def test_sink_draining_pure_rule():
    assert op._sink_draining(0) is True
    assert op._sink_draining(1) is False


def test_quarantine_backlog_makes_sink_fire(tmp_path, monkeypatch):
    gtd = tmp_path / "gtd"
    gtd.mkdir()
    (gtd / "inbox.json.corrupt-20260101T000000Z").write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("SK_GTD_DIR", str(gtd))
    assert op._count_quarantine(op._gtd_dir()) == 1
    assert op._sink_draining(op._count_quarantine(op._gtd_dir())) is False


# --- WatchdogDigestFresh -------------------------------------------------------


def test_digest_fresh_pure_rule():
    assert op._digest_fresh(None) is True                       # could not look -> safe
    assert op._digest_fresh(60.0) is True                        # just published -> fresh
    assert op._digest_fresh(op._DIGEST_MAX_AGE_S + 1) is False   # stale -> firing
    assert op._digest_fresh(float("inf")) is False               # never published -> firing


def test_digest_age_reads_real_publish_mtime(tmp_path, monkeypatch):
    """A real digest just published reads back as fresh: the file's own mtime
    IS the publish moment (publish_digest's atomic replace)."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(
        {"date": "2026-08-16", "headline": "quiet", "problems": [], "notable": [],
         "info_counts": {}, "per_source": {}},
        "# hello\n",
    )
    age = op._probe_digest_age()
    assert age is not None
    assert age < 5.0
    assert op._digest_fresh(age) is True


def test_digest_age_is_infinite_when_never_published(tmp_path, monkeypatch):
    """The 'I looked and it is stale' case: the watchdog home resolves fine,
    nothing has ever been published there. That is a real observation (the
    narrator has never spoken), not a probe failure, so it reads as an
    always-stale age, not as unknown."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    age = op._probe_digest_age()
    assert age == float("inf")
    assert op._digest_fresh(age) is False


def test_digest_age_unknown_when_probe_cannot_look(monkeypatch):
    """The 'I could not look' case: a real read failure (e.g. a permissions
    blip), not a missing file, must stay quiet -- never cry wolf."""
    class _Unreadable:
        def stat(self):
            raise PermissionError("simulated: no read access")

    monkeypatch.setattr(op, "_digest_path", lambda: _Unreadable())
    age = op._probe_digest_age()
    assert age is None
    assert op._digest_fresh(age) is True


def test_digest_age_unknown_when_path_resolution_fails(monkeypatch):
    """Even resolving WHERE to look can fail (an unresolvable watchdog home);
    that is also a probe failure, not an observation, so it stays quiet."""
    def _boom():
        raise RuntimeError("simulated: cannot resolve watchdog home")

    monkeypatch.setattr(op, "_digest_path", _boom)
    assert op._probe_digest_age() is None


# --- GradingBacklog -------------------------------------------------------------


def test_grading_not_backlogged_pure_rule():
    assert op._grading_not_backlogged(False) is True   # budget not exhausted -> healthy
    assert op._grading_not_backlogged(True) is False    # budget exhausted -> firing


def _grading_gap_digest(*, budget_exhausted: bool, skipped: int = 3):
    return {
        "date": "2026-08-16", "headline": "x", "problems": [],
        "notable": [{
            "ts": "2026-08-16T06:00:00Z", "source": "grading", "kind": "GradingGap",
            "object": "lumina-replies", "severity": "notable",
            "summary": f"{skipped} reply grade(s) skipped this run.",
            "link": {"uri": "", "http": ""}, "ref": "grading:gap:2026-08-16",
            "meta": {"skipped": skipped, "budget_exhausted": budget_exhausted},
        }],
        "info_counts": {}, "per_source": {},
    }


def test_grading_budget_exhausted_true_fires_backlog(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(_grading_gap_digest(budget_exhausted=True), "# md\n")
    assert op._probe_grading_budget_exhausted() is True
    assert op._grading_not_backlogged(op._probe_grading_budget_exhausted()) is False


def test_grading_gap_without_budget_exhausted_does_not_backlog(tmp_path, monkeypatch):
    """A skgateway outage or an unparseable reply is a grader-availability
    skip, not a backlog; GradingBacklog must not fire on it alone."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(_grading_gap_digest(budget_exhausted=False, skipped=1), "# md\n")
    assert op._probe_grading_budget_exhausted() is False


def test_grading_budget_exhausted_false_when_no_digest(tmp_path, monkeypatch):
    """No digest at all is not evidence of a backlog -- WatchdogDigestFresh's
    alarm to raise, not this one's."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    assert op._probe_grading_budget_exhausted() is False


def test_grading_budget_exhausted_false_when_digest_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import DIGEST_JSON_NAME, latest_dir

    (latest_dir() / DIGEST_JSON_NAME).write_text("{not valid json", encoding="utf-8")
    assert op._probe_grading_budget_exhausted() is False


# --- act ---------------------------------------------------------------------


def test_act_restart_service_via_runner():
    seen = {}

    def fake_runner(cmd):
        seen["cmd"] = cmd
        return {"ok": True, "returncode": 0}

    out = op.act("restart_service", runner=fake_runner)
    assert out["performed"] is True
    assert out["action"] == "restart_service"
    assert seen["cmd"] == ["systemctl", "--user", "restart", "skscheduler.service"]
    assert out["result"]["ok"] is True


def test_act_restart_service_unit_override():
    seen = {}
    op.act("restart_service", runner=lambda cmd: seen.setdefault("cmd", cmd) or {},
           unit="my-scheduler.service")
    assert seen["cmd"] == ["systemctl", "--user", "restart", "my-scheduler.service"]


def test_act_replay_errors_via_runner():
    seen = {}
    out = op.act("replay_errors", runner=lambda cmd: seen.setdefault("cmd", cmd) or {"ok": True})
    assert out["performed"] is True
    assert seen["cmd"] == ["skos", "gtd", "replay-errors"]


def test_act_unknown_action_refused():
    with pytest.raises(ValueError):
        op.act("nuke-everything", runner=lambda cmd: {})


def test_cli_act_unknown_action_exits_nonzero():
    r = runner.invoke(app, ["operator", "act", "bogus"])
    assert r.exit_code != 0
    assert "unknown skos operator action" in (r.output + str(r.exception or ""))


def test_cli_act_restart_service(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        op, "_default_runner",
        lambda cmd: seen.setdefault("cmd", cmd) or {"ok": True, "returncode": 0},
    )
    r = runner.invoke(app, ["operator", "act", "restart_service"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["performed"] is True
    assert seen["cmd"] == ["systemctl", "--user", "restart", "skscheduler.service"]


# --- replay actuation is a real, reversible gtd command ----------------------


def test_gtd_replay_errors_command_stages_backlog(tmp_path, monkeypatch):
    from skos import gtd_ingest

    gtd = tmp_path / "gtd"
    gtd.mkdir()
    corrupt = gtd / "next-actions.json.corrupt-20260101T000000Z"
    corrupt.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("SK_GTD_DIR", str(gtd))

    moved = gtd_ingest.replay_quarantine()
    assert moved == ["next-actions.json.corrupt-20260101T000000Z"]
    # reversible: the file is relocated, never deleted
    assert not corrupt.exists()
    staged = gtd / gtd_ingest.REPLAY_DIRNAME / corrupt.name
    assert staged.exists()
    # backlog is now clear -> the sink reads as draining again
    assert gtd_ingest.quarantine_backlog() == []


def test_cli_gtd_replay_errors_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    r = runner.invoke(app, ["gtd", "replay-errors"])
    assert r.exit_code == 0, r.output
    assert "clean" in r.output
