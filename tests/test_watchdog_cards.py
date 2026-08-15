"""WD-9: watchdog findings -> STAGED coord cards, behind SKWATCHDOG_CARDS.

HERMETIC IN BOTH DIRECTIONS, and this module is the one where that matters
most: a test that files a card onto Chef's real board would be a failure of
this whole card's premise. So every test here points `SKCAPSTONE_HOME` at a
throwaway tmp tree (the seam `cards.coord_home()` honors first), points
`SK_GTD_DIR` at another one, and drives hand-built digests or an ISOLATED
adapter registry so nothing reads live fleet state. On top of that, the
autouse `_never_the_real_board` fixture below counts the files in the REAL
`~/.skcapstone/coordination/tasks` before and after every single test and
fails the test if that count ever moves.

The board writes go through a `_FileBoard` double that mirrors the real
`Board.create_task` contract byte for byte in the way this module depends on
(one immutable `tasks/<id>-<slug>.json` per card), so the ledger scan under
test reads exactly what production would read. The tests that pin the REAL
`skcapstone` contract (the Task model accepts our payload, the real Board
writes it, an archived card is still a file) are marked `needs_skcapstone`.
"""
import json
import logging
from pathlib import Path

import pytest

from skos.watchdog import cards as cards_mod
from skos.watchdog.cards import (
    CARD_TAG, DAILY_BUDGET, EPIC_ID, EPIC_TITLE, FLAG, META_KEY, STAGED_TAGS,
    card_id_for, card_title, cards_enabled, coord_home, file_cards, read_ledger,
    repo_for, tasks_dir,
)
from skos.watchdog.events import WatchdogEvent, WatchdogLink
from skos.watchdog.gtd import FLAG as GTD_FLAG, source_ref_for
from skos.watchdog.port import AdapterRegistry, WatchdogSourceAdapter

REAL_BOARD_TASKS = Path.home() / ".skcapstone" / "coordination" / "tasks"


@pytest.fixture(autouse=True)
def _never_the_real_board():
    """The premise guard. Chef's real board must be untouched by this suite,
    so count it before and after every test."""
    def _count():
        return len(list(REAL_BOARD_TASKS.glob("*.json"))) if REAL_BOARD_TASKS.is_dir() else 0
    before = _count()
    yield
    assert _count() == before, "a test wrote to the REAL coordination board"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway skcapstone home that does NOT exist yet, so a test can
    prove the flag-off path never even creates it."""
    d = tmp_path / "skcapstone"
    monkeypatch.setenv("SKCAPSTONE_HOME", str(d))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    return d


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(GTD_FLAG, "1")


class _StubTask:
    """Stands in for `skcapstone.coordination.Task` on a node without the
    optional sibling. Only the two things `_FileBoard` needs: an id and a
    dict dump. A separate needs_skcapstone test pins the real model."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def id(self):
        return self.__dict__["id"]

    def model_dump(self):
        return dict(self.__dict__)


class _FileBoard:
    """Mirrors `Board.create_task`: one immutable JSON file per card under
    `<home>/coordination/tasks/`, which is exactly what the ledger reads."""

    def __init__(self, home: Path):
        self.tasks_dir = Path(home) / "coordination" / "tasks"
        self.created = []

    def create_task(self, task):
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        data = task.model_dump()
        slug = "".join(c if c.isalnum() else "-" for c in str(data["title"]).lower())[:40]
        (self.tasks_dir / f"{data['id']}-{slug}.json").write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.created.append(data)
        return data

    def archive_task(self, task_id, by=""):
        """The real archive: an APPEND to an index. The task file itself is
        left exactly where it is, which is the property guard 6 rests on."""
        d = Path(self.tasks_dir).parent / "archive"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "test-host.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": task_id, "archived_at": "2026-08-12T00:00:00+00:00",
                                 "archived_by": by}) + "\n")


@pytest.fixture
def board(home, monkeypatch):
    monkeypatch.setattr(cards_mod, "_task_factory", lambda: _StubTask)
    return _FileBoard(home)


def _event(*, source="git", kind="CIFailure", object="skos#41", severity="problem",
           summary="skos PR #41 has 2 failing checks.", meta=None,
           uri="skworld://skos/watchdog/git/skos/pr/41",
           http="https://github.com/smilinTux/skos/pull/41"):
    return {"ts": "2026-08-12T07:45:00Z", "source": source, "kind": kind,
            "object": object, "severity": severity, "summary": summary,
            "link": {"uri": uri, "http": http},
            "ref": f"{source}:{object}:2026-08-12", "meta": dict(meta or {})}


def _digest(problems=(), date="2026-08-12", sources=("git",), ok=True):
    return {
        "date": date,
        "window": {"from": "2026-08-11T07:45:00Z", "to": f"{date}T07:45:00Z"},
        "headline": "1 problem.",
        "problems": list(problems),
        "notable": [],
        "info_counts": {},
        "per_source": {n: {"ok": ok, "events": 1, "cursor": f"{date}T07:45:00Z"}
                       for n in sources},
    }


def _track(event, *, created="2026-08-11T07:45:00Z"):
    """Put the WD-8 GTD item this finding's card escalates into the tmp GTD
    store, with a created_at that makes it 'persisted' by default."""
    from skos.gtd_ingest import gtd_dir
    ref = source_ref_for(event)
    path = gtd_dir() / "next-actions.json"
    items = json.loads(path.read_text()) if path.exists() else []
    items.append({"id": f"item-{len(items)}", "text": card_title(event),
                  "source": "watchdog", "source_ref": ref, "status": "next",
                  "created_at": created})
    path.write_text(json.dumps(items, indent=2))
    return ref


def _cards_on(home):
    d = Path(home) / "coordination" / "tasks"
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        data = json.loads(p.read_text())
        if CARD_TAG in (data.get("tags") or []) and data.get("id") != EPIC_ID:
            out.append(data)
    return out


# -- the flag: off is invisible (guard 1) ------------------------------------

def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert cards_enabled() is False


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_only_explicit_truthy_values_enable_filing(monkeypatch, value):
    monkeypatch.setenv(FLAG, value)
    assert cards_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_values_enable_filing(monkeypatch, value):
    monkeypatch.setenv(FLAG, value)
    assert cards_enabled() is True


def test_flag_off_reads_nothing_writes_nothing_creates_nothing(home, monkeypatch):
    """PROOF of guard 1: with the flag off, filing does not resolve the board,
    read a task file, import skcapstone, or create a directory."""
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv(GTD_FLAG, "1")
    monkeypatch.setattr(cards_mod, "read_ledger",
                        lambda *a, **k: pytest.fail("flag off read the board"))
    monkeypatch.setattr(cards_mod, "_board_for",
                        lambda *a, **k: pytest.fail("flag off resolved a board"))

    report = file_cards(_digest(problems=[_event()]))
    assert report == {"enabled": False, "skipped": "flag-off", "filed": [],
                      "refused": [], "deduped": 0, "dropped": [], "budget": DAILY_BUDGET}
    assert not home.exists()


def test_coord_home_never_creates_the_directory(home):
    assert coord_home() == home
    assert tasks_dir() == home / "coordination" / "tasks"
    assert not home.exists()


# -- the stand-downs ---------------------------------------------------------

def test_gtd_off_short_circuits_the_whole_run(home, monkeypatch):
    """A card is an escalation of a WD-8 item. With the tracking layer off
    there is nothing to escalate, so nothing is read or written at all."""
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.delenv(GTD_FLAG, raising=False)
    report = file_cards(_digest(problems=[_event()]))
    assert report["skipped"] == "gtd-off" and report["filed"] == []
    assert not home.exists()


def test_frozen_fleet_stands_all_card_writes_down(home, flag_on, monkeypatch):
    monkeypatch.setattr(cards_mod, "fleet_frozen", lambda: True)
    report = file_cards(_digest(problems=[_event()]))
    assert report["skipped"] == "frozen" and report["filed"] == []
    assert not home.exists()


def test_freeze_is_the_one_shared_freeze_concept():
    """Guard 7: the same callable WD-8 uses, not a second implementation."""
    from skos.watchdog import gtd as gtd_mod
    assert cards_mod.fleet_frozen is gtd_mod.fleet_frozen


# -- guard 4: a repo tag is required, never defaulted ------------------------

def test_repo_comes_from_an_adapter_that_states_it():
    assert repo_for(_event(source="fleet", meta={"repo": "skchat"})) == "skchat"


@pytest.mark.parametrize("obj,expected", [
    ("skos#41", "skos"), ("skchat@a1b2c3d4e5", "skchat"), ("skharness", "skharness"),
])
def test_repo_comes_from_the_git_adapters_own_object_field(obj, expected):
    assert repo_for(_event(source="git", object=obj)) == expected


@pytest.mark.parametrize("event", [
    _event(source="scheduler", object="ops-report"),
    _event(source="fleet", object="skchat-daemon@dot41", meta={}),
    _event(source="itil", object="inc-31cfd2da"),
    _event(source="git", object=""),
    _event(source="fleet", meta={"repo": "two words"}),
    _event(source="fleet", meta={"repo": ""}),
    _event(source="git", object="smilinTux/skos#41"),
])
def test_an_unattributable_finding_is_refused_not_defaulted(event):
    assert repo_for(event) is None


def test_a_finding_with_no_repo_files_no_card(home, board, flag_on):
    ev = _event(source="scheduler", kind="JobStalled", object="ops-report")
    _track(ev)
    report = file_cards(_digest(problems=[ev]), board=board)
    assert report["filed"] == []
    assert report["refused"] == [{"source_ref": source_ref_for(ev), "reason": "no-repo"}]
    assert _cards_on(home) == []


# -- guard 3 + the staged lane (guard 2) -------------------------------------

def test_a_qualifying_finding_becomes_one_staged_child_of_the_one_epic(home, board, flag_on):
    ev = _event()
    ref = _track(ev)
    report = file_cards(_digest(problems=[ev]), board=board)

    assert [f["source_ref"] for f in report["filed"]] == [ref]
    filed = _cards_on(home)
    assert len(filed) == 1
    card = filed[0]
    assert card["id"] == card_id_for(ref)
    assert set(STAGED_TAGS).issubset(set(card["tags"]))
    assert f"parent:{EPIC_ID}" in card["tags"]
    assert "repo:skos" in card["tags"]
    assert card["meta"]["autopilot"] == {"parent": EPIC_ID, "staged": True}
    assert card["meta"][META_KEY]["source_ref"] == ref
    assert card["meta"][META_KEY]["filed_date"] == "2026-08-12"
    assert card["meta"][META_KEY]["repo"] == "skos"


def test_the_epic_is_created_once_and_only_once(home, board, flag_on):
    ev1 = _event(object="skos#41")
    _track(ev1)
    file_cards(_digest(problems=[ev1], date="2026-08-12"), board=board)
    ev2 = _event(object="skos#42")
    _track(ev2)
    file_cards(_digest(problems=[ev2], date="2026-08-13"), board=board)

    epics = [c for c in board.created if c["id"] == EPIC_ID]
    assert len(epics) == 1
    assert epics[0]["title"] == EPIC_TITLE
    assert "autopilot-staged" in epics[0]["tags"]


def test_a_run_with_nothing_to_file_creates_no_epic_and_no_directory(home, flag_on, monkeypatch):
    monkeypatch.setattr(cards_mod, "_board_for",
                        lambda *a, **k: pytest.fail("resolved a board with nothing to file"))
    report = file_cards(_digest(problems=[]))
    assert report["filed"] == [] and report["enabled"] is True
    assert not home.exists()


# -- the WD-8 / WD-9 relationship: escalation, not duplication ---------------

def test_an_untracked_finding_files_no_card(home, board, flag_on):
    """No WD-8 item means nothing to escalate."""
    report = file_cards(_digest(problems=[_event()]), board=board)
    assert [r["reason"] for r in report["refused"]] == ["gtd-untracked"]
    assert _cards_on(home) == []


def test_a_finding_first_seen_this_morning_files_no_card(home, board, flag_on):
    """The recall-for-restraint trade, stated in the module docstring: a
    one-morning blip gets an item and a day to clear, never a card."""
    ev = _event()
    _track(ev, created="2026-08-12T07:45:00Z")
    report = file_cards(_digest(problems=[ev], date="2026-08-12"), board=board)
    assert [r["reason"] for r in report["refused"]] == ["gtd-new"]
    assert _cards_on(home) == []


def test_the_same_finding_still_there_tomorrow_does_earn_a_card(home, board, flag_on):
    ev = _event()
    _track(ev, created="2026-08-12T07:45:00Z")
    assert file_cards(_digest(problems=[ev], date="2026-08-12"), board=board)["filed"] == []
    report = file_cards(_digest(problems=[ev], date="2026-08-13"), board=board)
    assert len(report["filed"]) == 1


def test_an_item_with_an_unreadable_created_at_is_treated_as_new(home, board, flag_on):
    ev = _event()
    _track(ev, created="")
    report = file_cards(_digest(problems=[ev]), board=board)
    assert [r["reason"] for r in report["refused"]] == ["gtd-new"]


def test_a_completed_gtd_item_is_not_escalated(home, board, flag_on):
    """`_find_item` searches the archive too. A cleared problem has nothing
    to escalate, so an archived item must not count as tracking."""
    from skos.gtd_ingest import gtd_dir
    ev = _event()
    ref = source_ref_for(ev)
    (gtd_dir() / "archive.json").write_text(json.dumps([
        {"id": "x", "text": "t", "source": "watchdog", "source_ref": ref,
         "status": "done", "created_at": "2026-08-01T00:00:00Z"}]))
    report = file_cards(_digest(problems=[ev]), board=board)
    assert [r["reason"] for r in report["refused"]] == ["gtd-untracked"]
    assert _cards_on(home) == []


def test_the_card_cross_links_to_the_gtd_item(home, board, flag_on):
    ev = _event()
    _track(ev)
    file_cards(_digest(problems=[ev]), board=board)
    assert _cards_on(home)[0]["meta"][META_KEY]["gtd_item"] == "item-0"


# -- guard 6: dedupe against everything ever filed ---------------------------

def test_a_filed_finding_is_never_filed_twice(home, board, flag_on):
    ev = _event()
    _track(ev)
    file_cards(_digest(problems=[ev], date="2026-08-12"), board=board)
    report = file_cards(_digest(problems=[ev], date="2026-08-13"), board=board)
    assert report["filed"] == [] and report["deduped"] == 1
    assert len(_cards_on(home)) == 1


def test_a_rejected_finding_does_not_come_back(home, board, flag_on):
    """THE guard. A human reads the staged card, judges it not worth doing,
    and archives it. Archiving appends an id to an index and leaves the task
    file in place, so the ledger still remembers it. Deduping against only
    OPEN cards would re-file this finding every single morning forever."""
    ev = _event()
    _track(ev)
    first = file_cards(_digest(problems=[ev], date="2026-08-12"), board=board)
    card_id = first["filed"][0]["id"]

    board.archive_task(card_id, by="chef-rejected-it")
    archived = json.loads(
        (Path(home) / "coordination" / "archive" / "test-host.jsonl").read_text().strip())
    assert archived["id"] == card_id

    for day in ("2026-08-13", "2026-08-14", "2026-08-15"):
        report = file_cards(_digest(problems=[ev], date=day), board=board)
        assert report["filed"] == [], f"the rejected finding came back on {day}"
        assert report["deduped"] == 1
    assert len(_cards_on(home)) == 1


def test_the_ledger_reads_every_task_file_regardless_of_state(home, board, flag_on):
    """`read_ledger` deliberately applies no status/open/archived filter."""
    ev = _event()
    _track(ev)
    file_cards(_digest(problems=[ev]), board=board)
    ledger = read_ledger("2026-08-12")
    assert source_ref_for(ev) in ledger.refs
    assert ledger.epic_exists is True
    assert ledger.filed_today == 1


def test_the_ledger_is_the_board_and_there_is_no_side_file(home, board, flag_on):
    """No parallel store: the ONLY thing this module writes is coord task
    files. Deleting the board erases its memory, which is the honest
    consequence of refusing a side list (and is documented as such)."""
    ev = _event()
    _track(ev)
    file_cards(_digest(problems=[ev], date="2026-08-12"), board=board)
    written = {p.name for p in (Path(home) / "coordination" / "tasks").glob("*")}
    assert len(written) == 2  # the epic and the one card, nothing else
    assert [p for p in Path(home).rglob("*") if p.is_file() and p.suffix not in (".json",)] == []


def test_a_malformed_task_file_does_not_break_the_ledger(home, board, flag_on):
    d = Path(home) / "coordination" / "tasks"
    d.mkdir(parents=True)
    (d / "garbage.json").write_text("{not json")
    ev = _event()
    _track(ev)
    assert len(file_cards(_digest(problems=[ev]), board=board)["filed"]) == 1


def test_two_sightings_of_one_finding_collapse_to_one_card(home, board, flag_on):
    ev = _event()
    _track(ev)
    dupes = [dict(ev, ref="git:a"), dict(ev, ref="git:b", summary="again.")]
    report = file_cards(_digest(problems=dupes), board=board)
    assert len(report["filed"]) == 1
    assert len(_cards_on(home)) == 1


def test_a_deterministic_id_collision_refuses_rather_than_overwrites(home, board, flag_on):
    ev = _event()
    ref = _track(ev)
    d = Path(home) / "coordination" / "tasks"
    d.mkdir(parents=True)
    (d / f"{card_id_for(ref)}-someone-elses-card.json").write_text(
        json.dumps({"id": card_id_for(ref), "title": "not ours", "tags": []}))

    report = file_cards(_digest(problems=[ev]), board=board)
    assert [r["reason"] for r in report["refused"]] == ["id-collision"]
    assert board.created == []


# -- guard 5: the hard budget, and loud drops --------------------------------

def _seven_findings():
    evs = [_event(object=f"skos#{n}", summary=f"skos PR #{n} has failing checks.")
           for n in range(41, 48)]
    for ev in evs:
        _track(ev)
    return evs


def test_the_budget_is_hard_and_the_rest_are_dropped(home, board, flag_on, caplog):
    evs = _seven_findings()
    with caplog.at_level(logging.WARNING):
        report = file_cards(_digest(problems=evs), board=board)

    assert len(report["filed"]) == DAILY_BUDGET == 5
    assert len(report["dropped"]) == 2
    assert len(_cards_on(home)) == 5

    for drop in report["dropped"]:
        assert drop["reason"] == "daily budget 5 exhausted"
        assert drop["title"]
        # named individually in the log, never a bare count
        assert any(drop["source_ref"] in r.getMessage() for r in caplog.records)


def test_dropped_findings_are_not_silently_marked_as_covered(home, board, flag_on):
    """A dropped finding must NOT reach the ledger: it was not filed, so
    tomorrow's run must reconsider it."""
    evs = _seven_findings()
    report = file_cards(_digest(problems=evs, date="2026-08-12"), board=board)
    dropped = {d["source_ref"] for d in report["dropped"]}
    assert dropped.isdisjoint(read_ledger("2026-08-12").refs)

    tomorrow = file_cards(_digest(problems=evs, date="2026-08-13"), board=board)
    assert {f["source_ref"] for f in tomorrow["filed"]} == dropped
    assert tomorrow["deduped"] == 5


def test_the_budget_is_spent_per_day_not_per_run(home, board, flag_on):
    """Running the digest twice in one day must not spend the budget twice.
    The count comes off the board, not off a counter this module keeps."""
    evs = _seven_findings()
    first = file_cards(_digest(problems=evs, date="2026-08-12"), board=board)
    second = file_cards(_digest(problems=evs, date="2026-08-12"), board=board)
    assert len(first["filed"]) == 5
    assert second["filed"] == [] and second["budget"] == 0
    assert len(second["dropped"]) == 2
    assert len(_cards_on(home)) == 5


def test_the_budget_cut_is_deterministic(home, board, flag_on):
    """Stable ordering by source_ref, so the same input always drops the same
    findings and tomorrow's run picks up a queue rather than a reshuffle."""
    evs = _seven_findings()
    report = file_cards(_digest(problems=evs), board=board)
    refs = sorted(source_ref_for(e) for e in evs)
    assert [f["source_ref"] for f in report["filed"]] == refs[:5]
    assert [d["source_ref"] for d in report["dropped"]] == refs[5:]


# -- severity discipline (the 2026-08-08 flood lesson) -----------------------

def test_only_problem_severity_ever_files(home, board, flag_on):
    ev = _event(severity="notable")
    _track(ev)
    report = file_cards(_digest(problems=[ev]), board=board)
    assert report["filed"] == [] and report["refused"] == []
    assert _cards_on(home) == []


# -- fail-safe ---------------------------------------------------------------

def test_a_board_outage_never_raises_and_never_breaks_the_run(home, flag_on, monkeypatch):
    monkeypatch.setattr(cards_mod, "_task_factory", lambda: _StubTask)
    ev = _event()
    _track(ev)

    class _Broken:
        def create_task(self, task):
            raise RuntimeError("simulated board outage")

    report = file_cards(_digest(problems=[ev]), board=_Broken())
    assert report["filed"] == []
    assert any("simulated board outage" in e for e in report["errors"])


def test_one_bad_card_does_not_stop_the_rest(home, board, flag_on, monkeypatch):
    evs = [_event(object="skos#41"), _event(object="skos#42")]
    for ev in evs:
        _track(ev)
    real = board.create_task

    def _selective(task):
        if "#41" in task.model_dump()["title"]:
            raise RuntimeError("nope")
        return real(task)

    monkeypatch.setattr(board, "create_task", _selective)
    report = file_cards(_digest(problems=evs), board=board)
    assert [f["source_ref"] for f in report["filed"]] == ["git:CIFailure:skos#42"]
    assert len(report["errors"]) == 1


# -- what Chef reads ---------------------------------------------------------

def test_no_banned_dashes_reach_a_card_title_or_body(home, board, flag_on):
    ev = _event(kind="CI — failure", summary="skos PR #41 failed — twice – today.")
    _track(ev)
    file_cards(_digest(problems=[ev]), board=board)
    card = _cards_on(home)[0]
    assert "—" not in card["title"] and "–" not in card["title"]
    assert "—" not in card["description"] and "–" not in card["description"]


def test_the_body_says_it_is_staged_and_names_the_release_step(home, board, flag_on):
    ev = _event()
    _track(ev)
    file_cards(_digest(problems=[ev]), board=board)
    body = _cards_on(home)[0]["description"]
    assert "STAGED" in body
    assert f"skos autopilot release {EPIC_ID}" in body
    assert "https://github.com/smilinTux/skos/pull/41" in body


def test_no_acceptance_criteria_are_invented(home, board, flag_on):
    ev = _event()
    _track(ev)
    file_cards(_digest(problems=[ev]), board=board)
    assert not (_cards_on(home)[0].get("acceptance_criteria") or [])


# -- through the real pipeline (run.py) --------------------------------------

class _ProblemAdapter(WatchdogSourceAdapter):
    """A test-only source on an ISOLATED registry, never the shared one."""
    name = "git"

    def collect(self, window):
        return [WatchdogEvent(
            ts=window.until, source=self.name, kind="CIFailure", object="skos#41",
            severity="problem", summary="skos PR #41 has 2 failing checks.",
            link=WatchdogLink(uri="skworld://skos/watchdog/git/skos/pr/41",
                              http="https://github.com/smilinTux/skos/pull/41"),
            ref="git:skos:pr:41:ci:2026-08-12")]


def _pipeline_run(monkeypatch, tmp_path, tag, *, flag):
    """One full run_digest_and_deliver against a fake source and throwaway
    stores. Returns (report, the exact published digest.json bytes)."""
    from skos.watchdog import deliver as dl, headline as hl
    from skos.watchdog.publish import latest_dir
    from skos.watchdog.run import run_digest_and_deliver

    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    monkeypatch.setattr(dl, "default_sender", lambda text: True)
    monkeypatch.setattr(cards_mod, "_task_factory", lambda: _StubTask)
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / f"watchdog-{tag}"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / f"gtd-{tag}"))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / f"skcapstone-{tag}"))
    monkeypatch.setenv(GTD_FLAG, "1")
    if flag:
        monkeypatch.setenv(FLAG, "1")
        monkeypatch.setattr(cards_mod, "_board_for",
                            lambda home: _FileBoard(home))
    else:
        monkeypatch.delenv(FLAG, raising=False)

    reg = AdapterRegistry()
    reg.register(_ProblemAdapter)
    report = run_digest_and_deliver(now="2026-08-12T07:45:00Z", registry=reg)
    return report, (latest_dir() / "digest.json").read_bytes()


def test_flag_off_through_the_full_pipeline_is_invisible(tmp_path, monkeypatch):
    """PROOF of guard 1 end to end: the published digest is byte-identical
    with the flag on and off, and with it off no board tree exists at all."""
    off_report, off_bytes = _pipeline_run(monkeypatch, tmp_path, "off", flag=False)
    on_report, on_bytes = _pipeline_run(monkeypatch, tmp_path, "on", flag=True)

    assert off_bytes == on_bytes
    assert off_report["cards"]["enabled"] is False
    assert off_report["cards"]["skipped"] == "flag-off"
    assert not (tmp_path / "skcapstone-off").exists()

    # Flag ON, first sighting: the GTD item is created by this same run, so by
    # design no card is filed yet. The board tree still does not exist.
    assert [r["reason"] for r in on_report["cards"]["refused"]] == ["gtd-new"]
    assert not (tmp_path / "skcapstone-on").exists()


def test_a_persisting_finding_files_a_card_through_the_real_pipeline(tmp_path, monkeypatch):
    _pipeline_run(monkeypatch, tmp_path, "day1", flag=True)  # opens the GTD item
    # age the item so the next run sees it as persisted
    items_path = tmp_path / "gtd-day1" / "next-actions.json"
    items = json.loads(items_path.read_text())
    items[0]["created_at"] = "2026-08-01T07:45:00Z"
    items_path.write_text(json.dumps(items))

    report, _bytes = _pipeline_run(monkeypatch, tmp_path, "day1", flag=True)
    assert len(report["cards"]["filed"]) == 1
    cards_written = _cards_on(tmp_path / "skcapstone-day1")
    assert len(cards_written) == 1
    assert "repo:skos" in cards_written[0]["tags"]
    assert "autopilot-staged" in cards_written[0]["tags"]


def test_dry_run_files_nothing_even_with_the_flag_on(tmp_path, monkeypatch):
    from skos.watchdog import headline as hl
    from skos.watchdog.run import run_digest_and_deliver

    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "skcapstone"))
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(GTD_FLAG, "1")

    reg = AdapterRegistry()
    reg.register(_ProblemAdapter)
    report = run_digest_and_deliver(now="2026-08-12T07:45:00Z", dry_run=True, registry=reg)
    assert report["cards"] == {}
    assert not (tmp_path / "skcapstone").exists()


def test_card_filing_runs_after_publish_and_after_the_cursor_advance(tmp_path, monkeypatch):
    """Fail-safe ordering: even a catastrophic filing failure cannot unpublish
    a digest or rewind a cursor."""
    from skos.watchdog import cursor as cur, deliver as dl, headline as hl
    from skos.watchdog.publish import latest_dir
    from skos.watchdog.run import run_digest_and_deliver

    monkeypatch.setattr(hl, "_chat_completion", lambda prompt, **kw: None)
    monkeypatch.setattr(dl, "default_sender", lambda text: True)
    monkeypatch.setenv("SK_WATCHDOG_DIR", str(tmp_path / "watchdog"))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv("SKCAPSTONE_HOME", str(tmp_path / "skcapstone"))
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(GTD_FLAG, "1")
    monkeypatch.setattr(cards_mod, "read_ledger",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("board on fire")))

    reg = AdapterRegistry()
    reg.register(_ProblemAdapter)
    report = run_digest_and_deliver(now="2026-08-12T07:45:00Z", registry=reg)

    assert report["published"] is True and report["sent"] is True
    assert (latest_dir() / "digest.json").exists()
    assert cur.read_cursor("git") == "2026-08-12T07:45:00Z"
    assert any("board on fire" in e for e in report["cards"]["errors"])


# -- the real skcapstone contract --------------------------------------------

@pytest.mark.needs_skcapstone
def test_the_real_task_model_accepts_the_card_payload(home, flag_on):
    """The stub above is only a stand-in. Pin the payload against the REAL
    coord Task model and the REAL Board, on a throwaway home."""
    from skcapstone.coordination import Board

    ev = _event()
    ref = _track(ev)
    board = Board(home)
    report = file_cards(_digest(problems=[ev]), board=board)

    assert [f["id"] for f in report["filed"]] == [card_id_for(ref)]
    written = _cards_on(home)
    assert len(written) == 1
    assert written[0]["created_by"] == "skwatchdog"
    assert written[0]["priority"] == "low"


@pytest.mark.needs_skcapstone
def test_archiving_through_the_real_board_leaves_the_task_file_in_place(home, flag_on):
    """The property guard 6 rests on, pinned against the real implementation:
    archiving is an index append, so the ledger keeps its memory."""
    from skcapstone.coordination import Board

    ev = _event()
    _track(ev)
    board = Board(home)
    cid = file_cards(_digest(problems=[ev], date="2026-08-12"), board=board)["filed"][0]["id"]
    board.archive_task(cid, by="rejected")

    assert cid in board.archived_ids()
    assert list((home / "coordination" / "tasks").glob(f"{cid}-*.json"))
    again = file_cards(_digest(problems=[ev], date="2026-08-13"), board=board)
    assert again["filed"] == [] and again["deduped"] == 1
