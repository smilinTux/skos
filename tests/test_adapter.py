import pytest
from skos import adapter


class FakeGarage(adapter.Adapter):
    capability = "skobject"
    name = "garage"


def test_register_and_lookup():
    reg = adapter.AdapterRegistry()
    reg.register(FakeGarage)
    assert reg.lookup("skobject", "garage") is FakeGarage


def test_missing_lookup_raises():
    reg = adapter.AdapterRegistry()
    with pytest.raises(adapter.AdapterError):
        reg.lookup("skobject", "garage")


def test_adapter_requires_capability_and_name():
    class Bad(adapter.Adapter):
        pass
    reg = adapter.AdapterRegistry()
    with pytest.raises(adapter.AdapterError):
        reg.register(Bad)


def test_available_for():
    reg = adapter.AdapterRegistry()
    reg.register(FakeGarage)
    assert reg.available_for("skobject") == ["garage"]
