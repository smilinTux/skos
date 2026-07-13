import json
import subprocess
import pytest
from skos.autopilot import ci
from skos.autopilot.types import RepoSpec


def _gha_repo(**kw):
    ci_poll_timeout = kw.pop('ci_poll_timeout', 1200)
    return RepoSpec(name="skrender", path="/repos/skrender", base_branch="main",
                    integration_branch="develop", test_cmd="pytest",
                    ci="github-actions", ci_poll_timeout=ci_poll_timeout, **kw)


def _runs_json(*runs):
    return subprocess.CompletedProcess(args=[], returncode=0,
                                       stdout=json.dumps(list(runs)), stderr="")


def test_gha_success_is_green_and_builds_expected_argv(mocker):
    repo = _gha_repo()
    run = mocker.patch("skos.autopilot.ci.subprocess.run", return_value=_runs_json(
        {"headSha": "abc123", "databaseId": 9, "status": "completed", "conclusion": "success"}))
    mocker.patch("skos.autopilot.ci.time.sleep")
    assert ci.external_ci_verdict(repo, "autopilot/t1", "abc123") == "green"
    argv = run.call_args.args[0]
    assert argv == ["gh", "run", "list", "--branch", "autopilot/t1",
                    "--json", "headSha,databaseId,status,conclusion"]
    assert run.call_args.kwargs["cwd"] == "/repos/skrender"


@pytest.mark.parametrize("concl", ["failure", "cancelled", "timed_out"])
def test_gha_failure_conclusions_are_red(mocker, concl):
    mocker.patch("skos.autopilot.ci.time.sleep")
    mocker.patch("skos.autopilot.ci.subprocess.run", return_value=_runs_json(
        {"headSha": "abc123", "status": "completed", "conclusion": concl}))
    assert ci.external_ci_verdict(_gha_repo(), "b", "abc123") == "red"


def test_gha_unknown_conclusion_never_green(mocker):
    mocker.patch("skos.autopilot.ci.time.sleep")
    mocker.patch("skos.autopilot.ci.subprocess.run", return_value=_runs_json(
        {"headSha": "abc123", "status": "completed", "conclusion": "neutral"}))
    assert ci.external_ci_verdict(_gha_repo(), "b", "abc123") == "red"


def test_gha_poll_timeout_is_red_not_green(mocker):
    mocker.patch("skos.autopilot.ci.time.sleep")
    # sha never appears -> stays pending; monotonic jumps past the deadline
    mocker.patch("skos.autopilot.ci.subprocess.run", return_value=_runs_json())
    mocker.patch("skos.autopilot.ci.time.monotonic", side_effect=[0.0, 0.0, 9999.0])
    assert ci.external_ci_verdict(_gha_repo(ci_poll_timeout=1), "b", "abc123") == "red"


def test_ci_none_returns_sentinel(mocker):
    run = mocker.patch("skos.autopilot.ci.subprocess.run")
    repo = RepoSpec(name="x", path="/x", base_branch="main", integration_branch="develop",
                    test_cmd="pytest", ci="none")
    assert ci.external_ci_verdict(repo, "b", "sha") == "none"
    run.assert_not_called()


def _local_repo(cmd):
    return RepoSpec(name="skr", path="/repos/skr", base_branch="main",
                    integration_branch="develop", test_cmd="pytest", ci=f"local:{cmd}")


def test_local_exit0_is_green_runs_cmd_in_repo(mocker):
    run = mocker.patch("skos.autopilot.ci.subprocess.run",
                       return_value=subprocess.CompletedProcess(args=[], returncode=0))
    assert ci.external_ci_verdict(_local_repo("make ci"), "b", "sha") == "green"
    assert run.call_args.args[0] == "make ci"
    assert run.call_args.kwargs["shell"] is True
    assert run.call_args.kwargs["cwd"] == "/repos/skr"


def test_local_nonzero_is_red(mocker):
    mocker.patch("skos.autopilot.ci.subprocess.run",
                 return_value=subprocess.CompletedProcess(args=[], returncode=1))
    assert ci.external_ci_verdict(_local_repo("make ci"), "b", "sha") == "red"


_COBERTURA = """<?xml version="1.0" ?>
<coverage><packages><package><classes>
<class filename="src/skr/foo.py"><lines>
<line number="10" hits="1"/><line number="11" hits="0"/>
<line number="12" hits="1"/><line number="99" hits="0"/>
</lines></class></classes></package></packages></coverage>"""

_DIFF = """diff --git a/src/skr/foo.py b/src/skr/foo.py
--- a/src/skr/foo.py
+++ b/src/skr/foo.py
@@ -9,3 +10,3 @@ def foo():
+    added_a = 1
+    added_b = 2
+    added_c = 3
"""


def _cov_repo(tmp, cmd="pytest --cov --cov-report=xml", **kw):
    return RepoSpec(name="skr", path=str(tmp), base_branch="main",
                    integration_branch="develop", test_cmd="pytest",
                    ci="none", coverage_cmd=cmd, **kw)


def test_diff_coverage_ratio_over_changed_lines_only(mocker, tmp_path):
    (tmp_path / "coverage.xml").write_text(_COBERTURA, encoding="utf-8")
    run = mocker.patch("skos.autopilot.ci.subprocess.run",
                       return_value=subprocess.CompletedProcess(args=[], returncode=0))
    repo = _cov_repo(tmp_path, min_diff_coverage=0.8)
    # changed lines 10,11,12 -> covered {10,12}, missed {11}; line 99 not in the diff
    ratio = ci.diff_coverage(repo, str(tmp_path), _DIFF)
    assert ratio == pytest.approx(2 / 3)
    assert ratio < repo.min_diff_coverage        # threshold compare the caller makes
    assert run.call_args.kwargs["cwd"] == str(tmp_path)


def test_diff_coverage_none_when_no_coverage_cmd(mocker, tmp_path):
    run = mocker.patch("skos.autopilot.ci.subprocess.run")
    repo = _cov_repo(tmp_path)
    repo.coverage_cmd = None
    assert ci.diff_coverage(repo, str(tmp_path), _DIFF) is None
    run.assert_not_called()
