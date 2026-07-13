"""The autopilot capability line loads from the catalog."""
from skos import capability


def test_autopilot_capability_loads():
    capability.Catalog.load.cache_clear()
    c = capability.Catalog.load().get("autopilot")
    assert c.group == "core"
    assert c.default == "skos-autopilot"


def test_autopilot_in_core_group():
    capability.Catalog.load.cache_clear()
    names = {c.name for c in capability.Catalog.load().by_group("core")}
    assert "autopilot" in names
