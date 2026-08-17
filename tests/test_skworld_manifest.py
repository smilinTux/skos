"""skos' SKWorld module manifest: shape, origin-relative URLs, operator facet,
the static-file emitter the umbrella shell registry reads (spec 5.3), and the
capauth signature round-trip the shell gate depends on (spec 5.3: "the shell
refuses any manifest whose detached capauth signature does not verify")."""

import json

import pytest
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
        "WatchdogDigestFresh",
        "GradingBacklog",
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


# --- signature verify (spec 5.3: the shell's refuse-unless-verifies gate) -------
#
# skos does not sign its own manifest (an operator signs it with the capauth
# root key, exactly as the shipped ~/.skcapstone/shell/modules/skos.skworld-
# module.json.sig shows). What skos MUST guarantee is that the bytes it emits
# are a stable, signable, verifiable payload: sign the emitted bytes, verify OK
# against the signer's public key, and confirm any tamper breaks verification.
# The crypto goes through the SAME capauth backend the fleet signing path uses
# (fleet/signing.py -> capauth.crypto). Gated on the sibling repo being present
# so skos' suite still runs standalone (mirrors the conformance drift-guard).


def _pgpy_backend_and_keys():
    """An ephemeral (Ed25519, fast) capauth PGP keypair + PGPy backend, or skip."""
    pytest.importorskip("pgpy", reason="pgpy (capauth crypto dep) not installed")
    crypto = pytest.importorskip(
        "capauth.crypto", reason="capauth (sibling repo) not installed"
    )
    models = pytest.importorskip("capauth.models")
    backend = crypto.get_backend(crypto.CryptoBackendType.PGPY)
    bundle = backend.generate_keypair(
        "skos manifest test", "skos-test@skworld.io", "pw", models.Algorithm.ED25519
    )
    return backend, bundle


def test_emitted_manifest_bytes_sign_and_verify_round_trip():
    backend, bundle = _pgpy_backend_and_keys()
    payload = render_manifest_json("http://100.108.59.57:7781/").encode("utf-8")
    signature = backend.sign(payload, bundle.private_armor, "pw")
    # The signer's public key verifies the exact emitted bytes (the shell gate).
    assert backend.verify(payload, signature, bundle.public_armor) is True


def test_tampered_manifest_fails_signature_verification():
    backend, bundle = _pgpy_backend_and_keys()
    payload = render_manifest_json("http://host:7781/").encode("utf-8")
    signature = backend.sign(payload, bundle.private_armor, "pw")
    # Flip the grade B -> A in the signed bytes: verification must reject it,
    # so a mutated manifest can never render behind a stale-but-valid signature.
    tampered = payload.replace(b'"grade": "B"', b'"grade": "A"')
    assert tampered != payload
    assert backend.verify(tampered, signature, bundle.public_armor) is False
