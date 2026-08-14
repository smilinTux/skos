"""sk-cron-run must reach sk-alert from a SCHEDULER's environment, not a shell's.

Card ``95a3b69e``. The failure branch guards the alert with
``if command -v sk-alert``, and ``sk-alert`` lives in ``~/.skenv/bin``, which is
on an interactive shell's PATH and on neither of the two environments that
actually run scheduled jobs:

* a systemd user unit (probed on noroc2027: NOT_REACHABLE), and
* cron, whose PATH is ``/usr/bin:/bin`` and whose crontab sets no PATH.

So the guard silently evaluated false and the realtime alert has never fired
for any scheduled job. The GTD capture survived only because it goes through
``$SKOS_BIN``, which is resolved as an absolute path.

That is the same failure as the 2026-08-13 watchdog incident, where the cron
PATH lacked ``/usr/sbin`` so ``qm`` exited 127 while ``kill -9``, a shell
builtin, worked fine. A PATH assumption that holds in your terminal is not a
PATH assumption that holds in the scheduler.

`sk-alert` is now resolved the same way `PY` and `SKOS_BIN` already are:
prefer the venv's absolute path, fall back to PATH.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CRON_SH = REPO / "scripts" / "sk-cron-run.sh"


@pytest.fixture
def scheduler_env(tmp_path, monkeypatch):
    """A HOME whose .skenv/bin holds a recording sk-alert, and a bare PATH."""
    home = tmp_path / "home"
    skenv = home / ".skenv" / "bin"
    skenv.mkdir(parents=True)
    receipt = tmp_path / "alert-receipt.txt"

    # Records ARGUMENTS only, deliberately: the real sk-alert takes its message
    # as an argument and rejects an empty one ("sk_alert: empty message"), so a
    # stub that also drained stdin would happily pass a wrapper that pipes.
    alert = skenv / "sk-alert"
    alert.write_text("#!/bin/sh\n" f'printf "%s\\n" "$*" >> "{receipt}"\n', encoding="utf-8")
    alert.chmod(0o755)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["SK_GTD_DIR"] = str(tmp_path / "gtd")
    env["SKOS_BIN"] = "/nonexistent/skos"  # force the library fallback, not the CLI
    # The scheduler's PATH: no ~/.skenv/bin anywhere in it.
    env["PATH"] = "/usr/bin:/bin"
    env["PY"] = "/usr/bin/python3"
    return env, receipt


def test_alert_fires_on_failure_from_a_bare_scheduler_path(scheduler_env):
    """Fail-before: the guard `command -v sk-alert` was false, so nothing fired."""
    env, receipt = scheduler_env
    proc = subprocess.run(
        [str(CRON_SH), "canary-job", "/bin/sh", "-c", "echo boom >&2; exit 3"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 3, proc.stderr  # the wrapper preserves the exit code
    assert receipt.exists(), "sk-alert was never invoked from a bare scheduler PATH"
    body = receipt.read_text(encoding="utf-8")
    assert "canary-job" in body
    assert "crit" in body  # -l crit
    # The message must arrive as an ARGUMENT. sk-alert does not read stdin: it
    # exits 2 with "sk_alert: empty message", which `|| true` then swallowed,
    # so a piped message was an alert that looked sent and never was.
    assert "cron FAILED" in body, "the message was not passed as an argument"
    assert "boom" in body, "the failing job's output never reached the alert"


def test_no_alert_on_success(scheduler_env):
    env, receipt = scheduler_env
    proc = subprocess.run(
        [str(CRON_SH), "happy-job", "/bin/sh", "-c", "echo fine"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert not receipt.exists(), "a successful job must not alert"


def test_a_missing_sk_alert_does_not_break_the_wrapper(tmp_path):
    """No alerter installed is not a reason to change the job's exit code."""
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "empty-home")
    env["SK_GTD_DIR"] = str(tmp_path / "gtd")
    env["SKOS_BIN"] = "/nonexistent/skos"
    env["PATH"] = "/usr/bin:/bin"
    env["PY"] = "/usr/bin/python3"
    (tmp_path / "empty-home").mkdir()

    proc = subprocess.run(
        [str(CRON_SH), "no-alerter", "/bin/sh", "-c", "exit 7"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 7
