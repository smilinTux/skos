import pytest
from skos import conformance, adapter
from skos.capability import Catalog


class GoodObj(adapter.Adapter):
    capability = "skobject"
    name = "garage"


class WrongCap(adapter.Adapter):
    capability = "not-a-capability"
    name = "x"


def test_conforms_when_capability_in_catalog_and_adapter_listed():
    conformance.assert_conforms(GoodObj, Catalog.load())  # garage is skobject.default → ok


def test_rejects_unknown_capability():
    with pytest.raises(conformance.ConformanceError):
        conformance.assert_conforms(WrongCap, Catalog.load())


def test_rejects_adapter_not_in_catalog_list():
    class Stray(adapter.Adapter):
        capability = "skobject"
        name = "totally-unlisted"
    with pytest.raises(conformance.ConformanceError):
        conformance.assert_conforms(Stray, Catalog.load())
