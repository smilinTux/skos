"""Tests for the WD-6 email adapter (skos.watchdog.adapters.email).

NOTHING HERE READS A LIVE MAILBOX (card hard rule). The one real read
boundary, `_search_thread_ids`, is monkeypatched in every test by an autouse
fixture whose default raises, the account list is stubbed so no real address
is ever resolved, the gog keyring password is never touched (the operator env
file is pointed at a non-existent path AND `secret_env.ensure` is stubbed),
and `test_no_test_invokes_gog` proves the binary is never spawned.
"""
from __future__ import annotations

import json

import pytest

from skos import secret_env
from skos.watchdog.adapters import email as em
from skos.watchdog.adapters.email import EmailAdapter, EmailReadError
from skos.watchdog.port import Window, collect_safe

BOX_A = "boxa@gmail.com"
BOX_B = "boxb@gmail.com"

#: The real read boundary, captured before the autouse fixture replaces it.
#: The handful of tests that exercise the boundary itself call this and stub
#: `subprocess.run` instead, so gog is still never spawned.
_REAL_SEARCH = em._search_thread_ids


def _window(since="2026-08-09T12:00:00Z", until="2026-08-10T12:00:00Z"):
    return Window(since=since, until=until)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("SKOS_SCHEDULE_ENV", str(tmp_path / "does-not-exist.env"))
    monkeypatch.setattr(secret_env, "ensure", lambda name: None)
    monkeypatch.setattr(em, "_accounts", lambda: [])
    monkeypatch.setattr(em, "_search_thread_ids", lambda account, query, maxn: (
        _ for _ in ()).throw(AssertionError("a test reached the live mailbox")))
    yield


def _searcher(table):
    """table: {(account, label, is_new): [thread ids]}. `is_new` marks the
    windowed query (the one carrying `after:`)."""
    def _search(account, query, maxn):
        label = query.split('"')[1]
        return list(table.get((account, label, "after:" in query), []))
    return _search


# ---------------------------------------------------------- configuration ---

def test_no_configured_box_is_a_quiet_empty_run():
    assert EmailAdapter().collect(_window()) == []


# ------------------------------------------------------------- narration ----

def test_new_action_mail_is_notable_and_rolls_up_across_boxes(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A, BOX_B])
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({
        (BOX_A, "1 Action", True): ["t1", "t2"],
        (BOX_B, "1 Action", True): ["t3"],
        (BOX_A, "1 Action", False): ["t1", "t2", "t9"],
    }))
    events = EmailAdapter().collect(_window())
    new = [e for e in events if e.kind == "NewMailInLabel"]
    assert len(new) == 1
    ev = new[0]
    assert ev.source == "email"
    assert ev.object == "1 Action"
    assert ev.severity == "notable"
    assert ev.summary == "3 new in 1 Action across 2 boxes."
    assert ev.meta["by_account"] == {"boxa": 2, "boxb": 1}
    assert ev.link.http.startswith("https://mail.google.com/mail/u/boxa@gmail.com/#label/1+Action")
    assert ev.ref == "email:new:1-action:2026-08-10"


def test_new_mail_in_the_quiet_lanes_is_only_info(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({
        (BOX_A, "3 Read", True): ["t1"],
        (BOX_A, "4 Someday", True): ["t2"],
        (BOX_A, "2 Waiting", True): ["t3"],
    }))
    events = EmailAdapter().collect(_window())
    new = {e.object: e for e in events if e.kind == "NewMailInLabel"}
    assert set(new) == {"2 Waiting", "3 Read", "4 Someday"}
    assert {e.severity for e in new.values()} == {"info"}


def test_a_label_with_no_new_mail_produces_no_line(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({}))
    events = EmailAdapter().collect(_window())
    assert not [e for e in events if e.kind == "NewMailInLabel"]


def test_backlog_is_reported_for_the_active_lanes_even_at_zero(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({}))
    events = EmailAdapter().collect(_window())
    backlog = {e.object: e for e in events if e.kind == "MailBacklog"}
    assert set(backlog) == {"1 Action", "2 Waiting"}
    assert backlog["1 Action"].severity == "info"
    assert backlog["1 Action"].summary == "0 open in 1 Action across 1 box(es)."


def test_a_backlog_over_the_threshold_is_notable_but_never_a_problem(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    monkeypatch.setattr(em, "BACKLOG_NOTABLE", 3)
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({
        (BOX_A, "1 Action", False): ["t1", "t2", "t3", "t4"],
    }))
    events = EmailAdapter().collect(_window())
    backlog = [e for e in events if e.kind == "MailBacklog" and e.object == "1 Action"][0]
    assert backlog.severity == "notable"
    assert backlog.meta["open"] == 4
    assert not [e for e in events if e.severity == "problem"]


def test_nothing_from_email_is_ever_a_problem(monkeypatch):
    """Unread mail is a workload, never an incident: a `problem` files a GTD
    item (WD-8) and can escalate to a card (WD-9)."""
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    monkeypatch.setattr(em, "MAX_PER_LABEL", 200)
    many = [f"t{i}" for i in range(150)]
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({
        (BOX_A, label, flag): many for label, _a in em.LABELS for flag in (True, False)
    }))
    events = EmailAdapter().collect(_window())
    assert not [e for e in events if e.severity == "problem"]


def test_a_capped_count_is_reported_as_at_least(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    monkeypatch.setattr(em, "MAX_PER_LABEL", 2)
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({
        (BOX_A, "1 Action", True): ["t1", "t2"],
    }))
    ev = [e for e in EmailAdapter().collect(_window()) if e.kind == "NewMailInLabel"][0]
    assert ev.summary == "2+ new in 1 Action across 1 box."
    assert ev.meta["capped"] is True


# --------------------------------------------------------------- privacy ----

def test_the_read_boundary_returns_thread_ids_and_discards_everything_else(monkeypatch):
    """Subjects, senders and snippets are dropped where gog output becomes
    data, so none of them exists downstream to leak into a published line."""
    payload = json.dumps({"threads": [
        {"id": "t1", "subject": "your biopsy results", "from": "clinic@example.com",
         "snippet": "the results are in"},
        {"threadId": "t2", "subject": "settlement offer"},
    ]})

    class _R:
        stdout = payload

    monkeypatch.setattr(em.subprocess, "run", lambda cmd, **kw: _R())
    monkeypatch.setattr(em, "_gog_bin", lambda: "gog")
    ids = _REAL_SEARCH(BOX_A, 'label:"1 Action"', 100)
    assert ids == ["t1", "t2"]
    assert "biopsy" not in str(ids)
    assert "clinic@example.com" not in str(ids)


def test_no_subject_or_sender_reaches_a_published_event(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({
        (BOX_A, "1 Action", True): ["t1"],
    }))
    dumped = str([e.to_dict() for e in EmailAdapter().collect(_window())])
    # thread ids are opaque handles and DO ride along; content never does
    assert "t1" in dumped
    for content_ish in ("subject", "snippet", "body", "biopsy"):
        assert content_ish not in dumped.lower()


# -------------------------------------------------------------- read path ---

def test_the_query_is_positional_and_the_call_is_read_only(monkeypatch):
    seen = {}

    class _R:
        stdout = "{}"

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return _R()

    monkeypatch.setattr(em.subprocess, "run", _fake_run)
    monkeypatch.setattr(em, "_gog_bin", lambda: "/usr/bin/gog")
    _REAL_SEARCH(BOX_A, 'label:"1 Action" after:123', 50)
    cmd = seen["cmd"]
    assert cmd[:4] == ["/usr/bin/gog", "gmail", "search", 'label:"1 Action" after:123']
    assert "-q" not in cmd  # there is no -q flag on gog gmail search
    assert "--readonly" in cmd and "--no-input" in cmd and "-j" in cmd
    assert cmd[cmd.index("-a") + 1] == BOX_A


def test_the_windowed_query_carries_an_after_epoch(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    queries = []

    def _search(account, query, maxn):
        queries.append(query)
        return []

    monkeypatch.setattr(em, "_search_thread_ids", _search)
    EmailAdapter().collect(_window())
    windowed = [q for q in queries if "after:" in q]
    assert windowed  # every label gets a windowed query
    assert f"after:{em._epoch('2026-08-09T12:00:00Z')}" in windowed[0]
    assert all(q.startswith('label:"') for q in queries)


def test_all_four_c_labels_are_read(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])
    labels = []

    def _search(account, query, maxn):
        labels.append(query.split('"')[1])
        return []

    monkeypatch.setattr(em, "_search_thread_ids", _search)
    EmailAdapter().collect(_window())
    assert set(labels) == {"1 Action", "2 Waiting", "3 Read", "4 Someday"}


# ------------------------------------------------------------- fail safe ----

def test_an_unreadable_box_never_looks_like_an_empty_box(monkeypatch):
    """The trap this adapter avoids by not reusing skos.mail.list_threads,
    which swallows failures and returns []."""
    monkeypatch.setattr(em, "_gog_bin", lambda: "gog")

    def _boom(cmd, **kw):
        raise OSError("gog: no such file")

    monkeypatch.setattr(em.subprocess, "run", _boom)
    with pytest.raises(EmailReadError):
        _REAL_SEARCH(BOX_A, 'label:"1 Action"', 10)


def test_unparseable_gog_output_is_a_read_error_not_an_empty_mailbox(monkeypatch):
    class _R:
        stdout = "gog: token expired, run gog auth add"

    monkeypatch.setattr(em, "_gog_bin", lambda: "gog")
    monkeypatch.setattr(em.subprocess, "run", lambda cmd, **kw: _R())
    with pytest.raises(EmailReadError):
        _REAL_SEARCH(BOX_A, 'label:"1 Action"', 10)


def test_one_failing_box_gaps_alone_and_the_others_still_report(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A, BOX_B])
    good = _searcher({(BOX_A, "1 Action", True): ["t1"]})

    def _search(account, query, maxn):
        if account == BOX_B:
            raise EmailReadError("gog search failed for boxb")
        return good(account, query, maxn)

    monkeypatch.setattr(em, "_search_thread_ids", _search)
    events = EmailAdapter().collect(_window())
    gaps = [e for e in events if e.kind == "SourceUnavailable"]
    assert len(gaps) == 1
    assert gaps[0].source == "email:boxb"
    assert gaps[0].severity == "notable"
    new = [e for e in events if e.kind == "NewMailInLabel"][0]
    assert new.meta["by_account"] == {"boxa": 1}  # the failed box is not counted as zero


def test_every_box_failing_marks_the_whole_source_unavailable(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A, BOX_B])

    def _boom(account, query, maxn):
        raise EmailReadError("gog unreachable")

    monkeypatch.setattr(em, "_search_thread_ids", _boom)
    events = collect_safe(EmailAdapter(), _window())
    assert len(events) == 1
    assert events[0].kind == "SourceUnavailable"
    assert events[0].source == "email"


def test_a_spent_time_budget_reports_the_unread_boxes(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A, BOX_B])
    monkeypatch.setattr(em, "EMAIL_RUN_BUDGET_S", 0.0)
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({}))
    events = EmailAdapter().collect(_window())
    spent = [e for e in events if e.kind == "MailReadBudgetSpent"]
    assert len(spent) == 1
    assert spent[0].severity == "notable"
    assert spent[0].meta["skipped_boxes"] == 2
    assert not [e for e in events if e.kind == "MailBacklog"]  # no box read, no claim


def test_no_test_invokes_gog(monkeypatch):
    monkeypatch.setattr(em, "_accounts", lambda: [BOX_A])

    def _no_subprocess(*a, **kw):
        raise AssertionError("a test spawned the real gog binary")

    monkeypatch.setattr(em.subprocess, "run", _no_subprocess)
    monkeypatch.setattr(em, "_search_thread_ids", _searcher({}))
    events = EmailAdapter().collect(_window())
    assert all(e.source == "email" for e in events)


# -------------------------------------------------------------- registry ----

def test_email_is_registered_on_the_watchdog_source_port():
    from skos.watchdog.port import registry
    assert registry.lookup("watchdog-source", "email") is EmailAdapter


def test_the_gtd_email_adapter_is_a_different_capability(monkeypatch):
    """Same name, different port: `email` on gtd-source is the capture
    adapter, `email` on watchdog-source is this narrator. No clash."""
    from skos.watchdog.port import registry as wd_registry
    assert wd_registry.lookup("watchdog-source", "email") is EmailAdapter
    assert EmailAdapter.capability == "watchdog-source"


def test_load_all_registers_email():
    from skos.watchdog.adapters import load_all
    assert "email" in load_all()
