"""Tests for gtd_triage (card eccd3f85): deterministic parsers, the
confidence autonomy gate, LLM degradation, and the unified-sink write path.
All LLM calls are mocked; nothing here talks to skgateway."""
from pathlib import Path

import pytest

from skos import gtd_triage as gt
from skos.gtd_ingest import _load

FIXTURES = Path(__file__).parent / "fixtures" / "triage"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path))
    yield


# ------------------------------------------------------------ deterministic

def test_parse_ics_extracts_vevent_with_folded_summary():
    events = gt.parse_ics((FIXTURES / "invite.ics").read_text())
    assert len(events) == 1
    ev = events[0]
    assert ev["uid"] == "evt-abc123@example.com"
    # unfolding joins continuation lines
    assert "hiring manager" in ev["summary"]
    assert ev["dtstart"].startswith("20260722T140000")
    assert ev["location"] == "Google Meet"


def test_parse_ics_no_events_returns_empty():
    assert gt.parse_ics("plain text, no calendar here") == []


def test_parse_email_headers_and_body():
    mail = gt.parse_email((FIXTURES / "order.eml").read_text())
    assert mail["subject"] == "Your package has shipped"
    assert mail["message_id"] == "<msg-777@amazon.com>"
    assert "112-555" in mail["body"]


def test_parse_email_rejects_non_email():
    assert gt.parse_email("just a note about nothing") is None


def test_triage_ics_is_deterministic_auto():
    results = gt.triage_item((FIXTURES / "invite.ics").read_text(),
                             use_llm=False)
    assert len(results) == 1
    r = results[0]
    assert r.kind == "calendar" and r.auto is True
    assert r.confidence == 1.0 and r.via == "deterministic"
    assert r.source_ref == "evt-abc123@example.com"


# ------------------------------------------------------------ autonomy gate

def test_llm_high_confidence_auto_files(monkeypatch):
    monkeypatch.setattr(gt, "triage_llm", lambda text: {
        "kind": "waiting", "confidence": 0.93,
        "summary": "Waiting for Amazon package 112-555"})
    (r,) = gt.triage_item((FIXTURES / "order.eml").read_text())
    assert r.kind == "waiting" and r.auto is True and r.via == "llm"
    assert r.source_ref == "<msg-777@amazon.com>"


def test_llm_below_gate_goes_to_inbox(monkeypatch):
    monkeypatch.setattr(gt, "triage_llm", lambda text: {
        "kind": "action", "confidence": 0.55, "summary": "Maybe do a thing"})
    (r,) = gt.triage_item("some ambiguous note")
    assert r.auto is False
    item_id, action = gt.file_result(r)
    assert action == "created"
    items = _load("inbox.json")
    (it,) = [i for i in items if i["id"] == item_id]
    # gtd_ingest flattens meta keys onto the item
    assert it["triage_pending"] is True
    assert it["triage"]["confidence"] == 0.55


def test_gate_boundary_is_inclusive(monkeypatch):
    monkeypatch.setattr(gt, "triage_llm", lambda text: {
        "kind": "action", "confidence": 0.80, "summary": "Do the thing"})
    (r,) = gt.triage_item("borderline")
    assert r.auto is True


def test_llm_failure_degrades_to_manual_inbox(monkeypatch):
    monkeypatch.setattr(gt, "_chat_json", lambda prompt, **kw: None)
    (r,) = gt.triage_item((FIXTURES / "order.eml").read_text())
    assert r.auto is False and r.via == "fallback"
    assert r.confidence == 0.0


def test_triage_llm_rejects_invalid_kind(monkeypatch):
    monkeypatch.setattr(gt, "_chat_json",
                        lambda prompt, **kw: {"kind": "banana",
                                              "confidence": 0.99,
                                              "summary": "x"})
    assert gt.triage_llm("whatever") is None


def test_triage_llm_rejects_out_of_range_confidence(monkeypatch):
    monkeypatch.setattr(gt, "_chat_json",
                        lambda prompt, **kw: {"kind": "action",
                                              "confidence": 1.7,
                                              "summary": "x"})
    assert gt.triage_llm("whatever") is None


# ------------------------------------------------------- filing and routing

def test_reference_auto_routes_to_skingest(monkeypatch):
    monkeypatch.setattr(gt, "triage_llm", lambda text: {
        "kind": "reference", "confidence": 0.95,
        "summary": "Useful sequoia PQC build notes"})
    (r,) = gt.triage_item("notes about building sequoia with PQC")
    item_id, action = gt.file_result(r)
    # status "reference" files into someday-maybe.json; meta is flattened
    items = _load("someday-maybe.json")
    (it,) = [i for i in items if i["id"] == item_id]
    assert it["route"] == "skingest"


def test_drain_batch_and_idempotent(monkeypatch):
    ics = (FIXTURES / "invite.ics").read_text()
    payloads = [(ics, "calendar", None)]
    audit1 = gt.drain(payloads, use_llm=False)
    audit2 = gt.drain(payloads, use_llm=False)
    assert audit1[0]["action"] == "created"
    assert audit2[0]["action"] in ("unchanged", "updated")
    assert audit1[0]["id"] == audit2[0]["id"]
    assert audit1[0]["kind"] == "calendar" and audit1[0]["auto"] is True
