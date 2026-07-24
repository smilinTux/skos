"""Guards for the committed-PII/secret scrub (card 422694eb).

1. secret_env resolves from the process env, then a gitignored operator env
   file, then a caller placeholder - never a hardcoded real value.
2. No real operator secret or PII default survives in the tracked source that
   used to carry it (gog keyring password, Gmail addresses, DM/chat id,
   skmem-pg password).
"""
import re
from pathlib import Path

import pytest

from skos import secret_env

REPO = Path(__file__).resolve().parent.parent


def _fresh():
    secret_env._file_values.cache_clear()


def test_resolve_prefers_process_env(monkeypatch):
    _fresh()
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", "/nonexistent/nope.env")
    monkeypatch.setenv("MY_SECRET", "from-env")
    assert secret_env.resolve("MY_SECRET", "ph") == "from-env"


def test_resolve_falls_back_to_env_file(tmp_path, monkeypatch):
    _fresh()
    env = tmp_path / "skos-schedule.env"
    env.write_text(
        "# comment\n"
        'GOG_KEYRING_PASSWORD="file-value"\n'
        "export GTD_MAIL_ACCOUNTS=a@example.com,b@example.com\n"
    )
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(env))
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)
    monkeypatch.delenv("GTD_MAIL_ACCOUNTS", raising=False)
    assert secret_env.resolve("GOG_KEYRING_PASSWORD", "ph") == "file-value"
    assert secret_env.accounts() == ["a@example.com", "b@example.com"]


def test_resolve_returns_placeholder_when_unconfigured(tmp_path, monkeypatch):
    _fresh()
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "absent.env"))
    monkeypatch.delenv("UNSET_THING", raising=False)
    assert secret_env.resolve("UNSET_THING", "placeholder") == "placeholder"
    assert secret_env.accounts("UNSET_ACCTS") == []


def test_ensure_copies_env_file_value_into_environ(tmp_path, monkeypatch):
    _fresh()
    env = tmp_path / "skos-schedule.env"
    env.write_text("GOG_KEYRING_PASSWORD=xyz\n")
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(env))
    monkeypatch.delenv("GOG_KEYRING_PASSWORD", raising=False)
    assert secret_env.ensure("GOG_KEYRING_PASSWORD") == "xyz"
    import os
    assert os.environ["GOG_KEYRING_PASSWORD"] == "xyz"


# --- no real secret/PII default remains in the tracked source it lived in ---

_LEAKED_KEYRING_PW = "sk" "2026"  # split so this guard file never re-commits it
_LEAKED_DM_ID = "15946" "78363"
_GMAIL_RE = re.compile(r"[a-z0-9][\w.+-]*@gmail\.com", re.IGNORECASE)

_SCRUBBED_FILES = [
    "src/skos/mail.py",
    "src/skos/status.py",
    "src/skos/adapters/calendar.py",
    "src/skos/adapters/telegram.py",
    "scripts/gtd-triage.sh",
    "docs/gtd-ingest-SOP.md",
]


@pytest.mark.parametrize("rel", _SCRUBBED_FILES)
def test_no_committed_secret_or_pii(rel):
    text = (REPO / rel).read_text(encoding="utf-8")
    assert _LEAKED_KEYRING_PW not in text, f"leaked keyring password in {rel}"
    assert _LEAKED_DM_ID not in text, f"leaked operator DM id in {rel}"
    assert not _GMAIL_RE.search(text), f"hardcoded personal gmail address in {rel}"


def test_no_hardcoded_pg_password_default():
    text = (REPO / "src/skos/status.py").read_text(encoding="utf-8")
    assert '"PGPASSWORD": "***REMOVED***"' not in text
    assert "PGPASSWORD" in text  # still wired, just resolved at runtime
