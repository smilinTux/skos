"""Tests for the skos gtd-ingest port + capture() sink."""
import json
import os

import pytest

from skos.gtd_ingest import GtdCapture, GtdSourceAdapter, capture, gtd_dir, registry


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    yield


def test_capture_writes_to_correct_list():
    iid = capture(GtdCapture(text="reply to Ayana", source="email",
                             source_ref="thread-abc", status="next", priority="high"))
    assert iid
    items = json.loads((gtd_dir() / "next-actions.json").read_text())
    assert len(items) == 1
    assert items[0]["source"] == "email" and items[0]["source_ref"] == "thread-abc"
    assert items[0]["status"] == "next" and items[0]["priority"] == "high"


def test_dedupe_by_source_ref():
    c = GtdCapture(text="x", source="itil", source_ref="inc-1", status="inbox")
    first = capture(c)
    second = capture(c)  # same (source, source_ref) -> dropped
    assert first is not None and second is None
    items = json.loads((gtd_dir() / "inbox.json").read_text())
    assert len(items) == 1


def test_same_ref_different_source_not_deduped():
    assert capture(GtdCapture(text="a", source="email", source_ref="R", status="inbox"))
    assert capture(GtdCapture(text="b", source="cron", source_ref="R", status="inbox"))
    items = json.loads((gtd_dir() / "inbox.json").read_text())
    assert len(items) == 2


def test_meta_is_namespaced_onto_item():
    capture(GtdCapture(text="t", source="email", source_ref="th", status="waiting",
                       meta={"email_account": "chef@x", "email_from": "vendor"}))
    items = json.loads((gtd_dir() / "waiting-for.json").read_text())
    assert items[0]["email_account"] == "chef@x" and items[0]["email_from"] == "vendor"


def test_pull_adapter_drain():
    class FakeMail(GtdSourceAdapter):
        name = "email"
        def poll(self):
            return [GtdCapture(text="m1", source="email", source_ref="t1", status="next"),
                    GtdCapture(text="m2", source="email", source_ref="t2", status="next")]

    ids = FakeMail().drain()
    assert len(ids) == 2
    # second drain dedupes -> nothing new
    assert FakeMail().drain() == []


def test_registry_registration():
    @registry.register
    class ItilAdapter(GtdSourceAdapter):
        name = "itil-test"

    assert "itil-test" in registry.available_for("gtd-ingest")


# ── upsert: stateful create-or-update (orders/deliveries) ────────────────────
from skos.gtd_ingest import upsert  # noqa: E402


def _load_list(name):
    p = gtd_dir() / name
    return json.loads(p.read_text()) if p.exists() else []


def _order_cap(state, text, status="waiting"):
    return GtdCapture(text=text, source="order", source_ref="amazon:ORD-1",
                      status=status, context="@errand", priority="low",
                      meta={"order": {"vendor": "amazon", "state": state,
                                      "complete_on": "delivered"}})


def test_upsert_creates_when_new():
    iid, action = upsert(_order_cap("ordered", "battery - ordered"))
    assert action == "created" and iid
    wf = _load_list("waiting-for.json")
    assert len(wf) == 1 and wf[0]["order"]["state"] == "ordered"


def test_upsert_unchanged_does_not_write():
    upsert(_order_cap("ordered", "battery - ordered"))
    before = (gtd_dir() / "waiting-for.json").read_text()
    iid, action = upsert(_order_cap("ordered", "battery - ordered"))  # identical
    assert action == "unchanged"
    assert (gtd_dir() / "waiting-for.json").read_text() == before  # byte-identical: no write


def test_upsert_updates_state_in_place():
    first, _ = upsert(_order_cap("ordered", "battery - ordered"))
    second, action = upsert(_order_cap("out_for_delivery", "battery - out for delivery"))
    assert action == "updated" and second == first  # same item, not a new one
    wf = _load_list("waiting-for.json")
    assert len(wf) == 1
    assert wf[0]["order"]["state"] == "out_for_delivery"
    assert wf[0]["text"].endswith("out for delivery")
    assert "updated_at" in wf[0]


def test_upsert_completes_and_archives_on_done():
    first, _ = upsert(_order_cap("ordered", "battery - ordered"))
    done_id, action = upsert(_order_cap("delivered", "battery - delivered", status="done"))
    assert action == "completed" and done_id == first
    assert _load_list("waiting-for.json") == []          # gone from waiting
    arch = _load_list("archive.json")
    assert any(i["id"] == first and i["status"] == "done" and i.get("completed_at")
               for i in arch)
