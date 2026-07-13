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
