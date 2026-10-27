"""Single-owner writes: non-owner hosts spool, the owner folds the spools in.

Regression for 2026-10-27: noroc2027 and .41 both rewrote inbox.json inside the
Syncthing window and the loser's items survived only in .sync-conflict copies.
"""
import json

import pytest

from skos import gtd_ingest as g
from skos.gtd_ingest import GtdCapture, capture, drain_spool, gtd_dir, upsert


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv("SK_GTD_SPOOL_STATE", str(tmp_path / "state" / "offsets.json"))
    monkeypatch.delenv("SK_GTD_OWNER_HOST", raising=False)
    (gtd_dir() / "OWNER").write_text("# GTD owner host\nnoroc2027\n")
    yield


def _as(monkeypatch, host):
    monkeypatch.setenv("SK_GTD_HOST", host)


def _inbox():
    p = gtd_dir() / "inbox.json"
    return json.loads(p.read_text()) if p.exists() else []


def test_no_owner_file_means_direct_writes(monkeypatch):
    (gtd_dir() / "OWNER").unlink()
    _as(monkeypatch, "anyhost")
    assert g.is_owner()
    iid = capture(GtdCapture(text="x", source="cron", source_ref="r1"))
    assert [i["id"] for i in _inbox()] == [iid]


def test_non_owner_never_writes_lists(monkeypatch):
    _as(monkeypatch, "laptop41")
    assert not g.is_owner()
    iid = capture(GtdCapture(text="cron FAILED", source="cron", source_ref="cron:a@d"))
    assert iid
    assert not (gtd_dir() / "inbox.json").exists()
    assert (gtd_dir() / "spool" / "laptop41.jsonl").exists()
    # a repeat on the same host is deduped against its own spool
    assert capture(GtdCapture(text="cron FAILED", source="cron", source_ref="cron:a@d")) is None
    assert drain_spool() == 0  # non-owner drain is a no-op


def test_owner_lands_spooled_item_with_its_id_once(monkeypatch):
    _as(monkeypatch, "laptop41")
    iid = capture(GtdCapture(text="from 41", source="cron", source_ref="cron:b@d"))
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert [i["id"] for i in _inbox()] == [iid]
    assert drain_spool() == 0
    # offsets lost: rescan is idempotent by id
    (g._offsets_path()).unlink()
    assert drain_spool() == 0
    assert len(_inbox()) == 1


def test_owner_capture_drains_first(monkeypatch):
    _as(monkeypatch, "laptop41")
    a = capture(GtdCapture(text="a", source="cron", source_ref="ra"))
    _as(monkeypatch, "noroc2027.lan")  # short-name match counts
    b = capture(GtdCapture(text="b", source="cron", source_ref="rb"))
    assert [i["id"] for i in _inbox()] == [a, b]


def test_spooled_item_already_done_is_not_resurrected(monkeypatch):
    _as(monkeypatch, "laptop41")
    iid = capture(GtdCapture(text="a", source="manual", source_ref=""))
    _as(monkeypatch, "noroc2027")
    drain_spool()
    items = _inbox()
    (gtd_dir() / "archive.json").write_text(json.dumps(items))
    (gtd_dir() / "inbox.json").write_text("[]")
    g._offsets_path().unlink()
    assert drain_spool() == 0
    assert _inbox() == []


def test_partial_trailing_line_waits(monkeypatch):
    _as(monkeypatch, "laptop41")
    capture(GtdCapture(text="a", source="cron", source_ref="ra"))
    with open(gtd_dir() / "spool" / "laptop41.jsonl", "a") as f:
        f.write('{"v": 1, "op": "capture"')  # mid-write / mid-sync
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert len(_inbox()) == 1


def test_non_owner_upsert_is_spooled_and_applied(monkeypatch):
    _as(monkeypatch, "noroc2027")
    iid, action = upsert(GtdCapture(text="order", source="order", source_ref="o1"))
    assert action == "created"
    _as(monkeypatch, "laptop41")
    sid, action = upsert(GtdCapture(text="order", source="order", source_ref="o1",
                                    status="done"))
    assert (sid, action) == (iid, "spooled")
    assert [i["id"] for i in _inbox()] == [iid]  # untouched until the owner drains
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert _inbox() == []
    assert [i["id"] for i in json.loads((gtd_dir() / "archive.json").read_text())] == [iid]
