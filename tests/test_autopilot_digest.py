"""Autopilot numbered digest: deterministic n ordering + manifest persistence."""
import json

import pytest

from skos.autopilot import digest


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    yield


def _item(iid, priority, created, answered=False):
    return {"id": iid, "source": "autopilot", "source_ref": f"autopilot:{iid}",
            "priority": priority, "created_at": created,
            "decision": {"qid": iid, "prompt": f"P{iid}", "options": {"yes": "y", "no": "n"},
                         "answered": answered}}


def test_manifest_numbering_priority_then_created_at():
    items = [
        _item("c", "medium", "2026-07-12T09:00:00Z"),
        _item("a", "high", "2026-07-12T08:00:00Z"),
        _item("b", "high", "2026-07-12T07:00:00Z"),
        _item("z", "high", "2026-07-12T06:00:00Z", answered=True),  # excluded
    ]
    m = digest.build_manifest(items, digest_date="2026-07-12")
    assert [(i["n"], i["qid"]) for i in m["items"]] == [(1, "b"), (2, "a"), (3, "c")]
    assert all(not i["answered"] for i in m["items"])


def test_build_digest_text_lists_numbers_and_options():
    m = digest.build_manifest([_item("a", "high", "2026-07-12T08:00:00Z")],
                              digest_date="2026-07-12")
    text = digest.build_digest_text(m)
    assert "1. Pa" in text and "[yes/no]" in text
    assert "reply with the number" in text.lower()


def test_write_manifest_persists_to_gtd_dir():
    from skos.gtd_ingest import gtd_dir
    m = digest.build_manifest([_item("a", "high", "2026-07-12T08:00:00Z")],
                              digest_date="2026-07-12")
    p = digest.write_manifest(m)
    assert p == gtd_dir() / "autopilot-digest.json"
    assert json.loads(p.read_text())["items"][0]["qid"] == "a"


def test_build_manifest_defaults_to_loading_store():
    from skos.gtd_ingest import gtd_dir
    (gtd_dir() / "waiting-for.json").write_text(json.dumps([_item("a", "high", "2026-07-12T08:00:00Z")]))
    m = digest.build_manifest(digest_date="2026-07-12")
    assert [i["qid"] for i in m["items"]] == ["a"]
