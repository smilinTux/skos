from skos import registry


def test_record_and_list(data_root):
    registry.record("capauth", adapter="oci", ref="img:1")
    items = registry.list_installed()
    assert items["capauth"]["adapter"] == "oci"
    assert items["capauth"]["ref"] == "img:1"


def test_remove(data_root):
    registry.record("x", adapter="oci", ref="img:1")
    registry.forget("x")
    assert "x" not in registry.list_installed()
