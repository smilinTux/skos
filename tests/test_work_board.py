"""SK Work board: Deck cards driven by the task-trigger dispatcher (fakes, no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from skos import work_board as wb


class FakeDeck:
    def __init__(self):
        self.base, self.user = "https://hub", "lumina"
        self.stack_list = [{"id": i + 1, "title": t} for i, t in enumerate(wb.STACKS)]
        self.cards: dict[int, dict] = {}
        self.comments: list[tuple[int, str]] = []
        self.attachments: list[str] = []
        self.movers: dict[tuple[int, str], str] = {}
        self.next_id = 100

    def sid(self, name):
        return next(s["id"] for s in self.stack_list if s["title"] == name)

    def stack_of(self, cid):
        sid = self.cards[cid]["stackId"]
        return next(s["title"] for s in self.stack_list if s["id"] == sid)

    def add(self, title, desc="", stack="Triage", labels=()):
        self.next_id += 1
        self.cards[self.next_id] = {"id": self.next_id, "title": title, "description": desc,
                                    "stackId": self.sid(stack), "labels": [{"title": x} for x in labels],
                                    "owner": {"uid": "dknestri"}, "order": 0}
        return self.next_id

    def find_board(self, title):
        return 9

    def stacks(self, bid):
        out = []
        for s in self.stack_list:
            out.append({**s, "cards": [dict(c) for c in self.cards.values() if c["stackId"] == s["id"]]})
        return out

    def card(self, bid, sid, cid):
        return dict(self.cards[cid])

    def set_description(self, bid, card, description):
        self.cards[card["id"]]["description"] = description

    def move(self, bid, card, sid):
        self.cards[card["id"]]["stackId"] = sid
        card["stackId"] = sid

    def create_card(self, bid, sid, title, description):
        cid = self.add(title, description)
        self.cards[cid]["stackId"] = sid
        return self.cards[cid]

    def comment(self, cid, message):
        self.comments.append((cid, message))

    def attach(self, bid, card, name, data):
        self.attachments.append(name)

    def mover(self, cid, to_stack):
        return self.movers.get((cid, to_stack), "")


class FakeTalk:
    def __init__(self):
        self.posts: list[dict] = []
        self.react: dict[str, dict] = {}
        self.titles: dict[str, str] = {}
        self.n = 1000

    def talk_post(self, room, message, *, thread_id="", title=""):
        self.n += 1
        self.posts.append({"room": room, "message": message, "thread": thread_id, "title": title, "id": str(self.n)})
        if title:
            self.titles[str(self.n)] = title
        return str(self.n)

    def thread_info(self, room, tid):
        return {"thread": {"title": self.titles.get(str(tid), "")}}

    def thread_set(self, room, tid, *, title=None, level=None):
        if title is not None:
            self.titles[str(tid)] = title

    def reactions(self, room, mid):
        return self.react.get(str(mid), {})


class FakeEngine:
    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.notes: list[tuple[str, str]] = []
        self.ensured: list[str] = []
        self.queued: list[tuple] = []
        self.votes: list[tuple] = []
        self.cards: list = []
        self.links: list[tuple] = []

    def ensure(self, coord_id, title, description, labels, origin):
        self.ensured.append(coord_id)
        return coord_id

    def suggest(self, coord_id):
        return [{"text": "Read the logs", "mode": "propose"},
                {"text": "Try a fix in a scratch tree", "mode": "dry-run"},
                {"text": "Open a draft PR", "mode": "execute"}]

    def agents(self):
        return ["lumina", "scholar"]

    def queue(self, coord_id, instruction, agent, mode, requester):
        rid = f"run-{len(self.queued) + 1}"
        self.queued.append((coord_id, instruction, agent, mode))
        self.runs[coord_id] = {"run_id": rid, "state": "queued", "activity": []}
        return rid

    def run(self, coord_id):
        return self.runs.get(coord_id, {})

    def note(self, coord_id, text):
        self.notes.append((coord_id, text))

    def change_id(self, coord_id, description):
        m = wb._CHG.search(description or "")
        return m.group(0) if m else ""

    def cab_vote(self, chg, who, evidence):
        self.votes.append((chg, who))
        return "approved"

    def link(self, coord_id, url):
        self.links.append((coord_id, url))

    def _store(self):
        return SimpleNamespace(list_cards=lambda include_archived=False: self.cards)


class FakeHermes:
    def __init__(self):
        self.started = []
        self.result = None

    def start(self, session_id, text):
        self.started.append((session_id, text))
        return "h-1"

    def status(self, run_id):
        return self.result


def make(cab=False):
    deck, talk, eng, herm = FakeDeck(), FakeTalk(), FakeEngine(), FakeHermes()
    w = wb.BoardWorker(deck, talk, eng, {}, board_title="SK Work", room="dm1",
                       approvers={"dknestri", "caseyK"}, hermes=herm,
                       planner=lambda *a: "1. look\n2. do", cab_bridge=cab)
    return w, deck, talk, eng, herm


def tick(deck, cid, n):
    d = deck.cards[cid]["description"]
    deck.cards[cid]["description"] = d.replace(f"- [ ] {n}.", f"- [x] {n}.", 1)


def finish(eng, coord, summary="All good", state="needs-review"):
    eng.runs[coord]["state"] = state
    eng.runs[coord]["activity"] = [{"atype": "response", "text": summary}]


def test_triage_card_gets_three_options_and_moves():
    w, deck, *_ = make()
    cid = deck.add("Fix the backup timer", "It fails nightly")
    st = w.cycle()
    assert st["options"] == 1
    desc = deck.cards[cid]["description"]
    assert desc.startswith("It fails nightly")
    assert "- [ ] 1. Read the logs (propose)" in desc and "Coord card: deck-" in desc
    assert deck.stack_of(cid) == "Options ready"
    st = w.cycle()  # our own write never retriggers
    assert st["options"] == 0


def test_tick_queues_run_opens_thread_and_consumes_tick():
    w, deck, talk, eng, _ = make()
    cid = deck.add("Fix the backup timer", labels=("agent:scholar",))
    w.cycle()
    tick(deck, cid, 2)
    w.cycle()
    assert eng.queued[0][1:] == ("Try a fix in a scratch tree", "scholar", "dry-run")
    assert deck.stack_of(cid) == "Agent queued"
    assert talk.posts[0]["title"] == "Card: Fix the backup timer"
    assert "[x]" not in deck.cards[cid]["description"]
    assert "Talk thread: https://hub/call/dm1#message_" in deck.cards[cid]["description"]


def test_go_line_in_description_picks_and_is_removed():
    w, deck, _, eng, _ = make()
    cid = deck.add("Card")
    w.cycle()
    deck.cards[cid]["description"] = "go 1\n" + deck.cards[cid]["description"]
    w.cycle()
    assert eng.queued[0][1] == "Read the logs"
    assert not deck.cards[cid]["description"].startswith("go 1")


def test_unknown_agent_label_falls_back_to_lumina():
    w, deck, _, eng, _ = make()
    cid = deck.add("Card", labels=("agent:nobody",))
    w.cycle()
    tick(deck, cid, 1)
    w.cycle()
    assert eng.queued[0][2] == "lumina"


def test_non_prod_run_reports_and_closes():
    w, deck, talk, eng, _ = make()
    cid = deck.add("Card")
    w.cycle()
    tick(deck, cid, 1)
    w.cycle()
    coord = f"deck-{cid}"
    eng.runs[coord]["state"] = "running"
    w.cycle()
    assert deck.stack_of(cid) == "Agent working"
    finish(eng, coord, "Found the cause: disk full.")
    w.cycle()
    assert deck.stack_of(cid) == "Done"
    assert any("Ready for review." in p["message"] and "disk full" in p["message"] for p in talk.posts)
    assert deck.comments and "disk full" in deck.comments[-1][1]
    tid = talk.posts[0]["id"]
    assert talk.titles[tid].startswith(wb.CLOSED_MARK)


def test_long_report_goes_in_an_attachment():
    w, deck, _, eng, _ = make()
    cid = deck.add("Card")
    w.cycle()
    tick(deck, cid, 1)
    w.cycle()
    finish(eng, f"deck-{cid}", "x" * 3000)
    w.cycle()
    assert deck.attachments == ["report-run-1.md"]
    assert all(len(m) <= wb.COMMENT_MAX for _, m in deck.comments)


def test_failed_run_goes_back_to_options():
    w, deck, talk, eng, _ = make()
    cid = deck.add("Card")
    w.cycle()
    tick(deck, cid, 1)
    w.cycle()
    finish(eng, f"deck-{cid}", "boom", state="failed")
    w.cycle()
    assert deck.stack_of(cid) == "Options ready"
    assert any("The run failed." in p["message"] for p in talk.posts)


def prod_waiting(cab=False, desc=""):
    w, deck, talk, eng, herm = make(cab)
    cid = deck.add("Rotate the cert", desc, labels=("prod",))
    w.cycle()
    tick(deck, cid, 1)
    w.cycle()
    finish(eng, f"deck-{cid}", "Plan ready, draft PR 12")
    w.cycle()
    return w, deck, talk, eng, herm, cid


def test_prod_stops_for_approval_and_ignores_non_approver_reaction():
    w, deck, talk, eng, herm, cid = prod_waiting()
    assert deck.stack_of(cid) == "Needs approval"
    mid = w.state["cards"][str(cid)]["approval_msg"]
    talk.react[mid] = {wb.CHECK: [{"actorType": "users", "actorId": "lumina"},
                                  {"actorType": "users", "actorId": "randomuser"}]}
    w.cycle()
    assert deck.stack_of(cid) == "Needs approval"
    assert not eng.notes and not herm.started
    assert any("does not count" in p["message"] for p in talk.posts)


def test_approver_reaction_approves_records_note_and_hands_back_to_agent():
    w, deck, talk, eng, herm, cid = prod_waiting()
    mid = w.state["cards"][str(cid)]["approval_msg"]
    talk.react[mid] = {wb.CHECK: [{"actorType": "users", "actorId": "dknestri"}]}
    st = w.cycle()
    assert st["approved"] == 1
    assert deck.stack_of(cid) == "Approved"
    assert "Approved by dknestri" in eng.notes[0][1]
    assert herm.started and herm.started[0][0] == f"card-{cid}"
    herm.result = {"status": "completed", "output": 'did it\nTASK_STATUS {"state": "done", "percent": 100, "note": "cert rotated"}'}
    w.cycle()
    assert deck.stack_of(cid) == "Approved"  # only a human moves it to Done
    assert any("cert rotated" in m for _, m in deck.comments)


def test_non_approver_move_to_approved_is_moved_back():
    w, deck, talk, eng, _, cid = prod_waiting()
    deck.cards[cid]["stackId"] = deck.sid("Approved")
    deck.movers[(cid, "Approved")] = "randomuser"
    st = w.cycle()
    assert st["moves_reverted"] == 1
    assert deck.stack_of(cid) == "Needs approval"
    assert "only Chef or Casey" in deck.comments[-1][1]
    assert not eng.notes


def test_agent_cannot_move_prod_card_to_done():
    w, deck, talk, eng, _, cid = prod_waiting()
    deck.cards[cid]["stackId"] = deck.sid("Done")
    deck.movers[(cid, "Done")] = "lumina"
    w.cycle()
    assert deck.stack_of(cid) == "Needs approval"
    assert "an agent tried" in deck.comments[-1][1]


def test_approver_move_to_approved_counts():
    w, deck, talk, eng, herm, cid = prod_waiting()
    deck.cards[cid]["stackId"] = deck.sid("Approved")
    deck.movers[(cid, "Approved")] = "caseyK"
    st = w.cycle()
    assert st["approved"] == 1 and deck.stack_of(cid) == "Approved"
    assert "Approved by caseyK" in eng.notes[0][1]


def test_change_linked_card_needs_cab_vote_when_bridge_off():
    w, deck, talk, eng, _, cid = prod_waiting(desc="Implements chg-1a2b3c4d")
    mid = w.state["cards"][str(cid)]["approval_msg"]
    talk.react[mid] = {wb.CHECK: [{"actorType": "users", "actorId": "dknestri"}]}
    w.cycle()
    assert not eng.votes
    assert any("still needs its CAB vote" in p["message"] for p in talk.posts)


def test_change_linked_card_records_cab_vote_when_bridge_on():
    w, deck, talk, eng, _, cid = prod_waiting(cab=True, desc="Implements chg-1a2b3c4d")
    mid = w.state["cards"][str(cid)]["approval_msg"]
    talk.react[mid] = {wb.CHECK: [{"actorType": "users", "actorId": "dknestri"}]}
    w.cycle()
    assert eng.votes == [("chg-1a2b3c4d", "dknestri")]


def test_pick_while_busy_is_ignored():
    w, deck, _, eng, _ = make()
    cid = deck.add("Card")
    w.cycle()
    tick(deck, cid, 1)
    w.cycle()
    tick(deck, cid, 2)
    w.cycle()
    assert len(eng.queued) == 1
    assert "pick ignored" in deck.cards[cid]["description"]


def test_migrate_puts_recent_open_coord_cards_on_the_board():
    w, deck, _, eng, _ = make()
    now = datetime.now(timezone.utc)

    def card(cid, days, status="ready", links=None):
        return SimpleNamespace(id=cid, title=f"T {cid}", description="d", kind=SimpleNamespace(value="task"),
                               status=SimpleNamespace(value=status), links=links or {},
                               updated_at=(now - timedelta(days=days)).isoformat())

    eng.cards = [card("aa", 2), card("bb", 30), card("cc", 1, "done"), card("dd", 3, links={"deck": "x"})]
    ids = wb.migrate(w, eng)
    assert ids == ["aa"]
    new = [c for c in deck.cards.values() if c["title"] == "T aa"][0]
    assert deck.stack_of(new["id"]) == "Triage"
    assert eng.links[0][0] == "aa"
    w.cycle()
    assert "Coord card: aa" in deck.cards[new["id"]]["description"]
    assert eng.ensured[-1] == "aa"


@pytest.mark.parametrize("block,want", [("- [x] 2. do it (dry-run)", "2"), ("- [ ] 1. a", ""), ("* [X] 3) b", "3")])
def test_ticked(block, want):
    assert wb.ticked(block) == want
