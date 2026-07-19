"""Durability tests for the gtd-ingest store: atomic saves, locking,
corrupt-file quarantine, and crash-safe upsert moves (card 83dbcab0)."""
import json
import os
import threading

import pytest

import skos.gtd_ingest as gi
from skos.gtd_ingest import GtdCapture, capture, gtd_dir, upsert


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    # reset the alert hook between tests
    monkeypatch.setattr(gi, "corrupt_alert_hook", None)
    yield


def _read(fname):
    p = gtd_dir() / fname
    return json.loads(p.read_text()) if p.exists() else []


# ── atomic _save ─────────────────────────────────────────────────────────────

def test_save_leaves_no_tmp_files():
    capture(GtdCapture(text="a", source="t", source_ref="r1"))
    leftovers = [p.name for p in gtd_dir().iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_save_crash_preserves_original():
    capture(GtdCapture(text="first", source="t", source_ref="r1"))
    original = (gtd_dir() / "inbox.json").read_text()

    real_replace = os.replace

    def boom(src, dst, *a, **kw):
        if str(dst).endswith("inbox.json"):
            raise OSError("simulated crash before rename")
        return real_replace(src, dst, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            capture(GtdCapture(text="second", source="t", source_ref="r2"))

    # original store bytes untouched, still valid JSON with 1 item, no tmp junk
    assert (gtd_dir() / "inbox.json").read_text() == original
    assert len(_read("inbox.json")) == 1
    assert [p for p in gtd_dir().iterdir() if p.name.endswith(".tmp")] == []


# ── corrupt-file quarantine ──────────────────────────────────────────────────

def test_corrupt_file_quarantined_not_silently_emptied():
    d = gtd_dir()
    (d / "inbox.json").write_text("{ this is not json", encoding="utf-8")
    alerts = []
    gi.corrupt_alert_hook = lambda p, q, exc: alerts.append((p.name, q.name))

    iid = capture(GtdCapture(text="new", source="t", source_ref="r1"))
    assert iid

    quarantined = [p for p in d.iterdir() if ".corrupt-" in p.name]
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{ this is not json"
    assert len(alerts) == 1 and alerts[0][0] == "inbox.json"
    # new store starts clean with just the new item
    assert [i["text"] for i in _read("inbox.json")] == ["new"]


def test_non_list_json_is_quarantined():
    d = gtd_dir()
    (d / "inbox.json").write_text('{"not": "a list"}', encoding="utf-8")
    capture(GtdCapture(text="x", source="t", source_ref="r1"))
    assert any(".corrupt-" in p.name for p in d.iterdir())
    assert len(_read("inbox.json")) == 1


def test_read_oserror_raises_loudly():
    d = gtd_dir()
    (d / "inbox.json").write_text("[]", encoding="utf-8")

    import pathlib
    real_read_text = pathlib.Path.read_text

    def flaky(self, *a, **kw):
        if self.name == "inbox.json":
            raise OSError("simulated I/O error")
        return real_read_text(self, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pathlib.Path, "read_text", flaky)
        with pytest.raises(OSError):
            capture(GtdCapture(text="x", source="t", source_ref="r1"))
    # the file must NOT have been quarantined or replaced
    assert (d / "inbox.json").exists()
    assert not any(".corrupt-" in p.name for p in d.iterdir())


# ── lock contention: concurrent writers lose nothing ─────────────────────────

def test_concurrent_captures_no_lost_update():
    n_threads, per_thread = 8, 5
    errors = []

    def worker(t):
        try:
            for k in range(per_thread):
                capture(GtdCapture(text=f"t{t}-{k}", source="t",
                                   source_ref=f"ref-{t}-{k}"))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []
    items = _read("inbox.json")
    assert len(items) == n_threads * per_thread
    assert len({i["source_ref"] for i in items}) == n_threads * per_thread


# ── upsert cross-file move: write-then-delete, crash-safe ────────────────────

def _order(state, text, status="waiting"):
    return GtdCapture(text=text, source="order", source_ref="amazon:ORD-9",
                      status=status, context="@errand",
                      meta={"order": {"state": state}})


def test_upsert_move_crash_between_writes_never_loses_item():
    first, _ = upsert(_order("ordered", "battery - ordered"))

    real_save = gi._save

    def crashy(fname, items):
        if fname == "waiting-for.json":  # the delete-phase write
            raise OSError("simulated crash after dest write")
        return real_save(fname, items)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gi, "_save", crashy)
        with pytest.raises(OSError):
            upsert(_order("delivered", "battery - delivered", status="done"))

    # dest write happened first, so the item survives in archive
    arch = _read("archive.json")
    assert any(i["id"] == first and i["status"] == "done" for i in arch)


def test_upsert_move_self_heals_duplicate_after_crash():
    first, _ = upsert(_order("ordered", "battery - ordered"))

    real_save = gi._save

    def crashy(fname, items):
        if fname == "waiting-for.json":
            raise OSError("simulated crash")
        return real_save(fname, items)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gi, "_save", crashy)
        with pytest.raises(OSError):
            upsert(_order("delivered", "battery - delivered", status="done"))

    # after "restart": stale copy still in waiting, fresh copy in archive
    assert len(_read("waiting-for.json")) == 1
    # re-running the move completes it without duplicating in the dest
    done_id, action = upsert(_order("delivered", "battery - delivered", status="done"))
    assert done_id == first and action == "completed"
    assert _read("waiting-for.json") == []
    matching = [i for i in _read("archive.json") if i["id"] == first]
    assert len(matching) == 1 and matching[0]["status"] == "done"


def test_upsert_same_file_update_keeps_single_copy():
    first, _ = upsert(_order("ordered", "battery - ordered"))
    second, action = upsert(_order("shipped", "battery - shipped"))
    assert second == first and action == "updated"
    wf = _read("waiting-for.json")
    assert len(wf) == 1 and wf[0]["order"]["state"] == "shipped"
