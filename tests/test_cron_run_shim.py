"""``scripts/sk-cron-run.sh`` is a shim, and must behave like one.

The wrapper moved to skcapstone on 2026-11-06 and installs as
``~/.skenv/bin/sk-cron-run``. This file stayed behind because 31 systemd
drop-ins and the managed crontab block named it by absolute path, and an
ExecStart pointing at an absent binary does not degrade quietly: the unit
fails at activation. The shim keeps all of them working with no node change.

Two properties make it safe to leave in place:

* it is transparent, so a wrapped job cannot tell the difference, and
* when it cannot resolve the real wrapper it says so and returns 78
  (EX_CONFIG), never the wrapped job's exit code. Returning 0 there would
  report a job that never ran as a job that succeeded, which is exactly the
  silence sk-cron-run exists to end.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SHIM = REPO / "scripts" / "sk-cron-run.sh"


def _fake_wrapper(tmp_path: Path) -> Path:
    """A stand-in for the installed sk-cron-run that records its arguments."""
    binary = tmp_path / "fake-sk-cron-run"
    receipt = tmp_path / "argv.txt"
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" > "{receipt}"\n'
        'shift\n'
        'exec "$@"\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


def _run(tmp_path, *args, **env_extra):
    env = dict(os.environ, HOME=str(tmp_path / "home"), **env_extra)
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        [str(SHIM), *args], env=env, capture_output=True, text=True, timeout=60
    )


def test_the_shim_holds_no_logic_of_its_own():
    """One implementation of the wrapper, not two that drift apart.

    Comments are stripped first: the shim's header explains the move and names
    the things it must not do, which is documentation, not behaviour.
    """
    code = "\n".join(
        line for line in SHIM.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    for owned_by_the_real_wrapper in ("cron-ledger", "gtd capture", "sk-alert", "incident"):
        assert owned_by_the_real_wrapper not in code, owned_by_the_real_wrapper


def test_it_delegates_and_preserves_the_wrapped_exit_code(tmp_path):
    binary = _fake_wrapper(tmp_path)
    proc = _run(tmp_path, "shim-job", "/bin/sh", "-c", "echo ran; exit 5",
                SK_CRON_RUN_BIN=str(binary))
    assert proc.returncode == 5, proc.stderr
    assert "ran" in proc.stdout
    assert (tmp_path / "argv.txt").read_text().startswith("shim-job /bin/sh")


def test_it_finds_the_installed_command_under_home(tmp_path):
    """The resolution a systemd unit and cron actually get: no PATH help."""
    skenv = tmp_path / "home" / ".skenv" / "bin"
    skenv.mkdir(parents=True)
    installed = skenv / "sk-cron-run"
    installed.write_text('#!/bin/sh\nshift\nexec "$@"\n', encoding="utf-8")
    installed.chmod(0o755)
    proc = _run(tmp_path, "home-job", "/bin/sh", "-c", "exit 4", PATH="/usr/bin:/bin")
    assert proc.returncode == 4, proc.stderr


def test_an_unresolvable_wrapper_fails_loudly_and_never_reports_success(tmp_path):
    proc = _run(tmp_path, "orphan-job", "/bin/true", PATH="/usr/bin:/bin")
    assert proc.returncode == 78, proc.stdout + proc.stderr
    assert "sk-cron-run is not installed" in proc.stderr
    assert "orphan-job" in proc.stderr
    assert "skcapstone" in proc.stderr
