"""Blank-machine secret provisioning + recovery status (card d65ff0ca).

Proves `skos.secrets_check`:
  * reports required secrets as MISSING on a blank machine (fail-before),
  * flips to present once each is provisioned (pass-after),
  * never returns/prints a secret VALUE,
  * `bootstrap` scaffolds the operator env file with placeholders only, 600,
    and never clobbers an existing file.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from cryptography.fernet import Fernet

from skos import secret_env, secrets_check


@pytest.fixture
def blank_env(tmp_path, monkeypatch):
    """A blank machine: no vault key, no operator env file, no plane env values."""
    monkeypatch.setenv("SK_DATA_ROOT", str(tmp_path / "skdata"))
    monkeypatch.delenv("SKOS_PROFILE", raising=False)
    monkeypatch.delenv("SKOS_VAULT_KEY", raising=False)
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "skos-schedule.env"))
    for k in ("GOG_KEYRING_PASSWORD", "SKMEM_PG_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    secret_env._file_values.cache_clear()
    yield tmp_path
    secret_env._file_values.cache_clear()


def _status(report, name):
    return next(s for s in report.statuses if s.name == name)


# ── fail-before: blank machine ────────────────────────────────────────────────


def test_blank_machine_required_secrets_missing(blank_env):
    report = secrets_check.check()
    assert not report.ok
    names = {s.name for s in report.missing_required}
    assert names == {"master.key", "schedule-env", "GOG_KEYRING_PASSWORD"}
    # master.key resolves under SK_DATA_ROOT but the file is absent
    assert _status(report, "master.key").present is False
    assert _status(report, "schedule-env").present is False


def test_optional_secrets_do_not_gate_exit(blank_env):
    report = secrets_check.check()
    for name in ("SKMEM_PG_PASSWORD", "gog-tokens", "capauth-identity"):
        assert _status(report, name).required is False


# ── pass-after: provision each required secret ───────────────────────────────


def test_master_key_present_via_env(blank_env, monkeypatch):
    monkeypatch.setenv("SKOS_VAULT_KEY", Fernet.generate_key().decode())
    assert secrets_check.check() and _status(secrets_check.check(), "master.key").present


def test_master_key_present_via_keyfile(blank_env):
    from skos import paths

    keyfile = paths.data_root() / "secrets" / "master.key"
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.write_bytes(Fernet.generate_key())
    assert _status(secrets_check.check(), "master.key").present is True


def test_env_file_and_gog_password_present_after_fill(blank_env, monkeypatch):
    envfile = blank_env / "skos-schedule.env"
    envfile.write_text("GOG_KEYRING_PASSWORD=a-real-unlock-value\n")
    secret_env._file_values.cache_clear()
    report = secrets_check.check()
    assert _status(report, "schedule-env").present is True
    assert _status(report, "GOG_KEYRING_PASSWORD").present is True


def test_placeholder_value_reads_as_missing(blank_env):
    envfile = blank_env / "skos-schedule.env"
    envfile.write_text("GOG_KEYRING_PASSWORD=replace-me\n")
    secret_env._file_values.cache_clear()
    report = secrets_check.check()
    # file exists, but the value is still the template placeholder
    assert _status(report, "schedule-env").present is True
    assert _status(report, "GOG_KEYRING_PASSWORD").present is False


def test_fully_provisioned_machine_is_ok(blank_env, monkeypatch):
    monkeypatch.setenv("SKOS_VAULT_KEY", Fernet.generate_key().decode())
    (blank_env / "skos-schedule.env").write_text("GOG_KEYRING_PASSWORD=real-value\n")
    secret_env._file_values.cache_clear()
    assert secrets_check.check().ok is True


# ── the value is never exposed ───────────────────────────────────────────────


def test_report_never_contains_secret_values(blank_env):
    sentinel = "SUPER-SECRET-VALUE-DO-NOT-LEAK"
    (blank_env / "skos-schedule.env").write_text(
        f"GOG_KEYRING_PASSWORD={sentinel}\nSKMEM_PG_PASSWORD={sentinel}\n"
    )
    secret_env._file_values.cache_clear()
    report = secrets_check.check()
    blob = json.dumps(report.as_dict())
    assert sentinel not in blob
    # status objects expose no value-bearing field
    for s in report.statuses:
        assert not hasattr(s, "value")
        assert sentinel not in json.dumps(s.as_dict())


# ── bootstrap: scaffold only, never a real secret ────────────────────────────


def test_bootstrap_creates_600_placeholder_file(blank_env):
    res = secrets_check.bootstrap()
    assert res.created is True
    assert res.path.exists()
    mode = stat.S_IMODE(os.stat(res.path).st_mode)
    assert mode == 0o600
    text = res.path.read_text()
    assert "GOG_KEYRING_PASSWORD=replace-me" in text
    assert "SKMEM_PG_PASSWORD=replace-me" in text


def test_scaffold_contains_no_real_value(blank_env):
    scaffold = secrets_check.env_scaffold()
    # every env-value line is a placeholder, never a real secret
    for line in scaffold.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            assert line.split("=", 1)[1] == "replace-me"


def test_bootstrap_does_not_clobber_existing(blank_env):
    envfile = blank_env / "skos-schedule.env"
    envfile.write_text("GOG_KEYRING_PASSWORD=already-here\n")
    res = secrets_check.bootstrap()
    assert res.created is False
    assert envfile.read_text() == "GOG_KEYRING_PASSWORD=already-here\n"


def test_bootstrap_force_overwrites(blank_env):
    envfile = blank_env / "skos-schedule.env"
    envfile.write_text("GOG_KEYRING_PASSWORD=already-here\n")
    res = secrets_check.bootstrap(force=True)
    assert res.created is True
    assert "replace-me" in envfile.read_text()
