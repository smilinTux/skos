"""task_triggers: Lumina proposes next steps on Nextcloud Tasks and runs the one Chef picks.

Watches the GTD task lists shared with the agent account (CalDAV VTODO on the
skhub). For each open task Chef adds or edits, the agent writes three concrete
next-step options into a block it owns at the end of the task's notes:

    == Lumina ==
    Next steps:
    1) ...
    2) ...
    3) ...
    Pick: put "go 1", "go 2" or "go 3" on its own line in these notes, or "go" to let me choose.
    Log:
    - Oct 30 14:05 started option 2
    == end ==

Chef triggers work with a plain-text command, which survives every task app
(iPhone Reminders drops tags, so tags are optional):
    notes line   "go", "go 2", "options", "@lumina <instructions>"
    title prefix "go 2 ...", "@lumina ..."
    category     GO, GO-1..GO-3, LUMINA (Android apps)
The command is consumed (removed) once picked up, and the block log says so.
"go" starts a run through Lumina's Hermes API server (/v1/runs): she works the
step with her normal tools and approval rules, and opens a Talk thread in the
list owner's DM for anything that needs Chef. When the run ends, its status
line is written back (log line, STATUS, PERCENT-COMPLETE).

Trigger: CalDAV sync-collection polling (one tiny REPORT per list per cycle).
Loop guard: content hashing. Only the part of the task outside the agent's
block counts as "Chef's"; the agent's own writes never change that hash, so
they never retrigger anything. Someday lists get no options unless asked.

Env (Lumina's ~/.hermes/.env supplies the first three and API_SERVER_KEY):
    NEXTCLOUD_TALK_SERVER_URL, NEXTCLOUD_TALK_USERNAME, NEXTCLOUD_TALK_APP_PASSWORD
    API_SERVER_KEY, SKOS_TT_HERMES_URL (default http://127.0.0.1:8642)
    SKOS_TT_ROOMS       owner=room map for Talk threads, e.g. "dknestri=gaquurgh"
    SKOS_TT_LISTS       comma list of calendar slugs; default every list named "GTD ..."
    SKOS_TT_STATE       state file (default ~/.local/state/skos/task-triggers.json)
    SKGATEWAY_URL / SKGATEWAY_MODEL as everywhere else.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

BLOCK_START = "== Lumina =="
BLOCK_END = "== end =="
PICK_HINT = ('Pick: put "go 1", "go 2" or "go 3" on its own line in these notes, '
             'or "go" to let me choose.')
OPTIONS_PER_CYCLE = 6
OPTIONS_PER_HOUR = 60
RUNS_PER_HOUR = 6
MAX_INFLIGHT_RUNS = 2
LOG_KEEP = 8
POLL_SECONDS = 45

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}

_NOTE_GO = re.compile(r"^\s*go(?:\s*([1-3]))?\s*[.!]?\s*$", re.I)
_NOTE_OPTS = re.compile(r"^\s*(?:options|opts)\s*\??\s*$", re.I)
_NOTE_AT = re.compile(r"^\s*@lumina\b[\s:,]*(.*)$", re.I)
_TITLE_GO = re.compile(r"^\s*go\s*([1-3])\s*[:.\-]?\s+", re.I)
_TITLE_AT = re.compile(r"^\s*@lumina\b[\s:,]*", re.I)
_CAT_CMDS = {"GO": "", "GO-1": "1", "GO-2": "2", "GO-3": "3", "LUMINA": ""}
_STATUS_LINE = re.compile(r"TASK_STATUS\s*(\{.*\})", re.S)


# --------------------------------------------------------------- iCalendar bits

def _unfold(ics: str) -> list[str]:
    out: list[str] = []
    for raw in ics.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _fold(line: str) -> str:
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not parts else 74
        if len(cur) + len(b) > limit:
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _unescape(v: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _escape(v: str) -> str:
    return (v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\n", "\\n"))


def _split_prop(line: str) -> tuple[str, str, str]:
    """('NAME', ';params' or '', 'value'), ignoring colons inside quoted params."""
    inq = False
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif ch == ":" and not inq:
            head, value = line[:i], line[i + 1:]
            name, _, params = head.partition(";")
            return name.upper(), (";" + params if params else ""), value
    return line.upper(), "", ""


class Todo:
    """The first VTODO in a calendar object, edited in place (other lines kept verbatim)."""

    def __init__(self, ics: str):
        self.lines = _unfold(ics)
        try:
            self.start = self.lines.index("BEGIN:VTODO")
            self.end = self.lines.index("END:VTODO", self.start)
        except ValueError as exc:
            raise ValueError("no VTODO in object") from exc

    def get(self, name: str) -> str | None:
        for ln in self.lines[self.start + 1:self.end]:
            n, _, v = _split_prop(ln)
            if n == name:
                return v
        return None

    def text(self, name: str) -> str:
        v = self.get(name)
        return _unescape(v) if v is not None else ""

    def categories(self) -> list[str]:
        cats: list[str] = []
        for ln in self.lines[self.start + 1:self.end]:
            n, _, v = _split_prop(ln)
            if n == "CATEGORIES":
                cats += [_unescape(c).strip() for c in re.split(r"(?<!\\),", v) if c.strip()]
        return cats

    def set(self, name: str, value: str | None, *, raw: bool = False) -> None:
        body = self.lines[self.start + 1:self.end]
        keep = [ln for ln in body if _split_prop(ln)[0] != name]
        if value is not None:
            keep.append(f"{name}:{value if raw else _escape(value)}")
        self.lines[self.start + 1:self.end] = keep
        self.end = self.start + 1 + len(keep)

    def set_categories(self, cats: list[str]) -> None:
        self.set("CATEGORIES", ",".join(_escape(c) for c in cats) if cats else None, raw=True)

    def dump(self) -> str:
        return "\r\n".join(_fold(ln) for ln in self.lines) + "\r\n"


# --------------------------------------------------------------- notes block

def split_notes(desc: str) -> tuple[str, str]:
    """(Chef's text, the agent's block or '')."""
    i = desc.find(BLOCK_START)
    if i < 0:
        return desc.rstrip(), ""
    j = desc.find(BLOCK_END, i)
    j = len(desc) if j < 0 else j + len(BLOCK_END)
    return (desc[:i] + desc[j:]).rstrip(), desc[i:j]


def block_log(block: str) -> list[str]:
    if "Log:" not in block:
        return []
    tail = block.split("Log:", 1)[1].replace(BLOCK_END, "")
    return [ln.strip() for ln in tail.splitlines() if ln.strip().startswith("- ")]


def render_block(options: list[str], log: list[str]) -> str:
    out = [BLOCK_START]
    if options:
        out.append("Next steps:")
        out += [f"{n}) {o}" for n, o in enumerate(options, 1)]
        out.append(PICK_HINT)
    if log:
        out.append("Log:")
        out += log[-LOG_KEEP:]
    out.append(BLOCK_END)
    return "\n".join(out)


def join_notes(chef: str, block: str) -> str:
    return f"{chef}\n\n{block}" if chef else block


def parse_command(summary: str, chef: str, cats: list[str]) -> tuple[dict | None, str, str, list[str]]:
    """Find one trigger. Returns (cmd, summary, chef, cats) with the command removed.

    cmd = {"kind": "go"|"options", "choice": "1".."3"|"", "extra": str}
    """
    m = _TITLE_GO.match(summary)
    if m:
        return {"kind": "go", "choice": m.group(1), "extra": ""}, summary[m.end():].strip(), chef, cats
    m = _TITLE_AT.match(summary)
    if m and summary[m.end():].strip():
        return {"kind": "go", "choice": "", "extra": ""}, summary[m.end():].strip(), chef, cats
    lines = chef.splitlines()
    for i, ln in enumerate(lines):
        for rx, kind in ((_NOTE_GO, "go"), (_NOTE_OPTS, "options"), (_NOTE_AT, "go")):
            mm = rx.match(ln)
            if not mm:
                continue
            choice = (mm.group(1) or "") if rx is _NOTE_GO else ""
            extra = (mm.group(1) or "").strip() if rx is _NOTE_AT else ""
            rest = "\n".join(lines[:i] + lines[i + 1:]).strip()
            return {"kind": kind, "choice": choice, "extra": extra}, summary, rest, cats
    for c in cats:
        if c.upper() in _CAT_CMDS:
            return ({"kind": "go", "choice": _CAT_CMDS[c.upper()], "extra": ""}, summary, chef,
                    [x for x in cats if x != c])
    return None, summary, chef, cats


def chef_hash(summary: str, chef: str) -> str:
    return hashlib.sha256(f"{summary.strip()}\n{chef.strip()}".encode()).hexdigest()[:16]


def _stamp(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time()).strftime("%b %d %H:%M")


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------- CalDAV client

class Dav:
    def __init__(self, base: str, user: str, password: str):
        self.base = base.rstrip("/")
        self.user = user
        self.s = requests.Session()
        self.s.auth = (user, password)
        self.home = f"/remote.php/dav/calendars/{user}/"

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        return self.s.request(method, self.base + path, timeout=30, **kw)

    def lists(self) -> list[dict]:
        body = ('<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
                '<d:displayname/><c:supported-calendar-component-set/></d:prop></d:propfind>')
        r = self._req("PROPFIND", self.home, data=body, headers={"Depth": "1"})
        r.raise_for_status()
        out = []
        for resp in ET.fromstring(r.content).findall("d:response", NS):
            href = resp.findtext("d:href", "", NS)
            name = resp.findtext(".//d:displayname", "", NS) or ""
            comps = [c.get("name") for c in resp.findall(".//c:comp", NS)]
            if "VTODO" in comps:
                out.append({"href": href, "name": name})
        return out

    def sync(self, href: str, token: str | None) -> tuple[str, list[str], list[str]]:
        """(new token, changed hrefs, removed hrefs) since token (all objects when None)."""
        body = ('<?xml version="1.0"?><d:sync-collection xmlns:d="DAV:">'
                f'<d:sync-token>{token or ""}</d:sync-token><d:sync-level>1</d:sync-level>'
                '<d:prop><d:getetag/></d:prop></d:sync-collection>')
        r = self._req("REPORT", href, data=body,
                      headers={"Depth": "1", "Content-Type": "application/xml"})
        if r.status_code in (403, 409, 412) and token:
            return self.sync(href, None)  # token expired: full resync
        r.raise_for_status()
        root = ET.fromstring(r.content)
        changed, removed = [], []
        for resp in root.findall("d:response", NS):
            h = resp.findtext("d:href", "", NS)
            status = resp.findtext("d:status", "", NS) or resp.findtext(".//d:status", "", NS)
            if not h.endswith(".ics"):
                continue
            (removed if "404" in status else changed).append(h)
        return root.findtext("d:sync-token", "", NS), changed, removed

    def get(self, href: str) -> tuple[str, str] | None:
        r = self._req("GET", href)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text, r.headers.get("ETag", "")

    def put(self, href: str, ics: str, etag: str) -> bool:
        """False on a conflict (someone edited it since we read it); retried next cycle."""
        r = self._req("PUT", href, data=ics.encode("utf-8"),
                      headers={"If-Match": etag, "Content-Type": "text/calendar; charset=utf-8"})
        if r.status_code == 412:
            return False
        r.raise_for_status()
        return True


# --------------------------------------------------------------- model + agent

def propose_options(summary: str, notes: str, list_name: str, due: str) -> list[str] | None:
    """Three concrete next steps from skgateway, or None on any failure."""
    url = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")
    model = os.environ.get("SKGATEWAY_MODEL", "sk-default")
    prompt = (
        "You help Chef with his GTD task list. Propose exactly 3 distinct, concrete next steps "
        "for the task below. Each is one short imperative line (max 110 characters), doable "
        "soon, and clearly different from the others (for example: do it now, delegate or ask, "
        "research first). Where Lumina, his AI agent with web, email, files and Talk, could do "
        "the step herself, say so starting with 'Lumina:'. Never use em or en dashes.\n\n"
        f"List: {list_name}\nTask: {summary}\nDue: {due or 'none'}\nNotes:\n{notes[:2000] or '(none)'}\n\n"
        'Answer JSON only: {"options": ["...", "...", "..."]}')
    payload = {
        "model": model, "temperature": 0.3, "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "next_steps", "schema": {
                "type": "object", "required": ["options"],
                "properties": {"options": {"type": "array", "minItems": 3, "maxItems": 3,
                                           "items": {"type": "string"}}}}}},
    }
    try:
        r = requests.post(f"{url}/chat/completions", json=payload, timeout=120)
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
        m = re.search(r"\{.*\}", content, re.S)
        opts = json.loads(m.group(0))["options"] if m else None
    except Exception:
        return None
    if not isinstance(opts, list) or len(opts) != 3:
        return None
    clean = [re.sub(r"\s+", " ", str(o)).replace("—", ",").replace("–", "-").strip()[:160]
             for o in opts]
    return clean if all(clean) else None


class Hermes:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def start(self, session_id: str, text: str) -> str | None:
        r = requests.post(f"{self.url}/v1/runs", headers=self.h, timeout=30,
                          json={"input": text, "session_id": session_id})
        if r.status_code >= 300:
            return None
        return r.json().get("run_id") or r.json().get("id")

    def status(self, run_id: str) -> dict | None:
        r = requests.get(f"{self.url}/v1/runs/{run_id}", headers=self.h, timeout=30)
        if r.status_code == 404:
            return {"status": "failed", "error": "run not found (gateway restarted?)"}
        if r.status_code >= 300:
            return None
        return r.json()


def run_prompt(task: dict, option: str, extra: str, room: str) -> str:
    where = (f"Talk room {room} (Chef's direct message with you; pass room=\"{room}\")"
             if room else "no Talk room is configured for this list, so do not post to Talk")
    return (
        "[Task trigger from Nextcloud Tasks] Chef asked you to work on one of his GTD tasks.\n"
        f"Task: {task['summary']}\nList: {task['list']}\nDue: {task.get('due') or 'none'}\n"
        f"Task id: {task['uid']}\nNotes:\n{task['notes'] or '(none)'}\n\n"
        + (f"Chosen next step: {option}\n" if option else
           "Chef said \"go\" without picking: choose the best next step yourself.\n")
        + (f"Chef's extra instructions: {extra}\n" if extra else "")
        + "\nDo the step now if you can with your tools, following your normal approval rules: "
        "anything irreversible, outward-facing or costly needs Chef's OK first. "
        f"Where to talk to Chef: {where}. If you need him, or the work is more than a quick "
        "step, open a Talk thread there with talk_thread_start titled with the task, put your "
        "progress and any question in it (a poll for a choice, a one-line question he can answer "
        "with a reaction for yes or no). Do not message him when nothing is needed.\n"
        "Do not edit or delete the task itself; this trigger writes your status back.\n"
        "End your reply with exactly one line:\n"
        'TASK_STATUS {"state": "done|working|needs_chef|blocked", "percent": 0-100, '
        '"note": "one short line for the task log"}')


def parse_status(output: str) -> dict:
    m = None
    for m in _STATUS_LINE.finditer(output or ""):
        pass
    if m:
        try:
            st = json.loads(m.group(1).splitlines()[0])
            state = st.get("state") if st.get("state") in ("done", "working", "needs_chef", "blocked") else "working"
            pct = max(0, min(100, int(st.get("percent", 0))))
            note = re.sub(r"\s+", " ", str(st.get("note", ""))).strip()[:140]
            return {"state": state, "percent": pct, "note": note}
        except Exception:
            pass
    tail = re.sub(r"\s+", " ", (output or "").strip())[-140:]
    return {"state": "working", "percent": 0, "note": tail or "finished without a status line"}


# --------------------------------------------------------------- the worker

class Worker:
    def __init__(self, dav: Dav, hermes: Hermes | None, state_path: Path, *,
                 rooms: dict[str, str], only: list[str] | None = None, proposer=propose_options):
        self.dav, self.hermes, self.path = dav, hermes, state_path
        self.rooms, self.only, self.propose = rooms, only, proposer
        self.state = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {"lists": {}, "tasks": {}, "events": []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
        tmp.replace(self.path)

    def _budget(self, kind: str, per_hour: int) -> bool:
        now = time.time()
        ev = [e for e in self.state.get("events", []) if now - e[1] < 3600]
        self.state["events"] = ev
        if sum(1 for k, _ in ev if k == kind) >= per_hour:
            return False
        ev.append([kind, now])
        return True

    def watched(self) -> list[dict]:
        out = []
        for lst in self.dav.lists():
            slug = lst["href"].rstrip("/").rsplit("/", 1)[-1]
            if self.only is not None:
                if slug not in self.only:
                    continue
            elif not lst["name"].upper().startswith("GTD "):
                continue
            owner = slug.split("_shared_by_", 1)[1] if "_shared_by_" in slug else self.dav.user
            lst.update(slug=slug, owner=owner, someday="someday" in (slug + lst["name"]).lower())
            out.append(lst)
        return out

    def cycle(self) -> dict:
        stats = {"seen": 0, "options": 0, "runs": 0, "writes": 0, "conflicts": 0, "finished": 0}
        opt_left = OPTIONS_PER_CYCLE
        for lst in self.watched():
            token = self.state["lists"].get(lst["href"])
            new_token, changed, removed = self.dav.sync(lst["href"], token)
            for h in removed:
                self.state["tasks"].pop(h, None)
            for h in changed:
                stats["seen"] += 1
                opt_left -= self._handle(lst, h, stats, opt_left)
            self.state["lists"][lst["href"]] = new_token
            self.save()
        self._finish_runs(stats)
        self.save()
        return stats

    def _handle(self, lst: dict, href: str, stats: dict, opt_left: int) -> int:
        """Process one changed task. Returns how many option generations it used."""
        got = self.dav.get(href)
        if got is None:
            return 0
        ics, etag = got
        try:
            todo = Todo(ics)
        except ValueError:
            return 0
        if (todo.get("STATUS") or "").upper() in ("COMPLETED", "CANCELLED"):
            return 0
        uid = todo.get("UID") or href
        rec = self.state["tasks"].setdefault(href, {"uid": uid})
        summary = todo.text("SUMMARY")
        chef, block = split_notes(todo.text("DESCRIPTION"))
        cats = todo.categories()
        cmd, summary2, chef2, cats2 = parse_command(summary, chef, cats)
        log = block_log(block)
        options = rec.get("options") or []
        h = chef_hash(summary2, chef2)
        used = 0
        dirty = False

        if cmd and cmd["kind"] == "options":
            options, dirty = [], True
            rec.pop("opts_for", None)
        want_opts = (not lst["someday"] or (cmd and cmd["kind"] == "options"))
        if want_opts and rec.get("opts_for") != h and not (cmd and cmd["kind"] == "go"):
            if opt_left > 0 and self._budget("options", OPTIONS_PER_HOUR):
                new = self.propose(summary2, chef2, lst["name"], todo.get("DUE") or "")
                used = 1
                if new:
                    options, rec["options"], rec["opts_for"] = new, new, h
                    dirty = True

        if cmd and cmd["kind"] == "go":
            inflight = sum(1 for r in self.state["tasks"].values() if r.get("run_id"))
            if rec.get("run_id"):
                log.append(f"- {_stamp()} already working on this; command ignored")
            elif inflight >= MAX_INFLIGHT_RUNS or not self.hermes or not self._budget("run", RUNS_PER_HOUR):
                return used  # leave the command in place; picked up on a later cycle
            else:
                choice = cmd["choice"]
                option = options[int(choice) - 1] if choice and len(options) >= int(choice) else ""
                task = {"uid": uid, "summary": summary2, "notes": chef2, "list": lst["name"],
                        "due": todo.get("DUE") or ""}
                room = self.rooms.get(lst["owner"], "")
                run_id = self.hermes.start(f"task-{uid}", run_prompt(task, option, cmd["extra"], room))
                if not run_id:
                    log.append(f"- {_stamp()} could not start: agent API unavailable")
                else:
                    rec["run_id"] = run_id
                    label = f"option {choice}" if option else "my pick"
                    log.append(f"- {_stamp()} started {label}")
                    todo.set("STATUS", "IN-PROCESS", raw=True)
                    todo.set("PERCENT-COMPLETE", str(max(10, int(todo.get("PERCENT-COMPLETE") or 0))), raw=True)
                    stats["runs"] += 1
            dirty = True
        elif not block and options and rec.get("opts_for") == h:
            dirty = True  # the block was stripped by another writer: put it back, no model call

        if cmd:  # consume the command so it never fires twice
            summary, chef, cats = summary2, chef2, cats2
            todo.set("SUMMARY", summary)
            todo.set_categories(cats)
            dirty = True
        if dirty:
            if self._write(href, todo, chef, render_block(options, log), etag):
                stats["writes"] += 1
            else:
                stats["conflicts"] += 1  # retried next cycle; a started run keeps its run_id
        if used:
            stats["options"] += 1
        return used

    def _write(self, href: str, todo: Todo, chef: str, block: str, etag: str) -> bool:
        todo.set("DESCRIPTION", join_notes(chef, block))
        todo.set("LAST-MODIFIED", _utc(), raw=True)
        todo.set("DTSTAMP", _utc(), raw=True)
        seq = todo.get("SEQUENCE")
        if seq and seq.isdigit():
            todo.set("SEQUENCE", str(int(seq) + 1), raw=True)
        return self.dav.put(href, todo.dump(), etag)

    def _finish_runs(self, stats: dict) -> None:
        if not self.hermes:
            return
        for href, rec in self.state["tasks"].items():
            run_id = rec.get("run_id")
            if not run_id:
                continue
            st = self.hermes.status(run_id)
            if not st or st.get("status") in ("queued", "running", "awaiting_approval", "stopping"):
                continue
            if st.get("status") == "completed":
                res = parse_status(str(st.get("output") or ""))
            else:
                res = {"state": "blocked", "percent": 0,
                       "note": f"run {st.get('status')}: {str(st.get('error') or '')[:100]}"}
            got = self.dav.get(href)
            if got is None:
                rec.pop("run_id", None)
                continue
            ics, etag = got
            todo = Todo(ics)
            chef, block = split_notes(todo.text("DESCRIPTION"))
            log = block_log(block) + [f"- {_stamp()} {res['state'].replace('_', ' ')}: {res['note']}"]
            if res["state"] == "done":
                todo.set("STATUS", "COMPLETED", raw=True)
                todo.set("PERCENT-COMPLETE", "100", raw=True)
                todo.set("COMPLETED", _utc(), raw=True)
            elif res["percent"]:
                todo.set("PERCENT-COMPLETE", str(res["percent"]), raw=True)
            if self._write(href, todo, chef, render_block(rec.get("options") or [], log), etag):
                rec.pop("run_id", None)
                rec["last_result"] = res
                stats["finished"] += 1
            else:
                stats["conflicts"] += 1


# --------------------------------------------------------------- entry point

def _rooms(spec: str) -> dict[str, str]:
    return dict(p.split("=", 1) for p in spec.split(",") if "=" in p)


def build_worker() -> Worker:
    e = os.environ
    dav = Dav(e["NEXTCLOUD_TALK_SERVER_URL"], e["NEXTCLOUD_TALK_USERNAME"], e["NEXTCLOUD_TALK_APP_PASSWORD"])
    key = e.get("API_SERVER_KEY", "")
    hermes = Hermes(e.get("SKOS_TT_HERMES_URL", "http://127.0.0.1:8642"), key) if key else None
    only = [s.strip() for s in e["SKOS_TT_LISTS"].split(",") if s.strip()] if e.get("SKOS_TT_LISTS") else None
    state = Path(e.get("SKOS_TT_STATE", "~/.local/state/skos/task-triggers.json")).expanduser()
    return Worker(dav, hermes, state, rooms=_rooms(e.get("SKOS_TT_ROOMS", "")), only=only)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    w = build_worker()
    if "--once" in argv:
        print(json.dumps(w.cycle()))
        return 0
    while True:
        try:
            st = w.cycle()
            if any(st[k] for k in ("options", "runs", "writes", "conflicts", "finished")):
                print(json.dumps(st), flush=True)
        except Exception as exc:  # network blips: log and keep going
            print(f"cycle failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
