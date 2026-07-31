"""skos' SKWorld module manifest: shape, origin-relative URLs, operator facet,
and the static-file emitter the umbrella shell registry reads (spec 5.3)."""

import json

from typer.testing import CliRunner

from skos.cli import app
from skos.skworld_manifest import (
    AUDIENCE,
    DEFAULT_BASE_URL,
    SCHEMA_VERSION,
    default_manifest_path,
    emit_manifest_file,
    render_manifest_json,
    skos_module_manifest,
)

runner = CliRunner()


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


# --- static-file emitter (spec 5.3 local-file registry location) ---------------


def test_render_manifest_json_is_deterministic_and_round_trips():
    a = render_manifest_json("http://host:7780/")
    b = render_manifest_json("http://host:7780/")
    # Byte-stable so the shell's capauth signature is reproducible.
    assert a == b
    assert a.endswith("\n")
    # Sorted keys: stable ordering regardless of dict insertion order.
    assert json.loads(a) == skos_module_manifest("http://host:7780/")
    top = list(json.loads(a).keys())
    assert top == sorted(top)


def test_default_manifest_path_lives_under_skcapstone_shell(monkeypatch, tmp_path):
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path))
    p = default_manifest_path()
    assert p == tmp_path / "shell" / "modules" / "skos.skworld-module.json"


def test_emit_manifest_file_writes_default_well_known_path(monkeypatch, tmp_path):
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path))
    written = emit_manifest_file()
    assert written == default_manifest_path()
    assert written.exists()
    data = json.loads(written.read_text())
    assert data == skos_module_manifest(DEFAULT_BASE_URL)
    assert data["id"] == "skos"


def test_emit_manifest_file_is_idempotent_byte_for_byte(tmp_path):
    out = tmp_path / "skos.skworld-module.json"
    first = emit_manifest_file("http://host:7780/", out).read_bytes()
    second = emit_manifest_file("http://host:7780/", out).read_bytes()
    assert first == second


def test_emit_manifest_file_custom_out_and_base_url(tmp_path):
    out = tmp_path / "nested" / "manifest.json"
    written = emit_manifest_file("http://myhost:9999/", out)
    assert written == out
    data = json.loads(written.read_text())
    assert data["entry"]["url"] == "http://myhost:9999/"
    assert data["health"] == "http://myhost:9999/health"


def test_cli_manifest_emit_print_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path))
    result = runner.invoke(app, ["manifest", "emit", "--print"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == skos_module_manifest(DEFAULT_BASE_URL)
    # --print writes nothing to disk.
    assert not default_manifest_path().exists()


def test_cli_manifest_emit_writes_file_and_prints_next_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path))
    result = runner.invoke(app, ["manifest", "emit"])
    assert result.exit_code == 0, result.output
    assert default_manifest_path().exists()
    # Operator guidance: sign + register in modules.json (spec 5.3).
    assert "modules.json" in result.output
    assert "sign" in result.output


def test_cli_manifest_emit_custom_out(tmp_path):
    out = tmp_path / "skos.skworld-module.json"
    result = runner.invoke(
        app, ["manifest", "emit", "--out", str(out), "--base-url", "http://h:1/"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["entry"]["url"] == "http://h:1/"
