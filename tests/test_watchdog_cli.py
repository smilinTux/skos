"""`skos watchdog digest` CLI wiring: the by-hand command the WD-3 card adds.
No schedule is touched here -- WD-4 owns the 07:45 cutover; this only proves
the command itself runs end to end against the REAL Phase-1 adapters, fully
isolated from any real fleet/coordination/ITIL state on this machine via env
overrides, and against a fully isolated watchdog state root.
"""
from typer.testing import CliRunner

from skos.cli import app
from skos.watchdog.cursor import read_cursor
from skos.watchdog.publish import latest_dir

runner = CliRunner()


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "skcapstone"))
    monkeypatch.setenv("SKFLEET_ROOT", str(tmp_path / "skcapstone" / "fleet"))
    monkeypatch.delenv("SK_WATCHDOG_GIT_REPOS", raising=False)
    from skos.watchdog import headline as hl
    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    from skos.watchdog import deliver as dl
    monkeypatch.setattr(dl, "default_sender", lambda text: True)


def test_dry_run_prints_markdown_and_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    res = runner.invoke(app, ["watchdog", "digest", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "skwatchdog digest" in res.output
    assert "--dry-run" in res.output
    assert not latest_dir().joinpath("digest.json").exists()


def test_real_run_publishes_and_reports(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    res = runner.invoke(app, ["watchdog", "digest", "--date", "2026-08-10"])
    assert res.exit_code == 0, res.output
    assert "published:" in res.output
    assert latest_dir().joinpath("digest.json").exists()
    # every registered Phase-1 source has an advanced cursor after a landed digest
    assert read_cursor("fleet") is not None


def test_no_send_skips_dm(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from skos.watchdog import deliver as dl
    calls = []
    monkeypatch.setattr(dl, "default_sender", lambda text: calls.append(text) or True)
    res = runner.invoke(app, ["watchdog", "digest", "--no-send"])
    assert res.exit_code == 0, res.output
    assert "skipped" in res.output
    assert calls == []
