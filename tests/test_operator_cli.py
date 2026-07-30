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
    assert c["kinds"] == ["scheduler", "gtd"]
    assert c["conditions"] == ["SchedulerAlive", "GtdSinkDraining"]
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
    assert c["kinds"] == ["scheduler", "gtd"]
    assert c["conditions"] == ["SchedulerAlive", "GtdSinkDraining"]
    assert [a["name"] for a in c["actions"]] == ["restart_service", "replay_errors"]


def test_explain_byte_compatible_with_adapter():
    """explain() is the same shape/content as Atlas's skos_adapter.skos_explain."""
    skos_adapter = pytest.importorskip(
        "skcapstone.operator_seat.skos_adapter",
        reason="optional sibling skcapstone not installed",
    )
    assert op.explain() == skos_adapter.skos_explain()


# --- observe -----------------------------------------------------------------


def _observe_types(doc):
    return [(c["type"], c["status"], c["object"]) for c in doc["conditions"]]


def test_observe_healthy_via_injected_probe():
    doc = op.observe(lambda: {"scheduler_alive": True, "gtd_draining": True})
    assert _observe_types(doc) == [
        ("SchedulerAlive", "True", "skscheduler"),
        ("GtdSinkDraining", "True", "gtd-sink"),
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


def test_observe_shape_byte_compatible_with_adapter():
    skos_adapter = pytest.importorskip(
        "skcapstone.operator_seat.skos_adapter",
        reason="optional sibling skcapstone not installed",
    )
    healthy = {"scheduler_alive": True, "gtd_draining": True}
    assert op.observe(lambda: healthy) == skos_adapter.skos_observe(lambda: healthy)


def test_cli_observe_emits_conditions_json(monkeypatch):
    monkeypatch.setattr(
        op, "_default_probe",
        lambda: {"scheduler_alive": True, "gtd_draining": True},
    )
    r = runner.invoke(app, ["operator", "observe"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert {c["type"] for c in doc["conditions"]} == {"SchedulerAlive", "GtdSinkDraining"}


# --- default probe: real signals, fail safe ----------------------------------


def test_default_probe_fails_safe_when_unreachable(tmp_path, monkeypatch):
    """No ledger + empty GTD store -> healthy (fail safe), never a false alarm."""
    monkeypatch.setenv("SKOS_CRON_LEDGER", str(tmp_path / "nope.jsonl"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "empty-gtd"))
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
