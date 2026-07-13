"""Tests for skos.autopilot.resolver.answer (numbered-decision resolver)."""
import json

import pytest

from skos import gtd_ingest
from skos.autopilot import resolver


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    yield


def _seed(prompt="Merge PR #123 for skrender task?", answered=False):
    # the decision as it lives in the GTD store (source="autopilot")
    gtd_ingest.capture(gtd_ingest.GtdCapture(
        text=prompt, source="autopilot", source_ref="autopilot:q1",
        status="waiting", context="@decide", priority="high",
        meta={"decision": {"qid": "q1", "prompt": prompt, "options": {},
                           "answered": answered, "answer": None, "action_ref": None}}))
    # the per-day manifest that assigns the ordinal
    manifest = {"digest_date": "2026-07-12", "sent_at": None,
                "items": [{"n": 1, "qid": "q1", "id": "x", "source_ref": "autopilot:q1",
                           "prompt": prompt, "options": {}, "answered": answered}]}
    (gtd_ingest.gtd_dir() / "autopilot-digest.json").write_text(
        json.dumps(manifest), encoding="utf-8")


def test_answer_resolves_and_transitions():
    _seed()
    out = resolver.answer(1, "yes")
    assert out["n"] == 1 and out["qid"] == "q1" and out["answer"] == "yes"
    assert out["answered"] is True and out["gtd_action"] in ("updated", "completed")
    # decision item moved to done -> archive.json, marked answered in its meta
    arch = json.loads((gtd_ingest.gtd_dir() / "archive.json").read_text())
    assert len(arch) == 1 and arch[0]["decision"]["answered"] is True
    assert arch[0]["decision"]["answer"] == "yes"
    # manifest entry marked answered
    m = json.loads((gtd_ingest.gtd_dir() / "autopilot-digest.json").read_text())
    assert m["items"][0]["answered"] is True


def test_answer_is_idempotent():
    _seed()
    resolver.answer(1, "yes")
    out2 = resolver.answer(1, "yes")            # re-answering n is a no-op update
    assert out2["idempotent"] is True
    arch = json.loads((gtd_ingest.gtd_dir() / "archive.json").read_text())
    assert len(arch) == 1                        # not duplicated


def test_unknown_n_raises():
    _seed()
    with pytest.raises(resolver.UnknownDecision):
        resolver.answer(99)


def test_missing_manifest_raises():
    with pytest.raises(resolver.UnknownDecision):
        resolver.answer(1)
