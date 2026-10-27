"""Spooled GTD mutations: clarify / move / done / reopen and raw appends.

Follow-up to the single-owner spool (test_gtd_owner_spool.py): the skcapstone
GTD tools and the dreaming engine also rewrite whole list files, so on a
non-owner host they queue these ops and the owner applies them on drain.
Every replay must be a logged no-op, never a crash or a double apply.
"""
import json
import logging

import pytest

from skos import gtd_ingest as g
from skos.gtd_ingest import drain_spool, gtd_dir, spool_append, spool_mutation


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv("SK_GTD_SPOOL_STATE", str(tmp_path / "state" / "offsets.json"))
    monkeypatch.setenv("SKOS_ALLOW_EMPTY_STORE", "1")
    monkeypatch.delenv("SK_GTD_OWNER_HOST", raising=False)
    (gtd_dir() / "OWNER").write_text("noroc2027\n")
    yield


def _as(monkeypatch, host):
    monkeypatch.setenv("SK_GTD_HOST", host)


def _list(name):
    p = gtd_dir() / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else []


def _put(name, items):
    (gtd_dir() / f"{name}.json").write_text(json.dumps(items))


def _done(item_id, ts, src=("inbox", "next-actions", "projects", "waiting-for", "someday-maybe")):
    return spool_mutation("done", item_id, to="archive", src=list(src), ts=ts,
                          patch={"status": "done", "completed_at": ts})


def test_done_lands_on_owner_and_replay_is_noop(monkeypatch):
    _put("next-actions", [{"id": "a1", "text": "x", "status": "next", "priority": "high"}])
    _as(monkeypatch, "laptop41")
    _done("a1", "2026-10-27T10:00:00+00:00")
    assert _list("archive") == []          # non-owner never rewrites lists
    assert drain_spool() == 0              # and cannot drain
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert _list("next-actions") == []
    (it,) = _list("archive")
    assert it["status"] == "done" and it["priority"] == "high"
    assert it["completed_at"] == "2026-10-27T10:00:00+00:00"
    assert drain_spool() == 0
    g._offsets_path().unlink()             # lost offsets: full rescan
    assert drain_spool() == 0
    assert len(_list("archive")) == 1


def test_done_for_missing_or_already_archived_item_is_logged_noop(monkeypatch, caplog):
    _put("archive", [{"id": "old", "status": "done", "completed_at": "2026-01-01T00:00:00+00:00"}])
    _as(monkeypatch, "laptop41")
    _done("ghost", "2026-10-27T10:00:00+00:00")
    _done("old", "2026-10-27T10:00:00+00:00")
    _as(monkeypatch, "noroc2027")
    with caplog.at_level(logging.WARNING, logger="skos.gtd_ingest"):
        assert drain_spool() == 0
    assert "not in the store" in caplog.text
    assert "expected" in caplog.text
    assert len(_list("archive")) == 1


def test_clarify_applies_onto_owner_copy(monkeypatch):
    _put("inbox", [{"id": "c1", "text": "call bob", "status": "inbox", "meta_x": "keep"}])
    _as(monkeypatch, "laptop41")
    ts = "2026-10-27T11:00:00+00:00"
    spool_mutation("clarify", "c1", to="waiting-for", src=["inbox"], ts=ts,
                   patch={"status": "waiting", "delegate_to": "bob", "clarified_at": ts,
                          "priority": "medium", "energy": "low", "context": "@phone"})
    # the owner edits the item in the meantime; that edit must survive
    _put("inbox", [{"id": "c1", "text": "call bob re: invoice", "status": "inbox",
                    "meta_x": "keep"}])
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert _list("inbox") == []
    (it,) = _list("waiting-for")
    assert it["text"] == "call bob re: invoice" and it["meta_x"] == "keep"
    assert it["delegate_to"] == "bob" and it["status"] == "waiting"


def test_clarify_of_item_no_longer_in_inbox_is_skipped(monkeypatch):
    _put("next-actions", [{"id": "c2", "text": "t", "status": "next"}])
    _as(monkeypatch, "laptop41")
    ts = "2026-10-27T11:00:00+00:00"
    spool_mutation("clarify", "c2", to="someday-maybe", src=["inbox"], ts=ts,
                   patch={"status": "someday", "clarified_at": ts})
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 0
    assert [i["id"] for i in _list("next-actions")] == ["c2"]


def test_replayed_move_does_not_undo_a_later_owner_move(monkeypatch):
    _put("inbox", [{"id": "m1", "text": "t", "status": "inbox"}])
    _as(monkeypatch, "laptop41")
    ts = "2026-10-27T12:00:00+00:00"
    spool_mutation("move", "m1", to="next-actions", ts=ts,
                   patch={"status": "next", "moved_at": ts})
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    # owner later moves it on to projects itself
    _put("next-actions", [])
    _put("projects", [{"id": "m1", "text": "t", "status": "project",
                       "moved_at": "2026-10-27T13:00:00+00:00"}])
    g._offsets_path().unlink()
    assert drain_spool() == 0
    assert [i["id"] for i in _list("projects")] == ["m1"]
    assert _list("next-actions") == []


def test_reopen_restores_and_drops_completed_at(monkeypatch):
    _put("archive", [{"id": "r1", "text": "t", "status": "done",
                      "completed_at": "2026-10-01T00:00:00+00:00"}])
    _as(monkeypatch, "laptop41")
    ts = "2026-10-27T14:00:00+00:00"
    spool_mutation("reopen", "r1", to="next-actions", src=["archive"], ts=ts,
                   patch={"status": "next", "reopened_at": ts}, drop=["completed_at"])
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert _list("archive") == []
    (it,) = _list("next-actions")
    assert "completed_at" not in it and it["reopened_at"] == ts
    g._offsets_path().unlink()
    assert drain_spool() == 0


def test_append_is_idempotent_by_id(monkeypatch):
    _as(monkeypatch, "laptop41")
    spool_append({"id": "d1", "text": "[Dream insight] x", "status": "someday",
                  "moved_at": None}, to="someday-maybe")
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert _list("someday-maybe") == [{"id": "d1", "text": "[Dream insight] x",
                                       "status": "someday", "moved_at": None}]
    g._offsets_path().unlink()
    assert drain_spool() == 0
    assert len(_list("someday-maybe")) == 1


def test_bad_records_are_skipped_and_do_not_block_the_spool(monkeypatch):
    _put("inbox", [{"id": "ok", "text": "t", "status": "inbox"}])
    _as(monkeypatch, "laptop41")
    spool = gtd_dir() / "spool" / "laptop41.jsonl"
    spool.parent.mkdir(parents=True, exist_ok=True)
    with open(spool, "a") as f:
        f.write(json.dumps({"v": 1, "op": "teleport", "id": "ok"}) + "\n")
        f.write(json.dumps({"v": 1, "op": "done", "id": "ok", "mutation": {"to": "nowhere"}}) + "\n")
        f.write(json.dumps({"v": 1, "op": "append", "id": "x", "to": "inbox"}) + "\n")
    _done("ok", "2026-10-27T10:00:00+00:00", src=["inbox"])
    _as(monkeypatch, "noroc2027")
    assert drain_spool() == 1
    assert [i["id"] for i in _list("archive")] == ["ok"]


def test_spool_api_validates_its_arguments():
    with pytest.raises(ValueError):
        spool_mutation("explode", "x", to="archive")
    with pytest.raises(ValueError):
        spool_mutation("done", "x", to="nowhere")
    with pytest.raises(ValueError):
        spool_append({"text": "no id"}, to="someday-maybe")
