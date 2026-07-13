import types as _t
import pytest
from skos.autopilot.engineering import EngineeringExecutor
from skos.autopilot.types import WorkItem, RepoSpec


def _spec(name):
    return RepoSpec(name=name, path=f"/repos/{name}", base_branch="main",
                    integration_branch="develop", test_cmd="pytest", ci="none")


@pytest.fixture
def cfg():
    return _t.SimpleNamespace(repo_map={"skrender": _spec("skrender")},
                              automerge_repos=[])


def _item(tags, **payload):
    payload.setdefault("tags", tags)
    return WorkItem(kind="engineering", ref="t1", source="coord", repo=None, payload=payload)


def test_kind_is_engineering(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.kind == "engineering"


def test_resolves_single_known_repo_tag(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    spec = ex.resolve_repo(_item(["repo:skrender", "backend"]))
    assert spec is not None and spec.name == "skrender" and spec.path == "/repos/skrender"


def test_unknown_repo_tag_resolves_none(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.resolve_repo(_item(["repo:nope"])) is None


def test_two_repo_tags_resolves_none(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.resolve_repo(_item(["repo:skrender", "repo:other"])) is None


def test_no_repo_tag_resolves_none(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.resolve_repo(_item(["backend"])) is None


def _sel_item(**over):
    p = dict(unblocked=True, verdict="valid", tags=["repo:skrender"],
             acceptance=["does X"])
    p.update(over)
    return WorkItem(kind="engineering", ref="t1", source="coord", repo=None, payload=p)


def test_selectable_happy_path(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.selectable(_sel_item()) is True


def test_not_selectable_when_blocked(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.selectable(_sel_item(unblocked=False)) is False


def test_not_selectable_when_not_valid(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.selectable(_sel_item(verdict="stale")) is False


def test_not_selectable_unknown_repo(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.selectable(_sel_item(tags=["repo:nope"])) is False


def test_not_selectable_untriaged(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.selectable(_sel_item(tags=["repo:skrender", "autopilot-untriaged"])) is False


def test_not_selectable_when_not_code_shaped(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.selectable(_sel_item(acceptance=[], deliverable="")) is False


def test_selectable_via_deliverable_without_acceptance(cfg):
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    assert ex.selectable(_sel_item(acceptance=[], deliverable="ship the reloader")) is True


def test_claim_calls_board_then_journal(mocker, cfg):
    board = mocker.Mock()
    journal = mocker.Mock()
    manager = mocker.Mock()
    manager.attach_mock(board.claim_task, "claim")
    manager.attach_mock(journal.record_claim, "record")
    ex = EngineeringExecutor(cfg, board=board, journal=journal)
    item = WorkItem(kind="engineering", ref="t1", source="coord", repo=None,
                    payload={"tags": ["repo:skrender"]})
    ex.claim(item)
    board.claim_task.assert_called_once_with("autopilot", "t1")
    assert journal.record_claim.call_args.kwargs.get("claimed_at") or \
           journal.record_claim.call_args.args
    assert [c[0] for c in manager.mock_calls] == ["claim", "record"]


def test_make_worktree_git_argv(mocker, cfg):
    run = mocker.patch("skos.autopilot.engineering.subprocess.run",
                       return_value=mocker.Mock(returncode=0, stdout="", stderr=""))
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    item = WorkItem(kind="engineering", ref="t1", source="coord", repo=None, payload={})
    spec = cfg.repo_map["skrender"]
    wt = ex.make_worktree(item, spec)
    argv = run.call_args_list[0].args[0]
    assert argv[:6] == ["git", "-C", "/repos/skrender", "worktree", "add", "-b"]
    assert argv[6] == "autopilot/t1"          # new branch name
    assert argv[7] == wt                       # worktree path
    assert argv[8] == "main"                   # base_branch checkout point


def test_prune_worktree_git_argv(mocker, cfg):
    run = mocker.patch("skos.autopilot.engineering.subprocess.run",
                       return_value=mocker.Mock(returncode=0, stdout="", stderr=""))
    ex = EngineeringExecutor(cfg, board=object(), journal=object())
    ex.prune_worktree(cfg.repo_map["skrender"], "/repos/skrender-wt/t1")
    calls = [c.args[0] for c in run.call_args_list]
    assert ["git", "-C", "/repos/skrender", "worktree", "remove", "--force",
            "/repos/skrender-wt/t1"] in calls
    assert ["git", "-C", "/repos/skrender", "worktree", "prune"] in calls


def test_parse_promise_extracts_signal():
    from skos.autopilot.engineering import parse_promise
    assert parse_promise("done here <promise>COMPLETE</promise>") == "COMPLETE"


def test_parse_promise_none_when_absent():
    from skos.autopilot.engineering import parse_promise
    assert parse_promise("still working, not COMPLETE yet") is None


def test_is_complete_requires_the_tag_not_prose():
    from skos.autopilot.engineering import is_complete
    assert is_complete("<promise>COMPLETE</promise>") is True
    assert is_complete("not COMPLETE yet") is False          # false-positive resistance
    assert is_complete("<promise>WORKING</promise>") is False  # wrong signal


def test_strip_promise_removes_tag_and_trims():
    from skos.autopilot.engineering import strip_promise
    assert strip_promise("great work <promise>COMPLETE</promise>") == "great work"
