"""work_board: the SK Work Deck board, driven by the task-trigger dispatcher.

Deck is the screen, skcoord is the engine. One process (skos-task-triggers)
runs this next to the Nextcloud Tasks worker, every cycle.

A card's life (stacks on the board):
    Triage -> Options ready -> Agent queued -> Agent working
           -> (prod only) Needs approval -> Approved -> Done

- A card in Triage gets three next steps from the existing engine
  (skcapstone.agent_run.suggest_next_steps, with its safety clamps), written
  as tick boxes in a block the agent owns at the end of the description.
- Chef ticks a box (or writes "go 2" on its own line). The pick is queued as
  an AgentRun on the skcoord card (the Deck card id is linked on it), a Talk
  thread "Card: <title>" opens in Chef's DM (reused on later picks), and the
  ai-runner does the work.
- When the run ends, the result goes to the thread ("ready for review"), a
  short comment on the card, and a report attachment when it is long.
  Non-prod cards then move to Done. Cards labelled prod stop at Needs approval.
- Approval: a check mark reaction on the approval message, or a move to
  Approved, by a member of the approvers list. It is recorded on the skcoord
  card as a note. Moves to Approved, and prod moves to Done, by anyone else
  (agents included) are moved back with a note. After approval Lumina carries
  out the approved plan through her own tools; only a human moves it to Done.

The CAB vote: Chef accepted a Nextcloud login on the approvers list as a CAB
identity (2026-10-30), so an approval on a card linked to an ITIL change is
also recorded as that person's vote on the change, with subject
"nextcloud:<uid>" and role "approver". The agent account is always removed
from the approvers list, so an agent still never approves. SKOS_WB_CAB_BRIDGE=0
turns this off and leaves the change waiting for a dashboard or signed vote.

Change detection: one GET of the board's stacks per cycle (the board ETag does
not change when a card moves, so it cannot gate the scan). Who moved a card
comes from the Activity API.

Env: the task-trigger env plus
    SKOS_WB_BOARD       board title (default "SK Work"); empty disables the board
    SKOS_WB_ROOM        Talk room for card threads (default SKOS_TT_ROOMS[board owner])
    SKOS_WB_APPROVERS   comma list of Nextcloud uids (default "dknestri,caseyK")
    SKOS_WB_CAB_BRIDGE  "0" to stop recording approvals as CAB votes (default on)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import requests

from skos.task_triggers import (
    BLOCK_END,
    BLOCK_START,
    CLOSED_MARK,
    NOTIFY_DEFAULT,
    NOTIFY_NEVER,
    _NOTE_AT,
    _NOTE_GO,
    _NOTE_OPTS,
    _stamp,
    block_log,
    parse_status,
    propose_plan,
    split_notes,
)

STACKS = ("Triage", "Options ready", "Agent queued", "Agent working",
          "Needs approval", "Approved", "Done")
TRIAGE, OPTIONS, QUEUED, WORKING, NEEDS_APPROVAL, APPROVED, DONE = STACKS
PROD_LABEL = "prod"
COMMENT_MAX = 900  # Deck caps comments at 1000 characters
TALK_MAX = 30000  # Talk caps messages at 32000 characters
OPTIONS_PER_CYCLE = 4
CHECK = "✅"
_TICK = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*([1-3])[.)]\s*(.*?)\s*$")
_MODE_TAIL = re.compile(r"\s*\((propose|dry-run|execute)\)\s*$")
_CHG = re.compile(r"\bchg-[0-9a-f]{6,}\b")
_COORD_LINE = re.compile(r"^Coord card:\s*(\S+)", re.M)


# --------------------------------------------------------------- the block

def render_block(coord_id: str, options: list[dict], log: list[str], thread_link: str = "") -> str:
    out = [BLOCK_START, f"Coord card: {coord_id}"]
    if thread_link:
        out.append(f"Talk thread: {thread_link}")
    if options:
        out.append("Next steps (tick one, or write go 2 on its own line above):")
        out += [f"- [ ] {n}. {o['text']} ({o['mode']})" for n, o in enumerate(options, 1)]
    if log:
        out.append("Log:")
        out += log[-8:]
    out.append(BLOCK_END)
    return "\n".join(out)


def ticked(block: str) -> str:
    """The number of the first ticked option in the block, or ''."""
    for ln in block.splitlines():
        m = _TICK.match(ln)
        if m and m.group(1).lower() == "x":
            return m.group(2)
    return ""


def block_options(block: str) -> list[dict]:
    out = []
    for ln in block.splitlines():
        m = _TICK.match(ln)
        if m:
            text = m.group(3)
            mm = _MODE_TAIL.search(text)
            out.append({"text": _MODE_TAIL.sub("", text), "mode": mm.group(1) if mm else "propose"})
    return out


def parse_pick(chef: str, block: str) -> tuple[dict | None, str]:
    """(command, Chef's text with any command line removed)."""
    lines = chef.splitlines()
    for i, ln in enumerate(lines):
        for rx, kind in ((_NOTE_GO, "go"), (_NOTE_OPTS, "options"), (_NOTE_AT, "go")):
            m = rx.match(ln)
            if m:
                choice = (m.group(1) or "") if rx is _NOTE_GO else ""
                extra = (m.group(1) or "").strip() if rx is _NOTE_AT else ""
                return ({"kind": kind, "choice": choice, "extra": extra},
                        "\n".join(lines[:i] + lines[i + 1:]).strip())
    n = ticked(block)
    if n:
        return {"kind": "go", "choice": n, "extra": ""}, chef
    return None, chef


def content_hash(title: str, chef: str) -> str:
    return hashlib.sha256(f"{title.strip()}\n{chef.strip()}".encode()).hexdigest()[:16]


# --------------------------------------------------------------- Deck client

class Deck:
    def __init__(self, base: str, user: str, password: str):
        self.base = base.rstrip("/")
        self.user = user
        self.s = requests.Session()
        self.s.auth = (user, password)
        self.h = {"OCS-APIRequest": "true", "Accept": "application/json"}

    def _api(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{self.base}/index.php/apps/deck/api/v1.0{path}",
                           headers=self.h, timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    def find_board(self, title: str) -> int | None:
        for b in self._api("GET", "/boards") or []:
            if b.get("title") == title and not b.get("deletedAt") and not b.get("archived"):
                return int(b["id"])
        return None

    def board(self, bid: int) -> dict:
        return self._api("GET", f"/boards/{bid}")

    def stacks(self, bid: int) -> list[dict]:
        return self._api("GET", f"/boards/{bid}/stacks") or []

    def card(self, bid: int, sid: int, cid: int) -> dict:
        return self._api("GET", f"/boards/{bid}/stacks/{sid}/cards/{cid}")

    def set_description(self, bid: int, card: dict, description: str) -> None:
        owner = card.get("owner")
        self._api("PUT", f"/boards/{bid}/stacks/{card['stackId']}/cards/{card['id']}", json={
            "title": card["title"], "type": card.get("type") or "plain",
            "owner": owner.get("uid") if isinstance(owner, dict) else owner,
            "description": description, "order": card.get("order", 0),
            "duedate": card.get("duedate")})

    def move(self, bid: int, card: dict, sid: int) -> None:
        # The stack in the URL wins over the body's stackId, so it must be the TARGET
        # stack; with the card's current stack there the call returns 200 and moves nothing.
        self._api("PUT", f"/boards/{bid}/stacks/{sid}/cards/{card['id']}/reorder",
                  json={"order": 0, "stackId": sid})
        card["stackId"] = sid

    def create_card(self, bid: int, sid: int, title: str, description: str) -> dict:
        return self._api("POST", f"/boards/{bid}/stacks/{sid}/cards",
                         json={"title": title[:255], "type": "plain", "order": 0,
                               "description": description})

    def comment(self, cid: int, message: str) -> None:
        r = self.s.post(f"{self.base}/ocs/v2.php/apps/deck/api/v1.0/cards/{cid}/comments",
                        headers=self.h, json={"message": message[:COMMENT_MAX]}, timeout=30)
        r.raise_for_status()

    def attach(self, bid: int, card: dict, name: str, data: bytes) -> None:
        r = self.s.post(
            f"{self.base}/index.php/apps/deck/api/v1.1/boards/{bid}/stacks/{card['stackId']}"
            f"/cards/{card['id']}/attachments", headers=self.h, timeout=60,
            data={"type": "file", "data": name}, files={"file": (name, data, "text/markdown")})
        r.raise_for_status()

    def mover(self, cid: int, to_stack: str) -> str:
        """Who most recently moved the card into ``to_stack`` (Activity API), or ''."""
        r = self.s.get(f"{self.base}/ocs/v2.php/apps/activity/api/v2/activity/filter",
                       headers=self.h, timeout=30,
                       params={"object_type": "deck_card", "object_id": cid, "limit": 20})
        if r.status_code == 304 or r.status_code >= 300:
            return ""
        for a in (r.json().get("ocs") or {}).get("data") or []:
            rich = (a.get("subject_rich") or [None, {}])[1] or {}
            if "stackBefore" in rich and (rich.get("stack") or {}).get("name") == to_stack:
                return a.get("user") or ""
        return ""


# --------------------------------------------------------------- engine (skcoord)

class Engine:
    """The skcoord side: cards, runs, notes. Thin over skcapstone.agent_run."""

    def __init__(self, home: Path | None = None):
        from skcapstone import SHARED_ROOT
        from skcapstone import agent_run

        self.ar = agent_run
        self.home = Path(home or SHARED_ROOT).expanduser()

    def _store(self):
        from skcapstone.card_store import CardStore
        return CardStore(self.home)

    def ensure(self, coord_id: str, title: str, description: str, labels: list[str],
               origin: dict) -> str:
        from skcapstone.card_store import CardCore

        store = self._store()
        card = store.fold(coord_id)
        if card is None:
            store.create(CardCore(
                id=coord_id, kind="task", title=title or coord_id, description=description,
                created_by="deck", initial_labels=["deck"] + labels,
                meta={"origin": origin}))
            store.append_event(coord_id, "move", "deck-dispatcher", column="backlog")
            store.append_event(coord_id, "link", "deck-dispatcher", link_key="deck",
                               link_value=origin.get("url", ""))
        elif coord_id.startswith("deck-") and (card.title, card.description or "") != (title, description):
            store.append_event(coord_id, "describe", "deck-dispatcher", title=title,
                               description=description)
        return coord_id

    def link(self, coord_id: str, url: str) -> None:
        self._store().append_event(coord_id, "link", "deck-dispatcher", link_key="deck",
                                   link_value=url)

    def suggest(self, coord_id: str) -> list[dict]:
        res = self.ar.suggest_next_steps(self.home, coord_id, use_llm=True, timeout=60)
        out = [s for s in res.get("suggestions") or [] if s.get("text")][:3]
        if len(out) < 3:
            out += self.ar.suggest_next_steps(self.home, coord_id, use_llm=False)["suggestions"][:3 - len(out)]
        return [{"text": re.sub(r"\s+", " ", s["text"]).replace("—", ",").replace("–", "-")[:200],
                 "mode": s.get("mode", "propose")} for s in out]

    def agents(self) -> list[str]:
        fn = getattr(self.ar, "available_agents", None)
        return fn() if fn else list(self.ar.AGENTS)

    def queue(self, coord_id: str, instruction: str, agent: str, mode: str, requester: str) -> str:
        res = self.ar.request_run(self.home, coord_id, instruction, agent=agent, mode=mode,
                                  requester=requester)
        return res.get("run_id", "")

    def run(self, coord_id: str) -> dict:
        return self.ar.current_run(self.home, coord_id) or {}

    def note(self, coord_id: str, text: str) -> None:
        self._store().append_event(coord_id, "note", "deck-dispatcher", text=text)

    def change_id(self, coord_id: str, description: str) -> str:
        if coord_id.startswith("chg-"):
            return coord_id
        m = _CHG.search(description or "")
        return m.group(0) if m else ""

    def cab_vote(self, change_id: str, approver: str, evidence: str) -> str:
        from skcoord.itil import ITILManager

        mgr = ITILManager(self.home)
        vote = mgr.submit_cab_vote(change_id, agent=f"nextcloud:{approver}", decision="approved",
                                   conditions=evidence, subject=f"nextcloud:{approver}",
                                   subject_role="approver")
        return vote.decision.value


# --------------------------------------------------------------- the worker

class BoardWorker:
    def __init__(self, deck: Deck, talk, engine, state: dict, *, board_title: str, room: str,
                 approvers: set[str], hermes=None, planner=propose_plan, cab_bridge: bool = False):
        self.deck, self.talk, self.engine, self.hermes = deck, talk, engine, hermes
        self.state = state.setdefault("deck", {"cards": {}})
        self.title, self.room, self.approvers = board_title, room, approvers
        self.plan, self.cab_bridge = planner, cab_bridge
        self.me = deck.user
        self.bid: int | None = None

    # ------------------------------------------------------------ plumbing
    def card_url(self, cid: int) -> str:
        return f"{self.deck.base}/index.php/apps/deck/board/{self.bid}/card/{cid}"

    def thread_link(self, rec: dict) -> str:
        th = rec.get("thread") or {}
        return f"{self.deck.base}/call/{th['room']}#message_{th['id']}" if th.get("id") else ""

    def _post(self, rec: dict, message: str) -> str | None:
        th = rec.get("thread") or {}
        if not th.get("id"):
            return None
        return self.talk.talk_post(th["room"], message, thread_id=th["id"])

    def _move(self, card: dict, rec: dict, stack: str) -> None:
        self.deck.move(self.bid, card, self.sids[stack])
        rec["stack"] = stack

    def _write(self, card: dict, chef: str, rec: dict, log: list[str]) -> None:
        block = render_block(rec["coord"], rec.get("options") or [], log, self.thread_link(rec))
        desc = f"{chef}\n\n{block}" if chef else block
        fresh = self.deck.card(self.bid, card["stackId"], card["id"])
        if (fresh.get("description") or "") != (card.get("description") or ""):
            return  # someone edited it since we read it: try again next cycle
        self.deck.set_description(self.bid, card, desc)
        card["description"] = desc

    # ------------------------------------------------------------ cycle
    def cycle(self) -> dict:
        stats = {"cards": 0, "options": 0, "runs": 0, "moves_reverted": 0, "approved": 0, "finished": 0}
        if self.bid is None:
            self.bid = self.deck.find_board(self.title)
            if self.bid is None:
                return stats
        stacks = self.deck.stacks(self.bid)
        self.sids = {s["title"]: s["id"] for s in stacks}
        names = {s["id"]: s["title"] for s in stacks}
        seen, opt_left = set(), OPTIONS_PER_CYCLE
        for s in stacks:
            for card in s.get("cards") or []:
                if card.get("archived") or card.get("deletedAt"):
                    continue
                seen.add(str(card["id"]))
                stats["cards"] += 1
                opt_left -= self._handle(card, names[card["stackId"]], stats, opt_left)
        for cid in [c for c in self.state["cards"] if c not in seen]:
            self.state["cards"].pop(cid, None)
        self._advance(stats)
        self._approvals(stats)
        return stats

    def _handle(self, card: dict, stack: str, stats: dict, opt_left: int) -> int:
        cid = str(card["id"])
        rec = self.state["cards"].setdefault(cid, {})
        chef, block = split_notes(card.get("description") or "")
        labels = [lb.get("title", "") for lb in card.get("labels") or []]
        prod = PROD_LABEL in labels
        if not rec.get("coord"):
            m = _COORD_LINE.search(block) or _COORD_LINE.search(chef)
            rec["coord"] = m.group(1) if m else f"deck-{cid}"
        if not rec.get("options"):
            rec["options"] = block_options(block)
        if rec.get("stack") and rec["stack"] != stack:
            if not self._on_move(card, rec, rec["stack"], stack, prod, stats):
                return 0
        rec["stack"] = stack
        rec["prod"] = prod
        cmd, chef2 = parse_pick(chef, block)
        busy = rec.get("phase") in ("queued", "working", "approval", "approved")
        log = block_log(block)
        h = content_hash(card["title"], chef2)
        used = 0
        origin = {"surface": "deck", "board": self.bid, "card": int(cid), "url": self.card_url(int(cid))}

        if cmd and cmd["kind"] == "options":
            rec.pop("opts_for", None)
        want = (stack == TRIAGE or (cmd and cmd["kind"] == "options")) and rec.get("opts_for") != h
        if want and not busy and not (cmd and cmd["kind"] == "go") and opt_left > 0:
            self.engine.ensure(rec["coord"], card["title"], chef2, labels, origin)
            rec["options"] = self.engine.suggest(rec["coord"])
            rec["opts_for"] = h
            used = 1
            stats["options"] += 1
            log.append(f"- {_stamp()} next steps ready")
            self._write(card, chef2, rec, log)
            if stack == TRIAGE:
                self._move(card, rec, OPTIONS)
            return used

        if cmd and cmd["kind"] == "go":
            if busy:
                log.append(f"- {_stamp()} already working on this; pick ignored")
                rec["options"] = rec.get("options") or []
                self._write(card, chef2, rec, log)
                return used
            self._start(card, rec, chef2, labels, prod, cmd, log, origin)
            stats["runs"] += 1
        elif stack == TRIAGE and not busy and rec.get("opts_for") == h and rec.get("options"):
            self._move(card, rec, OPTIONS)  # options are written but the card sits in Triage
        return used

    # ------------------------------------------------------------ runs
    def _agent(self, labels: list[str]) -> str:
        known = set(self.engine.agents())
        for lb in labels:
            if lb.startswith("agent:") and lb[6:] in known:
                return lb[6:]
        return "lumina"

    def _start(self, card, rec, chef, labels, prod, cmd, log, origin) -> None:
        self.engine.ensure(rec["coord"], card["title"], chef, labels, origin)
        options = rec.get("options") or []
        choice = cmd["choice"]
        if choice and len(options) >= int(choice):
            opt = options[int(choice) - 1]
            instruction, mode, label = opt["text"], opt["mode"], f"option {choice}"
        else:
            instruction = "Choose the best next step for this card yourself and do it."
            mode, label = "propose", "go (the agent picks the step)"
        if cmd.get("extra"):
            instruction += f"\nChef's note: {cmd['extra']}"
        agent = self._agent(labels)
        run_id = self.engine.queue(rec["coord"], instruction, agent, mode, requester="deck:sk-work")
        if not run_id:
            log.append(f"- {_stamp()} could not queue the run")
            self._write(card, chef, rec, log)
            return
        rec.update(run_id=run_id, phase="queued", agent=agent, mode=mode)
        self._open_thread(card, rec, chef, label, instruction, prod)
        log.append(f"- {_stamp()} queued {label} ({mode}) for {agent}")
        self._write(card, chef, rec, log)
        self._move(card, rec, QUEUED)

    def _open_thread(self, card, rec, chef, label, instruction, prod) -> None:
        if not self.room:
            return
        plan = self.plan(card["title"], chef, instruction)
        first = (f"You picked {label}: {instruction}\nAgent: {rec['agent']} ({rec['mode']})\n"
                 f"Plan:\n{plan}\nCard: {self.card_url(card['id'])}"
                 + ("\nThis card is labelled prod, so it stops for your approval before anything is applied."
                    if prod else ""))
        th = rec.get("thread") or {}
        if th.get("id") and th.get("room") == self.room:
            info = self.talk.thread_info(self.room, th["id"])
            title = ((info.get("thread") or {}).get("title") or "")
            if title.startswith(CLOSED_MARK):
                self.talk.thread_set(self.room, th["id"], title=title[len(CLOSED_MARK):])
            self.talk.thread_set(self.room, th["id"], level=NOTIFY_DEFAULT)
            if self.talk.talk_post(self.room, first, thread_id=th["id"]):
                return
        mid = self.talk.talk_post(self.room, first, title=f"Card: {card['title']}")
        if mid:
            rec["thread"] = {"room": self.room, "id": mid}

    def _close_thread(self, rec: dict, message: str) -> None:
        th = rec.get("thread") or {}
        if not th.get("id"):
            return
        self.talk.talk_post(th["room"], message, thread_id=th["id"])
        title = ((self.talk.thread_info(th["room"], th["id"]).get("thread") or {}).get("title") or "")
        if title and not title.startswith(CLOSED_MARK):
            self.talk.thread_set(th["room"], th["id"], title=CLOSED_MARK + title)
        self.talk.thread_set(th["room"], th["id"], level=NOTIFY_NEVER)

    def _card(self, cid: str) -> dict | None:
        for s in self.deck.stacks(self.bid):
            for c in s.get("cards") or []:
                if str(c["id"]) == cid:
                    return c
        return None

    def _advance(self, stats: dict) -> None:
        for cid, rec in list(self.state["cards"].items()):
            if rec.get("phase") in ("queued", "working") and rec.get("run_id"):
                self._advance_run(cid, rec, stats)
            elif rec.get("phase") == "approved" and rec.get("hermes_run") and self.hermes:
                self._advance_hermes(cid, rec, stats)

    def _advance_run(self, cid: str, rec: dict, stats: dict) -> None:
        run = self.engine.run(rec["coord"])
        if run.get("run_id") != rec["run_id"]:
            return
        state = run.get("state")
        card = None
        if state == "running" and rec["phase"] == "queued":
            card = self._card(cid)
            if card:
                self._move(card, rec, WORKING)
            rec["phase"] = "working"
            self._post(rec, f"{rec.get('agent', 'lumina')} is working on it now.")
            return
        if state not in ("needs-review", "failed", "done"):
            return
        card = card or self._card(cid)
        if card is None:
            return
        responses = [a.get("text", "") for a in run.get("activity") or [] if a.get("atype") == "response"]
        summary = (responses[-1] if responses else run.get("last_error") or "no output").strip()
        links = run.get("links") or {}
        extra = "".join(f"\n{k}: {v}" for k, v in links.items() if v)
        ok = state != "failed"
        head = "Ready for review." if ok else "The run failed."
        self._post(rec, f"{head}\n{summary[:TALK_MAX]}{extra}")
        short = summary if len(summary) <= COMMENT_MAX - 60 else summary[:COMMENT_MAX - 120] + " ... (full report attached)"
        self.deck.comment(card["id"], f"{head} {short}{extra}"[:COMMENT_MAX])
        if len(summary) > COMMENT_MAX - 60:
            self.deck.attach(self.bid, card, f"report-{rec['run_id']}.md",
                             f"# {card['title']}\n\n{summary}\n{extra}\n".encode())
        chef, block = split_notes(card.get("description") or "")
        log = block_log(block) + [f"- {_stamp()} {'ready for review' if ok else 'failed'}: {summary[:120]}"]
        rec.pop("run_id", None)
        stats["finished"] += 1
        if not ok:
            rec["phase"] = "idle"
            self._write(card, chef, rec, log)
            self._move(card, rec, OPTIONS)
        elif rec.get("prod"):
            rec["phase"] = "approval"
            mid = self._post(rec, f"This is prod work. React {CHECK} here to approve it, or move the "
                                  "card to Approved. Only Chef or Casey can approve.")
            rec["approval_msg"] = mid
            log.append(f"- {_stamp()} waiting for approval")
            self._write(card, chef, rec, log)
            self._move(card, rec, NEEDS_APPROVAL)
        else:
            rec["phase"] = "done"
            self._write(card, chef, rec, log)
            self._move(card, rec, DONE)
            self._close_thread(rec, "Done. Pick another step on the card, or write here, to pick it back up.")

    def _advance_hermes(self, cid: str, rec: dict, stats: dict) -> None:
        st = self.hermes.status(rec["hermes_run"])
        if not st or st.get("status") in ("queued", "running", "awaiting_approval", "stopping"):
            return
        res = (parse_status(str(st.get("output") or "")) if st.get("status") == "completed"
               else {"state": "blocked", "note": f"run {st.get('status')}"})
        card = self._card(cid)
        rec.pop("hermes_run", None)
        rec["phase"] = "applied"
        stats["finished"] += 1
        if card:
            self.deck.comment(card["id"], f"Approved work: {res['state']}. {res['note']}"[:COMMENT_MAX])
            chef, block = split_notes(card.get("description") or "")
            self._write(card, chef, rec, block_log(block) + [f"- {_stamp()} approved work {res['state']}: {res['note'][:120]}"])
        self._post(rec, f"Approved work finished ({res['state']}): {res['note']}\n"
                        "Move the card to Done when you are happy with it.")

    # ------------------------------------------------------------ approval
    def _on_move(self, card, rec, frm: str, to: str, prod: bool, stats: dict) -> bool:
        """React to a move someone else made. False when it was moved back."""
        guarded = to == APPROVED or (to == DONE and prod)
        if not guarded:
            if to == DONE and rec.get("phase") in ("approval", "approved", "applied"):
                rec["phase"] = "done"
            return True
        who = self.deck.mover(card["id"], to)
        if who and who in self.approvers:
            if to == APPROVED and rec.get("phase") == "approval":
                rec["stack"] = to
                self._approve(card, rec, who, "moved the card to Approved on SK Work")
                stats["approved"] += 1
            elif to == DONE:
                rec["phase"] = "done"
                self._close_thread(rec, f"Closed: {who} moved the card to Done.")
            return True
        self.deck.move(self.bid, card, self.sids[frm])
        rec["stack"] = frm
        stats["moves_reverted"] += 1
        note = (f"Moved back to {frm}: only Chef or Casey can move cards to {to}"
                + (f" ({who or 'unknown'} tried)." if who != self.me else " (an agent tried)."))
        self.deck.comment(card["id"], note)
        self._post(rec, note)
        return False

    def _approvals(self, stats: dict) -> None:
        for cid, rec in list(self.state["cards"].items()):
            if rec.get("phase") != "approval" or not rec.get("approval_msg"):
                continue
            th = rec.get("thread") or {}
            reacts = self.talk.reactions(th.get("room"), rec["approval_msg"]).get(CHECK) or []
            humans = [r.get("actorId") for r in reacts if r.get("actorType") == "users"]
            ok = [u for u in humans if u in self.approvers]
            if ok:
                card = self._card(cid)
                if card:
                    self._approve(card, rec, ok[0], f"reacted {CHECK} in Talk (message {rec['approval_msg']})")
                    self._move(card, rec, APPROVED)
                    stats["approved"] += 1
            elif humans and not rec.get("warned"):
                rec["warned"] = True
                self._post(rec, "Only Chef or Casey can approve prod work, so that reaction does not count.")

    def _approve(self, card, rec, who: str, how: str) -> None:
        rec["phase"] = "approved"
        evidence = f"Approved by {who}: {how} (SK Work card {card['id']})"
        self.engine.note(rec["coord"], evidence)
        chg = self.engine.change_id(rec["coord"], card.get("description") or "")
        cab = ""
        if chg and self.cab_bridge:
            try:
                cab = f" CAB vote recorded on {chg}: {self.engine.cab_vote(chg, who, evidence)}."
            except Exception as exc:  # noqa: BLE001
                cab = f" Could not record the CAB vote on {chg}: {exc}."
        elif chg:
            cab = (f" This card is linked to change {chg}: your approval is recorded on the card, "
                   "but the change still needs its CAB vote (dashboard or signed grant).")
        self.deck.comment(card["id"], (evidence + "." + cab)[:COMMENT_MAX])
        msg = f"Approved by {who}.{cab}"
        if self.hermes:
            th = rec.get("thread") or {}
            prompt = (
                "[SK Work board] Chef's approver approved this prod card. Carry out the approved plan "
                f"now with your normal tools and rules.\nCard: {card['title']}\n"
                f"Card link: {self.card_url(card['id'])}\nCoord card: {rec['coord']}\n"
                + (f"Talk thread: thread_id {th['id']} in room {th['room']}; post progress there with "
                   f"talk_thread_reply(room=\"{th['room']}\", thread_id=\"{th['id']}\").\n" if th.get("id") else "")
                + "Do not move the card to Done; a human does that.\nEnd with exactly one line:\n"
                'TASK_STATUS {"state": "done|working|needs_chef|blocked", "percent": 0-100, "note": "one short line"}')
            run = self.hermes.start(f"card-{card['id']}", prompt)
            if run:
                rec["hermes_run"] = run
                msg += f" {rec.get('agent', 'lumina')} is carrying out the approved plan now."
        self._post(rec, msg)


# --------------------------------------------------------------- talk extras

def add_reactions(dav) -> None:
    """Give the task-trigger Dav client a reactions() call (Talk reactions API)."""
    def reactions(room: str | None, message_id: str | None) -> dict:
        if not room or not message_id:
            return {}
        code, data = dav._ocs("GET", f"/reaction/{room}/{message_id}")
        return data if code < 300 and isinstance(data, dict) else {}
    dav.reactions = reactions


# --------------------------------------------------------------- migration

def migrate(worker: BoardWorker, engine: Engine, days: int = 14, dry_run: bool = False) -> list[str]:
    """Put the coord cards touched in the last ``days`` on the board (Triage), linked both ways."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    linked = {r.get("coord") for r in worker.state["cards"].values()}
    if worker.bid is None:
        worker.bid = worker.deck.find_board(worker.title)
    sids = {s["title"]: s["id"] for s in worker.deck.stacks(worker.bid)}
    out = []
    for card in engine._store().list_cards(include_archived=False):
        try:
            ts = datetime.fromisoformat(str(card.updated_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff or card.status.value == "done" or card.id in linked or card.links.get("deck"):
            continue
        if card.id.startswith("deck-"):
            continue
        out.append(card.id)
        if dry_run:
            continue
        desc = ((card.description or "").strip()[:1500]
                + f"\n\nKind: {card.kind.value}, coord status: {card.status.value}\n\n"
                + render_block(card.id, [], [f"- {_stamp()} moved here from the coord board"]))
        dc = worker.deck.create_card(worker.bid, sids[TRIAGE], card.title, desc.strip())
        engine.link(card.id, worker.card_url(dc["id"]))
        worker.state["cards"][str(dc["id"])] = {"coord": card.id, "stack": TRIAGE}
    return out


# --------------------------------------------------------------- wiring

def build(dav, hermes, state: dict) -> BoardWorker | None:
    e = os.environ
    title = e.get("SKOS_WB_BOARD", "SK Work")
    if not title:
        return None
    add_reactions(dav)
    deck = Deck(e["NEXTCLOUD_TALK_SERVER_URL"], e["NEXTCLOUD_TALK_USERNAME"], e["NEXTCLOUD_TALK_APP_PASSWORD"])
    rooms = dict(p.split("=", 1) for p in e.get("SKOS_TT_ROOMS", "").split(",") if "=" in p)
    room = e.get("SKOS_WB_ROOM") or rooms.get("dknestri", "")
    approvers = {u.strip() for u in e.get("SKOS_WB_APPROVERS", "dknestri,caseyK").split(",") if u.strip()}
    approvers.discard(deck.user)  # the agent account can never approve
    return BoardWorker(deck, dav, Engine(), state, board_title=title, room=room, approvers=approvers,
                       hermes=hermes, cab_bridge=e.get("SKOS_WB_CAB_BRIDGE", "1") != "0")


def main(argv: list[str] | None = None) -> int:
    import sys

    from skos import task_triggers as tt

    argv = sys.argv[1:] if argv is None else argv
    w = tt.build_worker()
    board = build(w.dav, w.hermes, w.state)
    if board is None:
        print("SKOS_WB_BOARD is empty: board disabled")
        return 1
    if "--migrate" in argv:
        ids = migrate(board, board.engine, dry_run="--dry-run" in argv)
        w.save()
        print(json.dumps({"migrated": ids, "count": len(ids)}))
        return 0
    print(json.dumps(board.cycle()))
    w.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
