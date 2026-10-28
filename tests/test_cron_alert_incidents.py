"""sk-cron-run alerts once per failure INCIDENT, repeats on an interval, and recovers.

It used to call ``sk-alert -k cron-<job>`` with no ``-t``. sk-alert documents a
key without a ttl as "once EVER", so every job alerted on its first failure in
history and then never again: nor's lumina sync last alerted 2026-09-19 while
failing every 3h for weeks afterwards. The incident is now owned by sk-cron-run:

* first failure of a streak alerts
* a continuing streak re-alerts at most every ``SK_CRON_ALERT_REPEAT_S``
* the first success after a streak sends exactly one recovery notice
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CRON_SH = REPO / "scripts" / "sk-cron-run.sh"
FAIL = ["/bin/sh", "-c", "echo boom >&2; exit 3"]
OK = ["/bin/sh", "-c", "echo fine"]


@pytest.fixture
def rig(tmp_path):
    home = tmp_path / "home"
    skenv = home / ".skenv" / "bin"
    skenv.mkdir(parents=True)
    receipt = tmp_path / "alerts.txt"
    rcfile = tmp_path / "alert-rc"
    rcfile.write_text("0")
    alert = skenv / "sk-alert"
    # Records every call; exits with whatever rcfile says, to model a failed send.
    alert.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{receipt}"\n'
        f'exit "$(cat "{rcfile}")"\n',
        encoding="utf-8",
    )
    alert.chmod(0o755)
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        SK_GTD_DIR=str(tmp_path / "gtd"),
        SKOS_BIN="/nonexistent/skos",
        PATH="/usr/bin:/bin",
        PY="/usr/bin/python3",
        SK_CRON_STATE_DIR=str(tmp_path / "state"),
        SK_CRON_HOST="testhost",
    )

    def run(cmd, job="flaky", **extra):
        e = dict(env, **extra)
        return subprocess.run(
            [str(CRON_SH), job, *cmd], env=e, capture_output=True, text=True, timeout=60
        ).returncode

    def alerts():
        return receipt.read_text().splitlines() if receipt.exists() else []

    return run, alerts, rcfile, tmp_path / "state"


def test_streak_alerts_once_within_the_repeat_window(rig):
    run, alerts, _, _ = rig
    for _ in range(4):
        assert run(FAIL) == 3
    got = alerts()
    assert len(got) == 1, got
    assert "cron FAILED on testhost: flaky (exit 3)" in got[0]
    assert "boom" in got[0] and "crit" in got[0]


def test_streak_re_alerts_once_the_interval_has_passed(rig):
    run, alerts, _, _ = rig
    run(FAIL, SK_CRON_ALERT_REPEAT_S="0")
    run(FAIL, SK_CRON_ALERT_REPEAT_S="0")
    got = alerts()
    assert len(got) == 2
    assert "STILL FAILING" in got[1] and "2 failed runs since" in got[1]


def test_recovery_is_announced_once_and_closes_the_incident(rig):
    run, alerts, _, state = rig
    run(FAIL)
    run(FAIL)
    assert run(OK) == 0
    assert run(OK) == 0
    got = alerts()
    assert len(got) == 2, got
    assert "cron RECOVERED on testhost: flaky ok again after 2 failed run(s)" in got[1]
    assert "info" in got[1]
    assert not list(state.glob("*.incident"))
    # A new failure after recovery is a NEW incident and alerts immediately.
    run(FAIL)
    assert len(alerts()) == 3 and "cron FAILED" in alerts()[2]


def test_success_without_an_incident_is_silent(rig):
    run, alerts, _, _ = rig
    assert run(OK) == 0
    assert alerts() == []


def test_undelivered_alert_does_not_start_the_quiet_window(rig):
    run, alerts, rcfile, _ = rig
    rcfile.write_text("1")  # telegram down
    run(FAIL)
    rcfile.write_text("0")
    run(FAIL)  # still inside 6h of the first attempt, but nothing was delivered
    got = alerts()
    assert len(got) == 2 and "STILL FAILING" in got[1]
    run(FAIL)
    assert len(alerts()) == 2  # now delivered, so the window holds


def test_jobs_have_independent_incidents(rig):
    run, alerts, _, _ = rig
    run(FAIL, job="a")
    run(FAIL, job="b")
    run(FAIL, job="a")
    assert [l.split(": ")[1].split(" ")[0] for l in alerts()] == ["a", "b"]


def test_legacy_once_ever_key_is_not_passed(rig):
    run, alerts, _, _ = rig
    run(FAIL)
    assert "-k" not in alerts()[0].split()
