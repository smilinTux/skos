"""Tests for the `skos operator` CLI and its explain / observe / act contract.

Hermetic: the health probe and the act runner are both injectable, so nothing
here touches a live skos, the cron ledger, the GTD store, systemd, or a network.
The contract shape is asserted directly and (when the optional sibling skcapstone
is installed) compared byte-for-byte against Atlas's skos adapter.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skos import operator_probe as op
from skos.cli import app

runner = CliRunner()


# --- explain -----------------------------------------------------------------


def test_explain_shape_matches_contract():
    c = op.explain()
    assert c["kinds"] == ["scheduler", "gtd", "watchdog"]
    assert c["conditions"] == [
        "SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog",
    ]
    names = [a["name"] for a in c["actions"]]
    assert names == ["restart_service", "replay_errors"]
    for a in c["actions"]:
        # Every action carries the full metadata standard.
        assert set(a) == {"name", "standard", "reversible", "blast_radius",
                          "runbook", "kedb_refs"}
        assert a["standard"] is True
        assert a["reversible"] is True
        assert a["blast_radius"] == "low"
        assert isinstance(a["kedb_refs"], list)
    runbooks = {a["name"]: a["runbook"] for a in c["actions"]}
    assert runbooks["restart_service"] == "restart the skscheduler service"
    assert runbooks["replay_errors"] == "replay the skos error-recovery queue"


def test_cli_explain_emits_contract_json():
    r = runner.invoke(app, ["operator", "explain"])
    assert r.exit_code == 0, r.output
    c = json.loads(r.output)
    assert c["kinds"] == ["scheduler", "gtd", "watchdog"]
    assert c["conditions"] == [
        "SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog",
    ]
    assert [a["name"] for a in c["actions"]] == ["restart_service", "replay_errors"]


def test_explain_superset_of_adapter():
    """explain() must carry every condition/kind Atlas's skos_adapter declares
    (a real drift check on the shared surface).

    Asserted as a superset rather than an equality on purpose: the sibling is an
    optional, separately-versioned install, so a checkout predating skcapstone's
    WD-11 follow-up still declares only the first two conditions. A superset
    passes against both, an equality would be red for an unrelated reason.
    Actions must match exactly either way, and the two watchdog conditions must
    always be present here."""
    skos_adapter = pytest.importorskip(
        "skcapstone.operator_seat.skos_adapter",
        reason="optional sibling skcapstone not installed",
    )
    c = op.explain()
    ac = skos_adapter.skos_explain()
    assert set(ac["conditions"]) <= set(c["conditions"])
    assert set(ac["kinds"]) <= set(c["kinds"])
    assert c["actions"] == ac["actions"]
    assert {"WatchdogDigestFresh", "GradingBacklog"} <= set(c["conditions"])


def test_manifest_operator_block_matches_this_facet():
    """The repo-local half of skcapstone's cross-repo drift guards
    (tests/operator_seat/test_manifest_adapter_conformance.py and
    test_manifest_adapter.py, which compare the SHIPPED manifest against
    skos_adapter.CONDITIONS). Those two importorskip skos, so they go quiet in a
    bare CI; this one always runs, and catches the manifest drifting from the
    facet the moment it happens rather than in another repo's suite."""
    from skos.skworld_manifest import skos_module_manifest

    operator = skos_module_manifest("http://x/")["operator"]
    assert operator["conditions"] == op.CONDITIONS
    assert operator["proposedStandardActions"] == [
        a["name"] for a in op._ACTIONS if a.get("standard") and a.get("reversible")
    ]


def test_grading_backlog_is_declared_problem_when_true():
    """GradingBacklog is the one PROBLEM-when-True condition here: a backlog
    EXISTS when it reads True, the inverse of the other three health types.
    skcapstone's operator loop unions this optional module-level set across
    adapters; without the declaration the brief reads the condition upside down,
    firing exactly when grading is healthy."""
    assert op.PROBLEM_WHEN_TRUE == frozenset({"GradingBacklog"})
    assert op.PROBLEM_WHEN_TRUE <= set(op.CONDITIONS)


# --- observe -----------------------------------------------------------------


def _observe_types(doc):
    return [(c["type"], c["status"], c["object"]) for c in doc["conditions"]]


def test_observe_healthy_via_injected_probe():
    """All four healthy. Note GradingBacklog reads "False" when healthy: it is
    the problem-when-True condition (test_grading_backlog_is_declared_problem_
    when_true), so "no backlog observed" is False, not True."""
    doc = op.observe(lambda: {
        "scheduler_alive": True, "gtd_draining": True,
        "digest_fresh": True, "grading_backlog": False,
    })
    assert _observe_types(doc) == [
        ("SchedulerAlive", "True", "skscheduler"),
        ("GtdSinkDraining", "True", "gtd-sink"),
        ("WatchdogDigestFresh", "True", "watchdog-digest"),
        ("GradingBacklog", "False", "grading-loop"),
    ]


def test_observe_scheduler_firing():
    doc = op.observe(lambda: {"scheduler_alive": False, "gtd_draining": True})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["SchedulerAlive"] == "False"
    assert by["GtdSinkDraining"] == "True"


def test_observe_gtd_sink_firing():
    doc = op.observe(lambda: {"scheduler_alive": True, "gtd_draining": False})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["SchedulerAlive"] == "True"
    assert by["GtdSinkDraining"] == "False"


def test_observe_digest_stale_firing():
    doc = op.observe(lambda: {"digest_fresh": False})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["WatchdogDigestFresh"] == "False"
    # untouched keys still default healthy, per observe()'s own .get(..., True)
    assert by["SchedulerAlive"] == "True"


def test_observe_grading_backlog_firing():
    doc = op.observe(lambda: {"grading_backlog": True})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["GradingBacklog"] == "True"


def test_observe_watchdog_halves_default_to_unknown_never_healthy():
    """A probe that says nothing about the watchdog must NOT read as healthy.

    The scheduler/GTD halves fail safe to healthy (they always have, and an
    unreachable skos there is not evidence of a fault). The watchdog halves must
    not: silence about the narrator is exactly the case WatchdogDigestFresh
    exists to catch, so an absent key reads Unknown. If this ever regresses to
    `.get(..., True)` a dead watchdog reports green."""
    doc = op.observe(lambda: {"scheduler_alive": True, "gtd_draining": True})
    by = {c["type"]: c["status"] for c in doc["conditions"]}
    assert by["WatchdogDigestFresh"] == "Unknown"
    assert by["GradingBacklog"] == "Unknown"
    assert by["SchedulerAlive"] == "True"
    assert by["GtdSinkDraining"] == "True"


def test_observe_shared_conditions_match_adapter():
    """op.observe() is byte-compatible with Atlas's skos_adapter.skos_observe on
    every condition the adapter knows about, including the tri-state Unknown and
    the GradingBacklog polarity.

    Superset rather than equality for the same reason as
    test_explain_superset_of_adapter: the sibling is optional and separately
    versioned, so a checkout predating skcapstone's WD-11 follow-up only
    declares the first two. Every condition it DOES declare must match exactly."""
    skos_adapter = pytest.importorskip(
        "skcapstone.operator_seat.skos_adapter",
        reason="optional sibling skcapstone not installed",
    )
    healthy = {
        "scheduler_alive": True, "gtd_draining": True,
        "digest_fresh": True, "grading_backlog": False,
    }
    ours = {c["type"]: c for c in op.observe(lambda: healthy)["conditions"]}
    theirs = {c["type"]: c for c in skos_adapter.skos_observe(lambda: healthy)["conditions"]}
    assert set(theirs) <= set(ours)
    for cond_type, cond in theirs.items():
        assert ours[cond_type] == cond


def test_cli_observe_emits_conditions_json(monkeypatch):
    monkeypatch.setattr(
        op, "_default_probe",
        lambda: {
            "scheduler_alive": True, "gtd_draining": True,
            "digest_fresh": True, "grading_backlog": False,
        },
    )
    r = runner.invoke(app, ["operator", "observe"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert {c["type"] for c in doc["conditions"]} == {
        "SchedulerAlive", "GtdSinkDraining", "WatchdogDigestFresh", "GradingBacklog",
    }


# --- default probe: real signals, fail safe ----------------------------------


def test_default_probe_fails_safe_when_unreachable(tmp_path, monkeypatch):
    """No ledger + empty GTD store -> healthy (fail safe), never a false alarm.

    The two halves fail differently and this locks both. Scheduler and GTD fail
    SAFE to healthy. The watchdog halves must NOT: with no digest ever published
    they read UNKNOWN (None), never healthy, so an unreachable watchdog can
    never be mistaken for a fresh one."""
    monkeypatch.setenv("SKOS_CRON_LEDGER", str(tmp_path / "nope.jsonl"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "empty-gtd"))
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    st = op._default_probe()
    assert st["scheduler_alive"] is True
    assert st["gtd_draining"] is True
    assert st["digest_fresh"] is None
    assert st["grading_backlog"] is None


def test_scheduler_alive_pure_rule():
    assert op._scheduler_alive(None) is True          # unknown -> safe (alive)
    assert op._scheduler_alive(60.0) is True           # fresh run -> alive
    assert op._scheduler_alive(op._SCHEDULER_MAX_AGE_S + 1) is False  # stale -> firing


def test_sink_draining_pure_rule():
    assert op._sink_draining(0) is True
    assert op._sink_draining(1) is False
    assert op._sink_draining(None) is True   # could not look -> safe (draining)


def test_quarantine_backlog_makes_sink_fire(tmp_path, monkeypatch):
    gtd = tmp_path / "gtd"
    gtd.mkdir()
    (gtd / "inbox.json.corrupt-20260101T000000Z").write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("SK_GTD_DIR", str(gtd))
    assert op._count_quarantine(op._gtd_dir()) == 1
    assert op._sink_draining(op._count_quarantine(op._gtd_dir())) is False


# --- observation is read-only, in the filesystem sense too ---------------------


def test_probe_never_creates_the_gtd_store(tmp_path, monkeypatch):
    """The bug this test exists for: `_gtd_dir()` used to call
    `gtd_ingest.gtd_dir()`, which CREATES the store tree (and, through
    skcapstone's resolver, seeds six JSON files). Merely asking "is the GTD
    sink draining" therefore brought the store into existence, on a developer
    machine or in CI, as a side effect of a read.

    This does not mock mkdir. It points the store env at a path that genuinely
    does not exist, runs the whole `_default_probe()` (the same entry point
    `/status.json` reaches with no env isolation), and asserts with
    `Path.exists()` that the path STILL does not exist afterwards.
    """
    store = tmp_path / "never" / "created" / "gtd"
    monkeypatch.setenv("SK_GTD_DIR", str(store))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "no-such-home"))
    monkeypatch.setenv("SKOS_CRON_LEDGER", str(tmp_path / "no-ledger.jsonl"))
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))

    assert op._gtd_dir() == store
    assert not store.exists()
    assert not (tmp_path / "never").exists()

    st = op._default_probe()                      # must not raise ...

    assert not store.exists()                     # ... and must not have created it
    assert not (tmp_path / "never").exists()
    assert not (tmp_path / "no-such-home").exists()
    # ... and reads as unknown, never as a confident "no backlog".
    assert st["quarantine_depth"] is None
    assert st["gtd_draining"] is True             # fail safe: no false alarm either


def test_gtd_dir_falls_back_to_skcapstone_home_without_creating(tmp_path, monkeypatch):
    """Resolution order without SK_GTD_DIR and without the optional sibling:
    `<SKCAPSTONE_HOME>/coordination/gtd`, mirroring `gtd_ingest.gtd_dir`'s own
    fallback, and still creating nothing."""
    import sys

    monkeypatch.delenv("SK_GTD_DIR", raising=False)
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "home"))
    # simulate the sibling being absent: `from ... import _shared_root` raises
    monkeypatch.setitem(sys.modules, "skcapstone.mcp_tools._helpers", None)

    assert op._gtd_dir() == tmp_path / "home" / "coordination" / "gtd"
    assert not (tmp_path / "home").exists()


def test_count_quarantine_is_unknown_not_zero_when_it_cannot_look(tmp_path):
    """An absent or unresolvable store is UNKNOWN, not an observed empty
    backlog: absence of the store is not evidence the sink is fine."""
    assert op._count_quarantine(None) is None
    assert op._count_quarantine(tmp_path / "absent") is None
    (tmp_path / "present").mkdir()
    assert op._count_quarantine(tmp_path / "present") == 0   # looked, found none


# --- WatchdogDigestFresh -------------------------------------------------------


def test_digest_fresh_pure_rule():
    """Tri-state, and note the first line: an unknown age is UNKNOWN, not fresh.

    This is the one rule in this module that deliberately does NOT fail safe.
    Every way the age comes back None (no digest on disk, an unreadable one, an
    unresolvable watchdog root) is itself the quiet-narrator case, so answering
    "fresh" would report the watchdog healthy on the strength of never having
    looked at it."""
    assert op._digest_fresh(None) is None                        # could not look -> Unknown
    assert op._digest_fresh(60.0) is True                        # just published -> fresh
    assert op._digest_fresh(op._DIGEST_MAX_AGE_S) is True        # exactly at the edge
    assert op._digest_fresh(op._DIGEST_MAX_AGE_S + 1) is False   # stale -> firing


def test_digest_window_matches_scheduler_staleness_window():
    """26h, the same window the scheduler probe uses. Both run daily, so the
    margin before "quiet" is real rather than a hair trigger."""
    assert op._DIGEST_MAX_AGE_S == op._SCHEDULER_MAX_AGE_S == 26 * 3600


def test_digest_age_reads_a_real_published_digest(tmp_path, monkeypatch):
    """A real digest just published reads back as fresh, through the real
    publish path (publish_digest's atomic replace), not a hand-built file."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(
        {"date": "2026-08-16", "headline": "quiet", "problems": [], "notable": [],
         "info_counts": {}, "per_source": {}},
        "# hello\n",
    )
    age = op._digest_age_s(op._read_digest())
    assert age is not None
    assert age < 5.0
    assert op._digest_fresh(age) is True


def test_digest_age_prefers_the_window_over_mtime(tmp_path, monkeypatch):
    """The reason age is not just the file mtime: a digest covering an old
    window, re-published (rewritten) seconds ago, must still read STALE. Dating
    it by mtime would call a digest fresh because its bytes were touched, which
    is exactly how a stalled narrator hides.

    Both window spellings are checked: `until` is what skcapstone's skos_adapter
    reads, `to` is what `Window.to_dict` actually serialises into the shipped
    digests, and this probe accepts either."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    old = datetime.now(timezone.utc) - timedelta(hours=40)
    for key in ("to", "until"):
        publish_digest(
            {"date": "2026-08-16", "headline": "stale",
             "window": {"from": "2026-08-14T00:00:00Z", key: old.isoformat()},
             "problems": [], "notable": [], "info_counts": {}, "per_source": {}},
            "# md\n",
        )
        age = op._digest_age_s(op._read_digest())
        assert age is not None
        assert age > 39 * 3600, f"window key {key!r} was ignored: age={age}"
        assert op._digest_fresh(age) is False, f"window key {key!r} was ignored"


def test_digest_age_falls_back_to_mtime_without_a_usable_window(tmp_path, monkeypatch):
    """No window, an empty one, or an unparseable stamp: fall back to the file
    mtime rather than giving up. The digests skos publishes without a Window
    carry `window: {}`."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    for window in ({}, {"to": "not-a-timestamp"}, {"to": None}):
        publish_digest(
            {"date": "2026-08-16", "headline": "x", "window": window,
             "problems": [], "notable": [], "info_counts": {}, "per_source": {}},
            "# md\n",
        )
        age = op._digest_age_s(op._read_digest())
        assert age is not None and age < 5.0, f"window {window!r} lost the mtime fallback"
        assert op._digest_fresh(age) is True


def test_digest_unknown_when_never_published(tmp_path, monkeypatch):
    """The watchdog home resolves fine, nothing has ever been published there.
    UNKNOWN, and critically NOT fresh: a missing digest must never read healthy."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    assert op._read_digest() is None
    assert op._digest_age_s(op._read_digest()) is None
    assert op._digest_fresh(op._digest_age_s(op._read_digest())) is None


def test_digest_unknown_when_corrupt_or_not_an_object(tmp_path, monkeypatch):
    """Unparseable JSON, and valid JSON that is not an object, both read UNKNOWN.
    A digest that is a bare list has no window and no event buckets; treating it
    as an empty dict would silently report "fresh, no backlog"."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    target = tmp_path / "watchdog" / "digests" / "latest" / "digest.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    for payload in ("{not valid json", "[1, 2, 3]", '"a string"', "null"):
        target.write_text(payload, encoding="utf-8")
        assert op._read_digest() is None, f"{payload!r} should not parse as a digest"
        assert op._digest_fresh(op._digest_age_s(op._read_digest())) is None


def test_digest_unknown_when_it_cannot_look(tmp_path, monkeypatch):
    """A real read failure (a permissions blip, an unresolvable watchdog home)
    is a probe failure, not an observation. Still UNKNOWN, never healthy."""
    monkeypatch.setattr(op, "_watchdog_home", lambda: None)
    assert op._digest_path() is None
    assert op._read_digest() is None

    def _boom():
        raise PermissionError("simulated: no read access")

    monkeypatch.setattr(op, "_digest_path", _boom)
    assert op._read_digest() is None


def test_watchdog_home_precedence_and_empty_override(tmp_path, monkeypatch):
    """SK_WATCHDOG_DIR > <SKCAPSTONE_HOME>/watchdog > ~/.skcapstone/watchdog,
    mirroring `watchdog_home()` by hand. An empty/whitespace override falls back
    to the default rather than resolving against the cwd."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "explicit"))
    assert op._watchdog_home() == tmp_path / "explicit"

    monkeypatch.setenv("SK_WATCHDOG_DIR", "   ")
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "home"))
    assert op._watchdog_home() == tmp_path / "home" / "watchdog"

    monkeypatch.delenv("SK_WATCHDOG_DIR", raising=False)
    monkeypatch.setenv("SKCAPSTONE_HOME", "")
    assert op._watchdog_home() == Path.home() / ".skcapstone" / "watchdog"


def test_probe_never_creates_the_watchdog_root(tmp_path, monkeypatch):
    """Observation is read-only in the filesystem sense too, and this is why
    `_watchdog_home` re-implements `watchdog_home()`'s precedence by hand:
    `watchdog_home()`, `publish.digests_dir` and `publish.latest_dir` all mkdir.
    An operator that creates the store it is only supposed to look at
    manufactures the state it reports on, and a freshly-created empty digests
    dir is indistinguishable from a watchdog that has never run.

    This does not mock mkdir. It points every root at paths that genuinely do
    not exist, runs the whole `_default_probe()` (the entry point `/status.json`
    reaches with no env isolation at all), and asserts with `Path.exists()` that
    nothing was created."""
    root = tmp_path / "never" / "created" / "watchdog"
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(root))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "no-such-gtd"))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "no-such-home"))
    monkeypatch.setenv("SKOS_CRON_LEDGER", str(tmp_path / "no-ledger.jsonl"))

    assert op._digest_path() == root / "digests" / "latest" / "digest.json"
    assert not root.exists()

    st = op._default_probe()                      # must not raise ...

    assert not root.exists()                      # ... and must not have created it
    assert not (tmp_path / "never").exists()
    assert not (tmp_path / "no-such-home").exists()
    assert not (tmp_path / "no-such-gtd").exists()
    # ... and reads UNKNOWN, never a confident "fresh, no backlog".
    assert st["digest_fresh"] is None
    assert st["grading_backlog"] is None


# --- GradingBacklog -------------------------------------------------------------


def _grading_event(*, kind="GradingGap", meta, source="grading"):
    return {
        "ts": "2026-08-16T06:00:00Z", "source": source, "kind": kind,
        "object": "lumina-replies", "severity": "notable",
        "summary": "reply grade(s) skipped this run.",
        "link": {"uri": "", "http": ""}, "ref": "grading:gap:2026-08-16",
        "meta": meta,
    }


def _digest_with(*events, bucket="notable"):
    d = {"date": "2026-08-16", "headline": "x", "problems": [], "notable": [],
         "info_counts": {}, "per_source": {}}
    d[bucket] = list(events)
    return d


def test_grading_backlog_positive_control_fires(tmp_path, monkeypatch):
    """The positive control for every negative case below: a GradingGap whose
    meta.budget_exhausted is a real JSON true DOES fire, through the real
    publish path. Without this, the negatives could all be passing because the
    check never fires at all."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(
        _digest_with(_grading_event(meta={"skipped": 3, "budget_exhausted": True})), "# md\n"
    )
    assert op._grading_backlog(op._read_digest()) is True


def test_grading_backlog_fires_from_the_problems_bucket_too(tmp_path, monkeypatch):
    """Both buckets assemble_digest carries are read; only info events (which
    are counted, never carried) are out of reach."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(
        _digest_with(_grading_event(meta={"budget_exhausted": True}), bucket="problems"),
        "# md\n",
    )
    assert op._grading_backlog(op._read_digest()) is True


def test_grading_availability_skips_stay_quiet(tmp_path, monkeypatch):
    """The whole point of the narrow check. A skgateway outage or one
    unparseable reply emits the SAME GradingGap kind, and that is grader
    AVAILABILITY, not backlog. Widening this to "any GradingGap" would turn
    every gateway blip into a backlog alarm and make the real signal worthless.

    The string "false" case is not hypothetical padding: a digest is JSON off
    disk, and `bool("false")` is True in Python, so a truthiness check here
    fires on a value that says the opposite of what it means. That is why the
    implementation tests `is True`."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    quiet_metas = [
        {"skipped": 1, "budget_exhausted": False},   # explicitly not a backlog
        {"skipped": 1},                              # flag absent entirely
        {"skipped": 1, "budget_exhausted": "false"},  # truthy string, means False
        {"skipped": 1, "budget_exhausted": "true"},   # a string is not a JSON bool
        {"skipped": 1, "budget_exhausted": 1},        # 1 is not True
        {"skipped": 1, "budget_exhausted": None},
    ]
    for meta in quiet_metas:
        publish_digest(_digest_with(_grading_event(meta=meta)), "# md\n")
        assert op._grading_backlog(op._read_digest()) is False, f"fired on meta={meta!r}"


def test_grading_other_kinds_stay_quiet_even_carrying_the_flag(tmp_path, monkeypatch):
    """Only the GradingGap kind means backlog. A SourceUnavailable event that
    happens to carry budget_exhausted: True in its meta is a source being down,
    and must not fire this condition. Nor must a malformed meta."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(
        _digest_with(
            _grading_event(kind="SourceUnavailable", meta={"budget_exhausted": True}),
            _grading_event(kind="GradingGap", meta="not-a-dict"),
        ),
        "# md\n",
    )
    assert op._grading_backlog(op._read_digest()) is False


def test_grading_backlog_unknown_when_there_is_no_digest(tmp_path, monkeypatch):
    """No readable digest is UNKNOWN, not a confident "no backlog". An absent
    digest is WatchdogDigestFresh's alarm to raise, and claiming a clean grading
    loop from a file nobody could read would be inventing a reading."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    assert op._grading_backlog(op._read_digest()) is None
    assert op._grading_backlog(None) is None


def test_grading_backlog_quiet_on_a_digest_with_no_events(tmp_path, monkeypatch):
    """A digest that IS readable and carries no GradingGap is a real
    observation: no backlog. False, not None."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    from skos.watchdog.publish import publish_digest

    publish_digest(_digest_with(), "# md\n")
    assert op._grading_backlog(op._read_digest()) is False


def test_grading_backlog_survives_malformed_event_buckets(tmp_path, monkeypatch):
    """Buckets that are not lists, and non-dict entries inside them, are skipped
    rather than raising: the probe must never take the observe verb down."""
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    target = tmp_path / "watchdog" / "digests" / "latest" / "digest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"problems": "not-a-list",
                    "notable": ["a string", None,
                                {"kind": "GradingGap",
                                 "meta": {"budget_exhausted": True}}]}),
        encoding="utf-8",
    )
    assert op._grading_backlog(op._read_digest()) is True


# --- act ---------------------------------------------------------------------


def test_act_restart_service_via_runner():
    seen = {}

    def fake_runner(cmd):
        seen["cmd"] = cmd
        return {"ok": True, "returncode": 0}

    out = op.act("restart_service", runner=fake_runner)
    assert out["performed"] is True
    assert out["action"] == "restart_service"
    assert seen["cmd"] == ["systemctl", "--user", "restart", "skscheduler.service"]
    assert out["result"]["ok"] is True


def test_act_restart_service_unit_override():
    seen = {}
    op.act("restart_service", runner=lambda cmd: seen.setdefault("cmd", cmd) or {},
           unit="my-scheduler.service")
    assert seen["cmd"] == ["systemctl", "--user", "restart", "my-scheduler.service"]


def test_act_replay_errors_via_runner():
    seen = {}
    out = op.act("replay_errors", runner=lambda cmd: seen.setdefault("cmd", cmd) or {"ok": True})
    assert out["performed"] is True
    assert seen["cmd"] == ["skos", "gtd", "replay-errors"]


def test_act_unknown_action_refused():
    with pytest.raises(ValueError):
        op.act("nuke-everything", runner=lambda cmd: {})


def test_cli_act_unknown_action_exits_nonzero():
    r = runner.invoke(app, ["operator", "act", "bogus"])
    assert r.exit_code != 0
    assert "unknown skos operator action" in (r.output + str(r.exception or ""))


def test_cli_act_restart_service(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        op, "_default_runner",
        lambda cmd: seen.setdefault("cmd", cmd) or {"ok": True, "returncode": 0},
    )
    r = runner.invoke(app, ["operator", "act", "restart_service"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["performed"] is True
    assert seen["cmd"] == ["systemctl", "--user", "restart", "skscheduler.service"]


# --- replay actuation is a real, reversible gtd command ----------------------


def test_gtd_replay_errors_command_stages_backlog(tmp_path, monkeypatch):
    from skos import gtd_ingest

    gtd = tmp_path / "gtd"
    gtd.mkdir()
    corrupt = gtd / "next-actions.json.corrupt-20260101T000000Z"
    corrupt.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("SK_GTD_DIR", str(gtd))

    moved = gtd_ingest.replay_quarantine()
    assert moved == ["next-actions.json.corrupt-20260101T000000Z"]
    # reversible: the file is relocated, never deleted
    assert not corrupt.exists()
    staged = gtd / gtd_ingest.REPLAY_DIRNAME / corrupt.name
    assert staged.exists()
    # backlog is now clear -> the sink reads as draining again
    assert gtd_ingest.quarantine_backlog() == []


def test_cli_gtd_replay_errors_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    r = runner.invoke(app, ["gtd", "replay-errors"])
    assert r.exit_code == 0, r.output
    assert "clean" in r.output
