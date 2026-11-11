"""gtd-mail attachments: sksecurity screening before save / Telegram. Mocks only.

Gmail, gog and Telegram are fully mocked: nothing here talks to the network,
runs gog, or sends a message.
"""

from __future__ import annotations

import json
import stat
import sys
import types

import pytest

from skos import mail


class _Proc:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake thread with three attachments; fake gog download; fake Telegram."""
    sent = []
    gog_calls = []
    atts = [
        {"aid": "a1", "filename": "ok.pdf", "mime": "application/pdf", "size": 3},
        {"aid": "a2", "filename": "sus.doc", "mime": "application/msword", "size": 3},
        {"aid": "a3", "filename": "bad.exe", "mime": "application/x-msdownload", "size": 3},
    ]
    monkeypatch.setattr(mail, "_resolve", lambda ref, account=None: {"account": "x@example.org", "thread_id": "T1"})
    monkeypatch.setattr(mail, "_thread", lambda acct, tid: {
        "messages": [{"id": "M1", "from": "s@example.org", "subject": "s", "attachments": atts}],
        "last_id": "M1", "subject": "s", "sender": "s@example.org"})

    def fake_run(cmd, **kw):
        gog_calls.append(cmd)
        assert cmd[0] == mail.GOG and cmd[1:3] == ["gmail", "attachment"], cmd
        out = cmd[cmd.index("--out") + 1]
        with open(out, "wb") as fh:
            fh.write(b"abc")
        return _Proc()

    monkeypatch.setattr(mail.subprocess, "run", fake_run)
    monkeypatch.setattr(mail, "_tg_send_file", lambda path, caption="": sent.append((path.name, caption)) or True)
    verdicts = {"ok.pdf": "safe", "sus.doc": "suspicious", "bad.exe": "malicious"}
    monkeypatch.setattr(mail, "_screen_attachment",
                        lambda p: {"verdict": verdicts[p.name], "detail": f"detail for {p.name}"})
    return types.SimpleNamespace(out=tmp_path / "out", sent=sent, gog=gog_calls)


def by_name(results):
    return {r["filename"]: r for r in results}


def test_malicious_quarantined_not_forwarded(env):
    res = by_name(mail.cmd_attachments("T1", save=True, telegram=True, to_dir=str(env.out)))
    bad = res["bad.exe"]
    assert bad["status"] == "quarantined" and bad["verdict"] == "malicious"
    q = env.out / "quarantine" / "bad.exe"
    assert q.exists() and not (env.out / "bad.exe").exists()
    assert stat.S_IMODE(q.stat().st_mode) == 0o600
    assert all(n != "bad.exe" for n, _ in env.sent)
    ledger = [json.loads(line) for line in (env.out / "quarantine" / "quarantine.jsonl").read_text().splitlines()]
    assert ledger[0]["name"] == "bad.exe" and ledger[0]["thread_id"] == "T1" and ledger[0]["message_id"] == "M1"
    assert stat.S_IMODE((env.out / "quarantine" / "quarantine.jsonl").stat().st_mode) == 0o600


def test_suspicious_forwarded_with_warning_prefix(env):
    res = by_name(mail.cmd_attachments("T1", save=True, telegram=True, to_dir=str(env.out)))
    assert res["sus.doc"]["status"] == "saved"
    assert (env.out / "sus.doc").exists()
    cap = dict(env.sent)["sus.doc"]
    assert "SUSPICIOUS" in cap and "sus.doc" in cap


def test_safe_forwarded_plain(env):
    mail.cmd_attachments("T1", save=True, telegram=True, to_dir=str(env.out))
    cap = dict(env.sent)["ok.pdf"]
    assert "SUSPICIOUS" not in cap and "ok.pdf" in cap
    assert (env.out / "ok.pdf").exists()


def test_staging_dirs_cleaned(env):
    mail.cmd_attachments("T1", save=True, to_dir=str(env.out))
    assert not [p for p in env.out.iterdir() if p.name.startswith(".stage-")]


def test_save_only_does_not_telegram(env):
    mail.cmd_attachments("T1", save=True, to_dir=str(env.out))
    assert env.sent == []


def test_list_only_downloads_nothing(env):
    res = mail.cmd_attachments("T1", to_dir=str(env.out))
    assert env.gog == [] and all(r["status"] == "listed" for r in res)


def test_path_traversal_filename_contained(env, monkeypatch):
    monkeypatch.setattr(mail, "_thread", lambda acct, tid: {
        "messages": [{"id": "M1", "from": "s", "subject": "s", "attachments": [
            {"aid": "a1", "filename": "../../evil.pdf", "mime": "", "size": 1}]}],
        "last_id": "M1", "subject": "s", "sender": "s"})
    monkeypatch.setattr(mail, "_screen_attachment", lambda p: {"verdict": "safe", "detail": ""})
    mail.cmd_attachments("T1", save=True, to_dir=str(env.out))
    assert (env.out / "evil.pdf").exists()
    assert not (env.out.parent / "evil.pdf").exists()


def test_failed_download_reported(env, monkeypatch):
    monkeypatch.setattr(mail.subprocess, "run", lambda cmd, **kw: _Proc())
    res = mail.cmd_attachments("T1", save=True, telegram=True, to_dir=str(env.out))
    assert all(r["status"] == "download-failed" for r in res) and env.sent == []


# --- _screen_attachment itself (screener import / CLI / missing) ---------


def _fake_filescreen(monkeypatch, verdict):
    class V:
        class _Vd:
            value = verdict
        verdict = _Vd()
        findings = [types.SimpleNamespace(description="because")]
    fs = types.ModuleType("sksecurity.filescreen")
    fs.screen_file = lambda p: V()
    monkeypatch.setitem(sys.modules, "sksecurity", types.ModuleType("sksecurity"))
    monkeypatch.setitem(sys.modules, "sksecurity.filescreen", fs)


def test_screen_uses_import_when_available(tmp_path, monkeypatch):
    _fake_filescreen(monkeypatch, "malicious")
    f = tmp_path / "x"
    f.write_bytes(b"1")
    r = mail._screen_attachment(f)
    assert r == {"verdict": "malicious", "detail": "because"}


def test_screen_falls_back_to_cli(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sksecurity.filescreen", None)
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return _Proc(json.dumps({"files": [{"verdict": "suspicious", "findings": [{"description": "d"}]}]}), 0)
    monkeypatch.setattr(mail.subprocess, "run", run)
    f = tmp_path / "x"
    f.write_bytes(b"1")
    assert mail._screen_attachment(f) == {"verdict": "suspicious", "detail": "d"}
    assert seen[0][:2] == ["sksecurity", "screen-file"]


def test_screen_cli_exit1_malicious_still_parsed(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sksecurity.filescreen", None)
    monkeypatch.setattr(mail.subprocess, "run", lambda cmd, **kw: _Proc(
        json.dumps({"files": [{"verdict": "malicious", "findings": []}]}), 1))
    f = tmp_path / "x"
    f.write_bytes(b"1")
    assert mail._screen_attachment(f)["verdict"] == "malicious"


def test_screen_neither_available_proceeds_with_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "sksecurity.filescreen", None)

    def run(cmd, **kw):
        raise FileNotFoundError("sksecurity")
    monkeypatch.setattr(mail.subprocess, "run", run)
    f = tmp_path / "x"
    f.write_bytes(b"1")
    with caplog.at_level("WARNING", logger="skos.mail"):
        r = mail._screen_attachment(f)
    assert r["verdict"] == "unscreened"
    assert "unavailable" in caplog.text


def test_unscreened_still_saved_and_forwarded(env, monkeypatch):
    monkeypatch.setattr(mail, "_screen_attachment", lambda p: {"verdict": "unscreened", "detail": ""})
    res = mail.cmd_attachments("T1", save=True, telegram=True, to_dir=str(env.out))
    assert all(r["status"] == "saved" for r in res)
    assert {n for n, _ in env.sent} == {"ok.pdf", "sus.doc", "bad.exe"}
