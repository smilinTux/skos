"""skos' READ-ONLY web surface: the manifest is served live and origin-relative,
the Grade B status pane renders, /health/status.json are read-only JSON, and NO
write routes exist (the pane never acts or writes)."""

import pytest

pytest.importorskip("fastapi", reason="optional [web] extra not installed")
pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient  # noqa: E402

from skos.skworld_manifest import SCHEMA_VERSION  # noqa: E402
from skos.webui import build_app, render_status_html, status_snapshot  # noqa: E402


@pytest.fixture
def client():
    return TestClient(build_app())


# --- manifest served live + origin-relative ----------------------------------


def test_manifest_served_at_well_known(client):
    r = client.get("/.well-known/skworld-module.json")
    assert r.status_code == 200
    m = r.json()
    assert m["id"] == "skos"
    assert m["schemaVersion"] == SCHEMA_VERSION
    assert m["grade"] == "B"
    assert m["operator"]["conditions"] == [
        "SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog",
    ]


def test_manifest_urls_are_origin_relative(client):
    """The served manifest's URLs resolve against the request origin, not a
    hardcoded host/port. TestClient's base is http://testserver."""
    m = client.get("/.well-known/skworld-module.json").json()
    assert m["entry"]["url"] == "http://testserver/"
    assert m["health"] == "http://testserver/health"


def test_manifest_reflects_a_different_host_header(client):
    """A different Host header yields a different (still origin-relative) origin,
    proving there is no baked-in host/port drift."""
    m = client.get(
        "/.well-known/skworld-module.json",
        headers={"Host": "skos.example:7781"},
    ).json()
    assert m["entry"]["url"] == "http://skos.example:7781/"
    assert m["health"] == "http://skos.example:7781/health"


# --- status pane renders ------------------------------------------------------


def test_status_page_renders_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "skos" in body
    assert "read-only status" in body
    # Read-only pane: it advertises that it never acts or writes.
    assert "no actions, no writes" in body


def test_app_alias_also_renders(client):
    assert client.get("/app").status_code == 200


def test_status_page_is_self_contained_no_external_assets(client):
    """CSP-safe: no external CDN/font/script references in the embedded pane."""
    body = client.get("/").text
    # No external resource loads at all:
    for needle in ("<script", "src=", "href=", "cdn", "googleapis", "unpkg"):
        assert needle not in body.lower(), f"unexpected external asset marker: {needle}"


def test_status_json_is_read_only_snapshot(client):
    r = client.get("/status.json")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "skos"
    assert set(["node", "scheduler", "gtd", "jobs"]).issubset(data.keys())


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "skos"


# --- no write routes ----------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/app", "/health", "/status.json",
                                  "/.well-known/skworld-module.json"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_write_methods(client, path, method):
    """Every route is GET-only: write verbs are refused (405), never handled."""
    r = getattr(client, method)(path)
    assert r.status_code == 405


def test_only_get_routes_are_registered():
    """Belt-and-suspenders: the app registers no route that accepts a write verb."""
    app = build_app()
    write_verbs = {"POST", "PUT", "PATCH", "DELETE"}
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        assert not (methods & write_verbs), f"{route.path} exposes {methods & write_verbs}"


# --- pure renderers fail safe -------------------------------------------------


def test_snapshot_and_render_never_raise():
    snap = status_snapshot()
    assert snap["service"] == "skos"
    # Renders even with an empty/degraded snapshot.
    assert "<html" in render_status_html({}).lower()
    assert "<html" in render_status_html(snap).lower()
