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
