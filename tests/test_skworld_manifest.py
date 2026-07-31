"""skos' SKWorld module manifest: shape, origin-relative URLs, operator facet."""

from skos.skworld_manifest import AUDIENCE, SCHEMA_VERSION, skos_module_manifest


def test_manifest_ui_facet_shape():
    m = skos_module_manifest("http://100.108.59.57:7780/")
    assert m["schemaVersion"] == SCHEMA_VERSION
    assert m["id"] == "skos"
    assert m["name"] == "OS"
    assert m["grade"] == "B"
    # nav.order 10 puts skos first, ahead of chats (20) and code (30).
    assert m["nav"] == {"icon": "grid_view", "order": 10, "label": "OS"}
    assert m["deeplinkPrefix"] == "skworld://skos/"
    assert m["memory"] == {"opt_in": True, "scope": "skos"}


def test_urls_are_origin_relative_and_not_double_slashed():
    m = skos_module_manifest("http://host:7780/")
    assert m["entry"]["url"] == "http://host:7780/"
    assert m["health"] == "http://host:7780/health"
    # A base without a trailing slash yields the same (no missing/extra slash).
    m2 = skos_module_manifest("http://host:7780")
    assert m2["entry"]["url"] == "http://host:7780/"
    assert m2["health"] == "http://host:7780/health"


def test_auth_facet_declares_audience_and_scopes():
    m = skos_module_manifest("http://host/")
    # The audience + scopes match capauth AUDIENCE_SCOPES["skos"] (provisional).
    assert m["auth"]["audience"] == AUDIENCE == "skos"
    assert m["auth"]["scopes"] == ["skos.read"]


def test_operator_facet_matches_the_skos_adapter_contract():
    op = skos_module_manifest("http://host/")["operator"]
    assert op["contractVersion"] == 1
    assert op["cli"] == "skos operator"
    assert op["repos"] == ["skos"]
    # Mirrors operator_seat/skos_adapter.py CONDITIONS and its standard actions.
    assert op["conditions"] == [
        "SchedulerAlive",
        "GtdSinkDraining",
    ]
    assert op["proposedStandardActions"] == ["restart_service", "replay_errors"]
