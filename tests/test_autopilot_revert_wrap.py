from unittest.mock import MagicMock
from skos.autopilot import engineering


def test_revert_one_arg_loads_board_and_config(monkeypatch):
    impl = MagicMock(return_value={"reverted": "sha1"})
    monkeypatch.setattr(engineering, "_revert_impl", impl)
    monkeypatch.setattr(engineering, "_load_board", lambda: "BOARD")
    monkeypatch.setattr(engineering, "_load_config", lambda: "CFG")
    out = engineering.revert("t1")
    impl.assert_called_once_with("BOARD", "CFG", "t1", "autopilot")
    assert out == {"reverted": "sha1"}
