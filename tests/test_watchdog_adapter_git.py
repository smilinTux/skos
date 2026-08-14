"""git adapter: git log merges + gh pr list (injected) across configured
repos -> WatchdogEvent.

Every git-log test runs against a REAL throwaway repo this test creates
with `git init` in tmp_path (never a live checkout). `gh pr list` is always
exercised through an injected `pr_lister` callable, so no test ever invokes
the real `gh` CLI or network, regardless of whether `gh` happens to be
installed on the box running the suite.
"""
import subprocess

import pytest

from skos.watchdog.adapters.git import GitAdapter
from skos.watchdog.port import Window, collect_safe


def _window(since="2020-01-01T00:00:00Z", until="2030-01-01T00:00:00Z"):
    return Window(since=since, until=until)


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True,
                    capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "a.txt").write_text("hi\n", encoding="utf-8")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture(autouse=True)
def no_repos_by_default(monkeypatch):
    monkeypatch.delenv("SK_WATCHDOG_GIT_REPOS", raising=False)


def _adapter(pr_lister=None):
    return GitAdapter(pr_lister=pr_lister if pr_lister is not None else (lambda name, path: []))


def test_no_repos_configured_is_quiet_not_unavailable():
    assert _adapter().collect(_window()) == []


def test_merge_to_main_is_info_with_a_link(repo, monkeypatch):
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "a.txt").write_text("hi\nbye\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "feature work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature into main", "feature")
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"testrepo={repo}")

    events = _adapter().collect(_window())
    merges = [e for e in events if e.kind == "GitMergeToMain"]
    assert len(merges) == 1
    ev = merges[0]
    assert ev.severity == "info"
    assert "testrepo" in ev.object
    assert "Merge feature into main" in ev.summary
    assert ev.link.uri.startswith("skworld://skos/watchdog/git/testrepo/commit/")


def test_non_merge_commit_on_main_is_not_narrated(repo, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"testrepo={repo}")
    events = _adapter().collect(_window())
    assert not any(e.kind == "GitMergeToMain" for e in events)


def test_merge_outside_window_is_excluded(repo, monkeypatch):
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "a.txt").write_text("hi\nbye\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "feature work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature into main", "feature")
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"testrepo={repo}")

    events = _adapter().collect(_window(since="2020-01-01T00:00:00Z", until="2020-01-02T00:00:00Z"))
    assert not any(e.kind == "GitMergeToMain" for e in events)


def test_aging_pr_is_notable(repo, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"testrepo={repo}")
    prs = [{"number": 42, "title": "old PR", "url": "https://github.com/x/y/pull/42",
            "createdAt": "2020-01-01T00:00:00Z", "statusCheckRollup": []}]
    events = _adapter(pr_lister=lambda name, path: prs).collect(_window())
    aging = [e for e in events if e.kind == "AgingPR"]
    assert len(aging) == 1
    assert aging[0].severity == "notable"
    assert aging[0].object == "testrepo#42"


def test_failing_ci_check_is_a_problem(repo, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"testrepo={repo}")
    prs = [{"number": 7, "title": "flaky PR", "url": "https://github.com/x/y/pull/7",
            "createdAt": "2030-01-01T00:00:00Z",
            "statusCheckRollup": [{"conclusion": "FAILURE"}, {"conclusion": "SUCCESS"}]}]
    events = _adapter(pr_lister=lambda name, path: prs).collect(_window())
    failures = [e for e in events if e.kind == "CIFailure"]
    assert len(failures) == 1
    assert failures[0].severity == "problem"
    assert failures[0].object == "testrepo#7"


def test_repo_path_not_a_git_checkout_degrades_inline(tmp_path, monkeypatch):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"broken={not_a_repo}")
    events = _adapter().collect(_window())
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"
    assert events[0].source == "git:broken"


def test_one_bad_repo_does_not_blank_a_good_one(tmp_path, repo, monkeypatch):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"broken={not_a_repo},good={repo}")
    events = _adapter().collect(_window())
    kinds_by_object = {e.object: e.kind for e in events}
    assert kinds_by_object.get("git:broken") == "SourceUnavailable"


def test_gh_pr_list_failure_degrades_inline_not_the_whole_repo(repo, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"testrepo={repo}")

    def boom(name, path):
        raise RuntimeError("gh: authentication required")

    events = _adapter(pr_lister=boom).collect(_window())
    unavailable = [e for e in events if e.kind == "SourceUnavailable"]
    assert len(unavailable) == 1
    assert unavailable[0].source == "git:testrepo:pr"


def test_degrades_to_source_unavailable_when_git_log_itself_fails(repo, monkeypatch):
    monkeypatch.setenv("SK_WATCHDOG_GIT_REPOS", f"testrepo={repo}")

    def broken_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="fatal: broken")

    adapter = GitAdapter(runner=broken_runner, pr_lister=lambda name, path: [])
    events = collect_safe(adapter, _window())
    assert any(e.kind == "SourceUnavailable" for e in events)
