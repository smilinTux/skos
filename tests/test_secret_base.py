import pytest
from skos.secrets import base


def test_secretbackend_is_skvault_capability():
    assert base.SecretBackend.capability == "skvault"


def test_subclass_must_implement(monkeypatch):
    class Incomplete(base.SecretBackend):
        name = "x"
    with pytest.raises(TypeError):
        Incomplete()  # abstract methods unimplemented
