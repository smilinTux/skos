"""Tests for the email → unified-GTD adapter (the 4-C cadence label tail).

Verifies that EVERY 4-C cadence label (`1 Action` / `2 Waiting` / `3 Read` /
`4 Someday`) drains through the ONE gtd-ingest sink, mapped to the right lane,
source_ref-deduped, with no parallel side-list. All Gmail I/O is mocked; nothing
here talks to gog/network.
"""
import pytest

from skos import mail as M
from skos.gtd_ingest import _load


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path))
    yield


@pytest.fixture
def _mailboxes(monkeypatch):
    """One fake account; list_threads returns a distinct thread per label."""
    monkeypatch.setattr(M, "ACCOUNTS", ["chef@example.com"])

    def _fake_list_threads(account, query, maxn=100):
        # query looks like: label:"3 Read"
        label = query.split('label:"', 1)[1].rstrip('"')
        tag = {"1 Action": "A", "2 Waiting": "W", "3 Read": "R", "4 Someday": "S"}[label]
        return [{"id": f"th-{tag}", "from": "Someone <a@b.com>",
                 "subject": f"{label} subject", "date": "2026-08-06"}]

    monkeypatch.setattr(M, "list_threads", _fake_list_threads)
    return _fake_list_threads


def test_email_labels_cover_all_four_cadence_lanes():
    labels = [row[0] for row in M.EMAIL_LABELS]
    assert labels == ["1 Action", "2 Waiting", "3 Read", "4 Someday"]


def test_email_captures_map_each_label_to_the_right_lane(_mailboxes):
    caps = {c.meta["email_label"]: c for c in M.email_captures()}
    assert set(caps) == {"1 Action", "2 Waiting", "3 Read", "4 Someday"}

    assert caps["1 Action"].status == "next" and caps["1 Action"].context == "@email"
    assert caps["2 Waiting"].status == "waiting" and caps["2 Waiting"].context == "@email"
    # the archived "later" lanes both file into someday-maybe.json
    assert caps["3 Read"].status == "reference" and caps["3 Read"].context == "@read"
    assert caps["4 Someday"].status == "someday" and caps["4 Someday"].context == "@someday"
    # every capture carries source="email" + the thread id as the dedup key
    for c in caps.values():
        assert c.source == "email" and c.source_ref == c.meta["email_thread_id"]


def test_capture_drains_all_lanes_through_the_sink(_mailboxes, capsys):
    M.cmd_capture()
    # 1 Action -> next-actions.json, 2 Waiting -> waiting-for.json
    assert [i["source_ref"] for i in _load("next-actions.json")] == ["th-A"]
    assert [i["source_ref"] for i in _load("waiting-for.json")] == ["th-W"]
    # 3 Read (reference) + 4 Someday both land in someday-maybe.json
    somerefs = {i["source_ref"] for i in _load("someday-maybe.json")}
    assert somerefs == {"th-R", "th-S"}
    # nothing stranded in inbox.json (every lane routed to a real list)
    assert _load("inbox.json") == []


def test_capture_is_idempotent_source_ref_deduped(_mailboxes):
    M.cmd_capture()
    M.cmd_capture()  # second run must not duplicate
    assert len(_load("next-actions.json")) == 1
    assert len(_load("someday-maybe.json")) == 2


def test_archive_lane_cap_zero_skips_read_and_someday(monkeypatch, _mailboxes):
    monkeypatch.setattr(M, "EMAIL_LABELS", [
        ("1 Action",  "next",      "high",   "@email",   100),
        ("2 Waiting", "waiting",   "medium", "@email",   100),
        ("3 Read",    "reference", "low",    "@read",    0),
        ("4 Someday", "someday",   "low",    "@someday", 0),
    ])
    labels = {c.meta["email_label"] for c in M.email_captures()}
    assert labels == {"1 Action", "2 Waiting"}


def test_gtd_email_labels_env_selects_subset(monkeypatch, _mailboxes):
    monkeypatch.setenv("GTD_EMAIL_LABELS", "1 Action, 4 Someday")
    labels = {c.meta["email_label"] for c in M.email_captures()}
    assert labels == {"1 Action", "4 Someday"}


def test_email_adapter_registers_on_gtd_ingest_port():
    from skos import adapters
    reg = adapters._adapters()
    assert "email" in reg
    from skos.gtd_ingest import registry
    assert "email" in registry.available_for("gtd-ingest")
