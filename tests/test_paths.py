import pytest
from skos import paths


def test_data_root_from_env(data_root):
    assert paths.data_root() == data_root.resolve()


def test_data_root_missing_raises(monkeypatch):
    # cloud profile has no default data root; without SK_DATA_ROOT it must raise
    monkeypatch.delenv("SK_DATA_ROOT", raising=False)
    monkeypatch.setenv("SKOS_PROFILE", "cloud")
    with pytest.raises(paths.DataRootError):
        paths.data_root()


def test_subdir_known(data_root):
    assert paths.subdir("apps") == (data_root.resolve() / "apps")


def test_subdir_unknown_raises(data_root):
    with pytest.raises(paths.DataRootError):
        paths.subdir("nonsense")


def test_ensure_tree_creates_all(data_root):
    paths.ensure_tree()
    for name in paths.TREE:
        assert (data_root.resolve() / name).is_dir()
