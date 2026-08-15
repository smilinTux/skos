"""WD-8: watchdog findings -> unified GTD, behind SKWATCHDOG_GTD.

Every test here is hermetic in both directions: `SK_GTD_DIR` points at a
throwaway tmp store so nothing ever reaches Chef's real GTD, and the digests
are hand-built dicts (or driven through an ISOLATED AdapterRegistry of fake
sources) so nothing ever reads live fleet state. The one test that exercises
the real fleet freeze file writes it under the hermetic `SKFLEET_ROOT` the
suite-wide conftest fixture already points at tmp_path.
"""
import json

import pytest

from skos.gtd_ingest import gtd_dir
from skos.watchdog import gtd as gtd_mod
from skos.watchdog.events import WatchdogEvent, WatchdogLink
from skos.watchdog.port import AdapterRegistry, WatchdogSourceAdapter
from skos.watchdog.gtd import (
    FLAG, GTD_SOURCE, META_KEY, file_findings, fleet_frozen, gtd_enabled,
    item_text, source_ref_for,
)


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    """A throwaway GTD store that does NOT exist yet, so a test can prove the
    flag-off path never even creates it."""
    d = tmp_path / "gtd"
    monkeypatch.setenv("SK_GTD_DIR", str(d))
    return d


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


# NOTE: no autouse "unfrozen" stub is needed. tests/conftest.py's `_hermetic_fleet`
# already points SKFLEET_ROOT at an empty tmp tree for every test, so the real
# `fleet_frozen()` reads "no freeze file, not frozen" without ever consulting the
# live ~/.skcapstone/fleet. The freeze tests below set their own state explicitly.


def _event(*, source="scheduler", kind="JobStalled", object="ops-report",
           severity="problem", summary="job ops-report has not run in 3 days.",
           ref="scheduler:ops-report:stale:2026-08-10",
           uri="skworld://skos/watchdog/scheduler/ops-report",
           http="https://atlas.skworld.io/jobs/ops-report"):
    return {"ts": "2026-08-10T07:45:00Z", "source": source, "kind": kind,
            "object": object, "severity": severity, "summary": summary,
            "link": {"uri": uri, "http": http}, "ref": ref, "meta": {}}


def _digest(problems=(), notable=(), sources=("scheduler",), ok=True):
    return {
        "date": "2026-08-10",
        "window": {"from": "2026-08-09T07:45:00Z", "to": "2026-08-10T07:45:00Z"},
        "headline": "1 problem, 0 notable items, 0 quiet info events.",
        "problems": list(problems),
        "notable": list(notable),
        "info_counts": {},
        "per_source": {name: {"ok": ok, "events": 1, "cursor": "2026-08-10T07:45:00Z"}
                       for name in sources},
    }


def _load(name):
    p = gtd_dir() / name
    return json.loads(p.read_text()) if p.exists() else []


def _all_items():
    out = []
    for name in ("inbox.json", "next-actions.json", "projects.json",
                 "waiting-for.json", "someday-maybe.json", "archive.json"):
        out.extend(_load(name))
    return out


# ── the flag: off is invisible ──────────────────────────────────────────────

def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert gtd_enabled() is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_only_explicit_truthy_values_enable_filing(monkeypatch, value):
    monkeypatch.setenv(FLAG, value)
    assert gtd_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_values_enable_filing(monkeypatch, value):
    monkeypatch.setenv(FLAG, value)
    assert gtd_enabled() is True


def test_flag_off_writes_nothing_and_never_touches_the_store(store_dir, monkeypatch):
    """PROOF of inertness: with the flag off, filing does not even RESOLVE the
    GTD dir (resolving it would create it), let alone write an item."""
    monkeypatch.delenv(FLAG, raising=False)
    report = file_findings(_digest(problems=[_event()]))
    assert report == {"enabled": False, "skipped": "flag-off",
                      "filed": [], "completed": [], "unchanged": 0}
    assert not store_dir.exists()


# ── the stable source_ref ───────────────────────────────────────────────────

def test_source_ref_is_stable_across_days():
    """The whole point of the scheme: the same real-world finding on two
    different days carries two different WatchdogEvent.refs (they bake in the
    date) but ONE GTD source_ref."""
    monday = _event(ref="scheduler:ops-report:stale:2026-08-10",
                    summary="job ops-report has not run in 3 days.")
    tuesday = _event(ref="scheduler:ops-report:stale:2026-08-11",
                     summary="job ops-report has not run in 4 days.")
    assert monday["ref"] != tuesday["ref"]
    assert source_ref_for(monday) == source_ref_for(tuesday) == "scheduler:JobStalled:ops-report"


def test_source_ref_carries_no_date_or_timestamp():
    ref = source_ref_for(_event())
    assert "2026" not in ref and "T07:45" not in ref


def test_different_findings_get_different_refs():
    failed = source_ref_for(_event(kind="JobFailed"))
    stalled = source_ref_for(_event(kind="JobStalled"))
    other_job = source_ref_for(_event(object="skingest-daily"))
    assert len({failed, stalled, other_job}) == 3


def test_still_stalled_tomorrow_is_the_same_item_not_a_second_one(store_dir, flag_on):
    file_findings(_digest(problems=[_event(ref="scheduler:ops-report:stale:2026-08-10")]))
    report = file_findings(_digest(problems=[_event(
        ref="scheduler:ops-report:stale:2026-08-11",
        summary="job ops-report has not run in 4 days.")]))
    items = [it for it in _all_items() if it["source"] == GTD_SOURCE]
    assert len(items) == 1
    assert report["unchanged"] == 1


def test_two_sightings_in_one_window_collapse_to_one_item(store_dir, flag_on):
    dupes = [_event(ref="fleet:a"), _event(ref="fleet:b", summary="again.")]
    report = file_findings(_digest(problems=dupes))
    assert len(report["filed"]) == 1
    assert len([it for it in _all_items() if it["source"] == GTD_SOURCE]) == 1


# ── filing ──────────────────────────────────────────────────────────────────

def test_a_problem_becomes_one_tracked_item_with_its_deep_link(store_dir, flag_on):
    report = file_findings(_digest(problems=[_event()]))
    assert [f["action"] for f in report["filed"]] == ["created"]

    items = _load("next-actions.json")
    assert len(items) == 1
    item = items[0]
    assert item["source"] == GTD_SOURCE
    assert item["source_ref"] == "scheduler:JobStalled:ops-report"
    assert item["status"] == "next"
    block = item[META_KEY]
    assert block["link"] == {"uri": "skworld://skos/watchdog/scheduler/ops-report",
                             "http": "https://atlas.skworld.io/jobs/ops-report"}
    assert block["source"] == "scheduler" and block["object"] == "ops-report"


def test_item_text_names_the_finding_and_carries_no_banned_dashes():
    text = item_text(_event(kind="Crash — loop"))
    assert "—" not in text and "–" not in text
    assert "ops-report" in text and "scheduler" in text


def test_an_unchanged_finding_performs_no_write(store_dir, flag_on):
    """The idempotence property the whole polling design rests on: a second
    identical run must report `unchanged` and leave the item untouched.
    `updated_at` is only ever set by a real write, so its absence is the
    proof that nothing was rewritten."""
    file_findings(_digest(problems=[_event()]))
    before = json.dumps(_load("next-actions.json"), sort_keys=True)

    report = file_findings(_digest(problems=[_event()]))
    assert report["filed"] == [] and report["unchanged"] == 1
    after = json.dumps(_load("next-actions.json"), sort_keys=True)
    assert before == after
    assert "updated_at" not in _load("next-actions.json")[0]


def test_a_reworded_summary_does_not_churn_the_item(store_dir, flag_on):
    """A summary that counts days re-words itself every morning. That volatile
    text must not reach a field that would make every run look `updated`."""
    file_findings(_digest(problems=[_event(summary="has not run in 3 days.")]))
    first = _load("next-actions.json")[0][META_KEY]["summary"]

    report = file_findings(_digest(problems=[_event(summary="has not run in 9 days.")]))
    assert report["unchanged"] == 1
    assert _load("next-actions.json")[0][META_KEY]["summary"] == first


# ── severity discipline (the 2026-08-08 flood lesson) ───────────────────────

def test_notable_and_info_never_file(store_dir, flag_on):
    digest = _digest(problems=[], notable=[
        _event(severity="notable", kind="SourceUnavailable"),
        _event(severity="notable", kind="PrAging"),
    ])
    digest["info_counts"] = {"git": 42}
    report = file_findings(digest)
    assert report["filed"] == [] and report["completed"] == []
    assert _all_items() == []


def test_a_non_problem_smuggled_into_problems_is_still_refused(store_dir, flag_on):
    report = file_findings(_digest(problems=[_event(severity="notable")]))
    assert report["filed"] == []
    assert _all_items() == []


# ── freeze ──────────────────────────────────────────────────────────────────

def test_frozen_fleet_stands_all_writes_down(store_dir, flag_on, monkeypatch):
    monkeypatch.setattr(gtd_mod, "fleet_frozen", lambda: True)
    report = file_findings(_digest(problems=[_event()]))
    assert report["enabled"] is True and report["skipped"] == "frozen"
    assert report["filed"] == []
    assert not store_dir.exists()


@pytest.mark.needs_skcapstone
def test_fleet_frozen_reads_the_real_kill_switch(tmp_path, monkeypatch):
    """Mirrors the operator seat exactly: skcapstone.fleet.store.is_frozen over
    default_paths(), which honours SKFLEET_ROOT (the conftest already points it
    at tmp_path, so the live ~/.skcapstone/fleet is never read)."""
    root = tmp_path / "fleet"
    monkeypatch.setenv("SKFLEET_ROOT", str(root))
    objects = root / "objects"
    objects.mkdir(parents=True)

    assert fleet_frozen() is False
    (objects / "_freeze.json").write_text(json.dumps({"frozen": True, "reason": "test"}))
    assert fleet_frozen() is True
    (objects / "_freeze.json").write_text(json.dumps({"frozen": False}))
    assert fleet_frozen() is False


@pytest.mark.needs_skcapstone
def test_fleet_frozen_halts_when_the_switch_is_unreadable(monkeypatch):
    """The store's own rule: in doubt, halt actuation."""
    import skcapstone.fleet.store as store

    def _boom(paths):
        raise OSError("unreadable")

    monkeypatch.setattr(store, "is_frozen", _boom)
    assert fleet_frozen() is True


def test_fleet_frozen_is_false_without_a_fleet_control_plane(monkeypatch):
    """skos is standalone-capable: no skcapstone means no kill switch to
    respect, not a permanent stand-down."""
    import sys

    monkeypatch.setitem(sys.modules, "skcapstone.fleet.paths", None)
    assert fleet_frozen() is False


# ── auto-completion ─────────────────────────────────────────────────────────

def test_a_cleared_finding_is_completed_and_archived(store_dir, flag_on):
    file_findings(_digest(problems=[_event()]))
    assert len(_load("next-actions.json")) == 1

    report = file_findings(_digest(problems=[]))
    assert [c["source_ref"] for c in report["completed"]] == ["scheduler:JobStalled:ops-report"]
    assert _load("next-actions.json") == []
    archived = _load("archive.json")
    assert len(archived) == 1
    assert archived[0]["status"] == "done"
    assert archived[0]["completed_at"]


def test_a_finding_whose_source_is_unavailable_is_never_completed(store_dir, flag_on):
    """A source that failed to read proves nothing about whether its findings
    cleared, so its items must survive the run untouched."""
    file_findings(_digest(problems=[_event()]))
    report = file_findings(_digest(problems=[], ok=False))
    assert report["completed"] == []
    assert len(_load("next-actions.json")) == 1
    assert _load("archive.json") == []


def test_a_finding_whose_source_did_not_run_is_never_completed(store_dir, flag_on):
    file_findings(_digest(problems=[_event()]))
    report = file_findings(_digest(problems=[], sources=("git",)))
    assert report["completed"] == []
    assert len(_load("next-actions.json")) == 1


def test_a_returning_finding_resurrects_the_same_item(store_dir, flag_on):
    """Auto-completion is non-destructive: the stable source_ref finds the
    ARCHIVED item, so the same problem coming back reopens one item instead of
    opening a second one, with the stale completion stamp cleared."""
    file_findings(_digest(problems=[_event()]))
    original_id = _load("next-actions.json")[0]["id"]
    file_findings(_digest(problems=[]))
    assert _load("archive.json")[0]["id"] == original_id

    report = file_findings(_digest(problems=[_event()]))
    assert [f["action"] for f in report["filed"]] == ["updated"]
    assert _load("archive.json") == []
    revived = _load("next-actions.json")
    assert len(revived) == 1
    assert revived[0]["id"] == original_id
    assert revived[0]["status"] == "next"
    assert revived[0]["completed_at"] is None


def test_items_from_other_sources_are_never_touched(store_dir, flag_on):
    from skos.gtd_ingest import GtdCapture, capture

    capture(GtdCapture(text="pick up the truck title", source="manual",
                       source_ref="manual:dmv-1", status="next"))
    file_findings(_digest(problems=[_event()]))
    report = file_findings(_digest(problems=[]))
    assert [c["source_ref"] for c in report["completed"]] == ["scheduler:JobStalled:ops-report"]
    survivors = [it for it in _load("next-actions.json") if it["source"] == "manual"]
    assert len(survivors) == 1


# ── fail-safe ───────────────────────────────────────────────────────────────

def test_filing_never_raises_into_the_digest_run(store_dir, flag_on, monkeypatch):
    def _boom(cap):
        raise RuntimeError("simulated store outage")

    monkeypatch.setattr(gtd_mod, "upsert", _boom)
    report = file_findings(_digest(problems=[_event()]))
    assert report["filed"] == []
    assert any("simulated store outage" in e for e in report["errors"])


def test_one_bad_item_does_not_stop_the_rest(store_dir, flag_on, monkeypatch):
    real = gtd_mod.upsert

    def _selective(cap):
        if cap.source_ref.endswith("bad-job"):
            raise RuntimeError("nope")
        return real(cap)

    monkeypatch.setattr(gtd_mod, "upsert", _selective)
    report = file_findings(_digest(problems=[
        _event(object="bad-job"), _event(object="good-job")]))
    assert [f["source_ref"] for f in report["filed"]] == ["scheduler:JobStalled:good-job"]
    assert len(report["errors"]) == 1


# ── through the real pipeline (run.py) ──────────────────────────────────────

class _ProblemAdapter(WatchdogSourceAdapter):
    """A test-only source on an ISOLATED registry, never the shared one."""
    name = "scheduler"

    def collect(self, window):
        return [WatchdogEvent(
            ts=window.until, source=self.name, kind="JobStalled",
            object="ops-report", severity="problem",
            summary="job ops-report has not run in 3 days.",
            link=WatchdogLink(uri="skworld://skos/watchdog/scheduler/ops-report",
                              http="https://atlas.skworld.io/jobs/ops-report"),
            ref="scheduler:ops-report:stale:2026-08-10",
        )]


def _pipeline_run(monkeypatch, tmp_path, tag, *, flag):
    """One full run_digest_and_deliver against fake sources and throwaway
    stores. Returns (report, the exact published digest.json bytes)."""
    from skos.watchdog import deliver as dl, headline as hl
    from skos.watchdog.publish import latest_dir
    from skos.watchdog.run import run_digest_and_deliver

    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    monkeypatch.setattr(dl, "default_sender", lambda text: True)
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / f"watchdog-{tag}"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / f"gtd-{tag}"))
    if flag:
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)

    reg = AdapterRegistry()
    reg.register(_ProblemAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", registry=reg)
    return report, (latest_dir() / "digest.json").read_bytes()


def test_flag_off_through_the_full_pipeline_is_invisible(tmp_path, monkeypatch):
    """PROOF of requirement 1: flag off means no GTD store is even created,
    and the published digest is byte-identical to the flag-on one (filing
    changes tracked work, never the report)."""
    off_report, off_bytes = _pipeline_run(monkeypatch, tmp_path, "off", flag=False)
    on_report, on_bytes = _pipeline_run(monkeypatch, tmp_path, "on", flag=True)

    assert off_bytes == on_bytes
    assert off_report["gtd"]["skipped"] == "flag-off"
    assert off_report["gtd"]["enabled"] is False
    assert not (tmp_path / "gtd-off").exists()

    assert [f["action"] for f in on_report["gtd"]["filed"]] == ["created"]
    items = json.loads((tmp_path / "gtd-on" / "next-actions.json").read_text())
    assert items[0]["source_ref"] == "scheduler:JobStalled:ops-report"


def test_dry_run_files_nothing_even_with_the_flag_on(tmp_path, monkeypatch):
    from skos.watchdog import headline as hl
    from skos.watchdog.run import run_digest_and_deliver

    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv(FLAG, "1")

    reg = AdapterRegistry()
    reg.register(_ProblemAdapter)
    report = run_digest_and_deliver(now="2026-08-10T07:45:00Z", dry_run=True, registry=reg)
    assert report["gtd"] == {}
    assert not (tmp_path / "gtd").exists()
