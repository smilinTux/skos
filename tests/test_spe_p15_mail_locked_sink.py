"""SPE P1.5: mail.py writes through the locked, atomic store sink.

Card ``4082d990`` (sprint ``83482526``, epic ``373a33ca``). ``skos.mail._save``
was a plain ``write_text``: unlocked AND non-atomic, so a concurrent writer
could lose an update and a crash mid-write could leave a truncated store file.
``cmd_done`` also archived items itself, popping from the source list and
saving it BEFORE writing the archive, the same lose-on-crash ordering that
card 4562954e fixed on the skcapstone side.

It also resolved the store directory from ``SKCAPSTONE_HOME`` alone, ignoring
``SK_GTD_DIR``, so under an override it wrote to a different directory than the
one the ``.gtd.lock`` file lives in: a lock that protected nothing.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from skos import gtd_ingest


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    gtd_ingest.gtd_dir()  # create it
    yield


def _mail():
    import importlib

    from skos import mail

    return importlib.reload(mail)


def test_save_writes_into_the_unified_store_dir():
    mail = _mail()
    mail._save("inbox", [{"id": "m1", "text": "hi"}])
    assert json.loads((gtd_ingest.gtd_dir() / "inbox.json").read_text()) == [
        {"id": "m1", "text": "hi"}
    ]


def test_save_is_atomic_so_a_crash_leaves_the_old_file(monkeypatch):
    import os as _os

    mail = _mail()
    mail._save("inbox", [{"id": "original"}])

    def _boom(*a, **kw):
        raise OSError("crash at the rename")

    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(OSError):
        mail._save("inbox", [{"id": "replacement"}])

    path = gtd_ingest.gtd_dir() / "inbox.json"
    assert json.loads(path.read_text()) == [{"id": "original"}]
    leftovers = list(gtd_ingest.gtd_dir().glob(".inbox.json.*.tmp"))
    assert leftovers == []


def test_done_serializes_on_the_shared_store_lock(monkeypatch):
    mail = _mail()
    mail._save("inbox", [{"id": "gtd1", "text": "close me"}])

    order: list[str] = []
    holding = threading.Event()

    def hold_the_lock():
        with gtd_ingest._store_lock():
            holding.set()
            time.sleep(0.25)
            order.append("holder-release")

    t_hold = threading.Thread(target=hold_the_lock)
    t_hold.start()
    assert holding.wait(2), "lock holder never started"

    def mail_done():
        mail.cmd_done("gtd1")
        order.append("mail-done")

    t_mail = threading.Thread(target=mail_done)
    t_mail.start()
    t_hold.join(3)
    t_mail.join(3)

    assert order == ["holder-release", "mail-done"], f"mail bypassed the lock: {order}"


def test_done_writes_the_archive_before_deleting_the_source(monkeypatch):
    """A crash in the transfer window must duplicate, never lose."""
    mail = _mail()
    mail._save("inbox", [{"id": "gtd2", "text": "precious"}])

    real_save = gtd_ingest._save

    def _save(fname: str, items):
        if fname == "inbox.json":
            raise OSError("crash between the two saves")
        real_save(fname, items)

    monkeypatch.setattr(gtd_ingest, "_save", _save)
    with pytest.raises(OSError):
        mail.cmd_done("gtd2")

    assert [it["id"] for it in gtd_ingest._load("inbox.json")] == ["gtd2"]
    assert [it["id"] for it in gtd_ingest._load("archive.json")] == ["gtd2"]


def test_done_still_archives_on_the_happy_path():
    mail = _mail()
    mail._save("next-actions", [{"id": "gtd3", "text": "finish it"}])
    mail.cmd_done("gtd3")
    assert gtd_ingest._load("next-actions.json") == []
    archived = gtd_ingest._load("archive.json")
    assert [it["id"] for it in archived] == ["gtd3"]
    assert archived[0]["status"] == "done"
    assert archived[0]["completed_at"]
