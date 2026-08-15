"""WD-9 guard 2, pinned against the real promotion machinery.

The whole point of reusing the existing staged lane is that a watchdog-filed
card must be indistinguishable from an autopilot-staged child: the same tag
pair, the same parent link, and the SAME human promotion step. This module
proves that end to end by filing a card and then running the real
`release_epic` from the autocode orchestrator over it, rather than asserting
that two string literals match.

It lives in its own file because tests/conftest.py drops any test module that
imports skharness when the optional sibling is absent (CI), and the rest of
the WD-9 suite must keep running there.

Hermetic: throwaway `SKCAPSTONE_HOME` + `SK_GTD_DIR`, and the same real-board
file-count guard the main WD-9 module uses.
"""
import json
from pathlib import Path

import pytest

from skharness.autocode.orchestrator import load_raw_tasks, release_epic

from skos.watchdog.cards import EPIC_ID, FLAG, file_cards
from skos.watchdog.gtd import FLAG as GTD_FLAG, source_ref_for

pytestmark = pytest.mark.needs_skcapstone

REAL_BOARD_TASKS = Path.home() / ".skcapstone" / "coordination" / "tasks"


@pytest.fixture(autouse=True)
def _never_the_real_board():
    def _count():
        return len(list(REAL_BOARD_TASKS.glob("*.json"))) if REAL_BOARD_TASKS.is_dir() else 0
    before = _count()
    yield
    assert _count() == before, "a test wrote to the REAL coordination board"


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "skcapstone"
    monkeypatch.setenv("SKCAPSTONE_HOME", str(d))
    monkeypatch.setenv("SK_GTD_DIR", str(tmp_path / "gtd"))
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv(GTD_FLAG, "1")
    return d


def _event():
    return {"ts": "2026-08-12T07:45:00Z", "source": "git", "kind": "CIFailure",
            "object": "skos#41", "severity": "problem",
            "summary": "skos PR #41 has 2 failing checks.",
            "link": {"uri": "skworld://skos/watchdog/git/skos/pr/41",
                     "http": "https://github.com/smilinTux/skos/pull/41"},
            "ref": "git:skos:pr:41:ci:2026-08-12", "meta": {}}


def _digest(event, date="2026-08-12"):
    return {"date": date, "window": {"from": "2026-08-11T07:45:00Z", "to": f"{date}T07:45:00Z"},
            "headline": "1 problem.", "problems": [event], "notable": [],
            "info_counts": {},
            "per_source": {"git": {"ok": True, "events": 1, "cursor": f"{date}T07:45:00Z"}}}


def _track(event):
    from skos.gtd_ingest import gtd_dir
    (gtd_dir() / "next-actions.json").write_text(json.dumps([
        {"id": "item-0", "text": "tracked", "source": "watchdog",
         "source_ref": source_ref_for(event), "status": "next",
         "created_at": "2026-08-01T07:45:00Z"}]))


def _card(home, cid):
    path = next((Path(home) / "coordination" / "tasks").glob(f"{cid}-*.json"))
    return json.loads(path.read_text())


def test_a_watchdog_card_is_promoted_by_the_ordinary_release_step(home):
    """File a staged card, then promote it exactly the way a human promotes
    an autopilot-staged child. Nothing watchdog-specific in the promotion."""
    from skcapstone.coordination import Board

    ev = _event()
    _track(ev)
    board = Board(home)
    cid = file_cards(_digest(ev), board=board)["filed"][0]["id"]

    staged = _card(home, cid)
    assert "autopilot-staged" in staged["tags"]      # hidden from unblocked/selection
    assert "autopilot-untriaged" in staged["tags"]   # the legacy build gate
    assert f"parent:{EPIC_ID}" in staged["tags"]

    tasks = load_raw_tasks(home / "coordination" / "tasks")
    released = release_epic(EPIC_ID, board=board, tasks=tasks)

    assert released == [cid]
    promoted = _card(home, cid)
    assert "autopilot-staged" not in promoted["tags"]
    assert "autopilot-untriaged" not in promoted["tags"]
    assert "repo:skos" in promoted["tags"]          # the repo tag survives promotion
    assert "autopilot" in promoted["tags"]


def test_the_standing_epic_itself_is_never_promoted(home):
    """`release_epic` promotes children, so the container stays parked in the
    Proposed lane and is never picked up as buildable work."""
    from skcapstone.coordination import Board

    ev = _event()
    _track(ev)
    board = Board(home)
    file_cards(_digest(ev), board=board)

    release_epic(EPIC_ID, board=board,
                 tasks=load_raw_tasks(home / "coordination" / "tasks"))
    assert "autopilot-staged" in _card(home, EPIC_ID)["tags"]
