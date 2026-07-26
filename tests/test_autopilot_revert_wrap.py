from unittest.mock import MagicMock
from skos.autopilot import engineering

# engineering.revert() is defined in skharness.autocode.engineering and calls
# _revert_impl/_load_board/_load_config as its OWN module globals, so the
# patches must target that implementation module, not the skos.autopilot shim
# (which only holds re-exported copies of the same names).
_IMPL = "skharness.autocode.engineering"


def test_revert_one_arg_loads_board_and_config(monkeypatch):
    impl = MagicMock(return_value={"reverted": "sha1"})
    monkeypatch.setattr(f"{_IMPL}._revert_impl", impl)
    monkeypatch.setattr(f"{_IMPL}._load_board", lambda: "BOARD")
    monkeypatch.setattr(f"{_IMPL}._load_config", lambda: "CFG")
    out = engineering.revert("t1")
    impl.assert_called_once_with("BOARD", "CFG", "t1", "autopilot")
    assert out == {"reverted": "sha1"}
