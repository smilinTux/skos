"""CLI wiring for `skos autopilot` (CliRunner, module fns mocked)."""
from typer.testing import CliRunner

from skos.cli import app

runner = CliRunner()


def test_answer_calls_resolver(mocker):
    m = mocker.patch("skos.autopilot.resolver.answer",
                     return_value={"n": 2, "qid": "q2", "answer": "yes", "answered": True})
    r = runner.invoke(app, ["autopilot", "answer", "2", "yes"])
    assert r.exit_code == 0
    m.assert_called_once_with(2, "yes")
    assert "q2" in r.stdout


def test_answer_without_response(mocker):
    m = mocker.patch("skos.autopilot.resolver.answer",
                     return_value={"n": 1, "qid": "q1", "answer": None, "answered": True})
    r = runner.invoke(app, ["autopilot", "answer", "1"])
    assert r.exit_code == 0
    m.assert_called_once_with(1, None)


def test_run_defaults_to_dry_run(mocker):
    m = mocker.patch("skos.autopilot.orchestrator.run_cli", return_value={"run_id": "r1"})
    r = runner.invoke(app, ["autopilot", "run"])
    assert r.exit_code == 0
    assert m.call_args.kwargs["dry_run"] is True


def test_run_no_dry_run(mocker):
    m = mocker.patch("skos.autopilot.orchestrator.run_cli", return_value={"disabled": "x"})
    r = runner.invoke(app, ["autopilot", "run", "--no-dry-run"])
    assert r.exit_code == 0
    assert m.call_args.kwargs["dry_run"] is False


def test_run_canary_with_task(mocker):
    m = mocker.patch("skos.autopilot.orchestrator.run_cli", return_value={"disabled": "x"})
    r = runner.invoke(app, ["autopilot", "run", "--canary", "--task", "014b0318"])
    assert r.exit_code == 0
    assert m.call_args.kwargs["canary"] is True
    assert m.call_args.kwargs["task"] == "014b0318"


def test_send_preview_does_not_send(mocker):
    mocker.patch("skos.autopilot.digest.rebuild_manifest", return_value={"items": []})
    mocker.patch("skos.autopilot.digest.build_digest_text", return_value="1. A")
    send = mocker.patch("skos.autopilot.digest.send_digest")
    r = runner.invoke(app, ["autopilot", "send", "--preview"])
    assert r.exit_code == 0 and "1. A" in r.stdout
    send.assert_not_called()


def test_stub_harness_assess_valid_run_raises():
    from skos.autopilot.harness import StubHarness
    from skos.autopilot.types import AssessBrief
    h = StubHarness()
    v = h.assess(AssessBrief(task_id="t", title="", description="", acceptance=[],
                             tags=[], repo=None, codebase_context=""))
    assert v.verdict == "valid"
    import pytest
    with pytest.raises(RuntimeError):
        h.run_task(None)
