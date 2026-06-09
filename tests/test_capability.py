import pytest
from skos import capability


def test_catalog_loads_all_ports():
    cat = capability.Catalog.load()
    assert len(cat.all()) >= 24
    assert {c.group for c in cat.all()} == {"cloud", "comms", "compute", "core"}


def test_get_by_name():
    c = capability.Catalog.load().get("skobject")
    assert c.default == "garage" and "seaweedfs" in c.alternates


def test_by_group():
    core = capability.Catalog.load().by_group("core")
    names = {c.name for c in core}
    assert {"capauth", "sksso", "sksec", "skwaf", "skca", "skvault"} <= names


def test_unknown_raises():
    with pytest.raises(capability.CapabilityError):
        capability.Catalog.load().get("nope")
