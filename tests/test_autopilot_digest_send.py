"""Tests for send_digest: sk-alert wiring, numbering, dry-run suppression."""
from types import SimpleNamespace

from skos.autopilot import digest

# send_digest() is defined in skharness.autocode.digest and calls
# rebuild_manifest/build_digest_text as its OWN module globals, so the patches
# must target that implementation module, not the skos.autopilot shim (which
# only holds re-exported copies of the same names). digest.subprocess stays a
# shared stdlib module object either way, so patching it via the shim works
# regardless; kept as-is for minimal diff.
import skharness.autocode.digest as _digest_impl


def test_send_digest_live_sends_numbered_text(mocker):
    manifest = {"items": [{"n": 1}, {"n": 2}]}
    mocker.patch.object(_digest_impl, "rebuild_manifest", return_value=manifest)
    mocker.patch.object(_digest_impl, "build_digest_text",
                        return_value="Morning decisions (reply with the number):\n1. A\n2. B")
    run = mocker.patch.object(digest.subprocess, "run")
    cfg = SimpleNamespace(digest_chat="999000111", dry_run_summary=False)

    out = digest.send_digest(cfg, dry_run=False)

    assert out["sent"] is True and out["mode"] == "live" and out["items"] == 2
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv[0] == "sk-alert" and "999000111" in argv
    assert argv[-1] == "Morning decisions (reply with the number):\n1. A\n2. B"


def test_dry_run_without_optin_sends_nothing(mocker):
    mocker.patch.object(_digest_impl, "rebuild_manifest", return_value={"items": [{"n": 1}]})
    mocker.patch.object(_digest_impl, "build_digest_text", return_value="1. A")
    run = mocker.patch.object(digest.subprocess, "run")
    cfg = SimpleNamespace(digest_chat="999000111", dry_run_summary=False)

    out = digest.send_digest(cfg, dry_run=True)

    assert out["sent"] is False and out["reason"] == "dry-run"
    run.assert_not_called()


def test_dry_run_with_optin_sends_one_summary(mocker):
    mocker.patch.object(_digest_impl, "rebuild_manifest", return_value={"items": [{"n": 1}, {"n": 2}]})
    mocker.patch.object(_digest_impl, "build_digest_text", return_value="1. A\n2. B")
    run = mocker.patch.object(digest.subprocess, "run")
    cfg = SimpleNamespace(digest_chat="42", dry_run_summary=True)

    out = digest.send_digest(cfg, dry_run=True)

    assert out["sent"] is True and out["mode"] == "dry-run-summary"
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv[0] == "sk-alert" and "42" in argv
    assert "dry-run" in argv[-1] and "2 decision" in argv[-1]   # summary, not the full digest
