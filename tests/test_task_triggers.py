"""task_triggers: options block, plain-text triggers, loop guard, run write-back."""
from __future__ import annotations

from skos import task_triggers as tt

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\nBEGIN:VTODO\r\nUID:{uid}\r\n"
    "SUMMARY:{summary}\r\nDESCRIPTION:{desc}\r\n{extra}STATUS:NEEDS-ACTION\r\n"
    "END:VTODO\r\nEND:VCALENDAR\r\n")


def mk(uid="t1", summary="Renew car registration", desc="", extra=""):
    return ICS.format(uid=uid, summary=tt._escape(summary), desc=tt._escape(desc), extra=extra)


class FakeDav:
    user = "lumina"

    def __init__(self, lists):
        self.lists_ = lists  # {href: {"name":..., "objs": {href: ics}}}
        self.etag = 0
        self.tags = {}
        self.changed = {h: set(lst_["objs"]) for h, lst_ in lists.items()}
        self.conflict_next = False
        self.threads, self.msgs = {}, []

    def lists(self):
        return [{"href": h, "name": lst_["name"]} for h, lst_ in self.lists_.items()]

    def sync(self, href, token):
        ch = sorted(self.changed[href])
        self.changed[href] = set()
        return "tok", ch, []

    def get(self, href):
        for lst_ in self.lists_.values():
            if href in lst_["objs"]:
                return lst_["objs"][href], self.tags.get(href, "e0")
        return None

    def put(self, href, ics, etag):
        if self.conflict_next:
            self.conflict_next = False
            return False
        for h, lst_ in self.lists_.items():
            if href in lst_["objs"]:
                lst_["objs"][href] = ics
                self.etag += 1
                self.tags[href] = f"e{self.etag}"
                self.changed[h].add(href)
        return True

    # Talk (same account)
    def talk_post(self, room, message, *, thread_id="", title=""):
        self.mid = getattr(self, "mid", 1000) + 1
        if title:
            self.threads[str(self.mid)] = {"title": title, "level": 0, "room": room}
            thread_id = str(self.mid)
        self.msgs.append({"id": self.mid, "actorId": "lumina", "message": message,
                          "threadId": int(thread_id), "room": room})
        return str(self.mid)

    def thread_info(self, room, tid):
        t = self.threads.get(str(tid))
        return {"thread": {"title": t["title"]}, "attendee": {"notificationLevel": t["level"]}} if t else {}

    def thread_set(self, room, tid, *, title=None, level=None):
        t = self.threads[str(tid)]
        if title is not None:
            t["title"] = title
        if level is not None:
            t["level"] = level

    def thread_messages(self, room, tid, after):
        return [m for m in self.msgs if str(m["threadId"]) == str(tid) and m["id"] > after]

    def chef_says(self, tid, text):
        self.mid = max([getattr(self, "mid", 1000)] + [m["id"] for m in self.msgs]) + 1
        self.msgs.append({"id": self.mid, "actorId": "dknestri", "message": text, "threadId": int(tid)})

    def edit(self, href, fn):
        for h, lst_ in self.lists_.items():
            if href in lst_["objs"]:
                lst_["objs"][href] = fn(lst_["objs"][href])
                self.changed[h].add(href)


class FakeHermes:
    def __init__(self):
        self.started, self.result = [], None

    def start(self, session_id, text):
        self.started.append((session_id, text))
        return f"run{len(self.started)}"

    def status(self, run_id):
        return self.result or {"status": "running"}


L = "/remote.php/dav/calendars/lumina/gtd-next_shared_by_dknestri/"
S = "/remote.php/dav/calendars/lumina/gtd-someday_shared_by_dknestri/"
T = L + "t1.ics"


def worker(tmp_path, objs=None, someday=None, proposer=None):
    calls = []

    def prop(summary, notes, name, due):
        calls.append(summary)
        return ["Lumina: look up the renewal fee", "Call the DMV", "Ask Casey to drop it off"]

    dav = FakeDav({L: {"name": "GTD Next (chef)", "objs": {T: mk()} if objs is None else objs},
                   S: {"name": "GTD Someday (chef)", "objs": someday or {}}})
    h = FakeHermes()
    dav.base = "https://hub"
    w = tt.Worker(dav, h, tmp_path / "s.json", rooms={"dknestri": "gaquurgh"},
                  proposer=proposer or prop, planner=lambda s, n, o: "1. look it up\n2. report")
    return w, dav, h, calls


def desc_of(dav, href=T):
    return tt.Todo(dav.get(href)[0]).text("DESCRIPTION")


def test_fold_escape_roundtrip():
    long = "x" * 200 + ", semi; back\\slash\nline two é" * 3
    t = tt.Todo(mk(desc=long))
    assert t.text("DESCRIPTION") == long
    out = t.dump()
    assert all(len(ln.encode()) <= 75 for ln in out.split("\r\n"))
    assert tt.Todo(out).text("DESCRIPTION") == long


def test_parse_command_variants():
    p = tt.parse_command
    assert p("go 2 Buy milk", "", [])[:2] == ({"kind": "go", "choice": "2", "extra": ""}, "Buy milk")
    assert p("Go to the dentist", "", [])[0] is None  # a real title, not a command
    cmd, _, chef, _ = p("X", "go 3\nmy notes", [])
    assert cmd["choice"] == "3" and chef == "my notes"
    assert p("X", "Go", [])[0]["choice"] == ""
    assert p("X", "options?", [])[0]["kind"] == "options"
    cmd, _, chef, _ = p("X", "notes\n@lumina book it for friday", [])
    assert cmd == {"kind": "go", "choice": "", "extra": "book it for friday"} and chef == "notes"
    cmd, _, _, cats = p("X", "", ["home", "GO-1"])
    assert cmd["choice"] == "1" and cats == ["home"]
    assert p("X", "we should go to the store", [])[0] is None


def test_options_written_once_then_loop_guard(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    st = w.cycle()
    assert st["options"] == 1 and st["writes"] == 1
    d = desc_of(dav)
    assert tt.BLOCK_START in d and "2) Call the DMV" in d and tt.PICK_HINT in d
    st = w.cycle()  # sees its own write
    assert calls == ["Renew car registration"] and st["writes"] == 0


def test_chef_edit_regenerates_and_keeps_his_text(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    w.cycle()
    dav.edit(T, lambda ics: ics.replace("SUMMARY:Renew car registration", "SUMMARY:Renew car reg online"))
    w.cycle()
    assert calls[-1] == "Renew car reg online"
    dav.edit(T, lambda ics: tt.Todo(ics).dump())  # no-op rewrite by another client
    w.cycle()
    assert len(calls) == 2


def test_someday_needs_a_command(tmp_path):
    sh = S + "s1.ics"
    w, dav, h, calls = worker(tmp_path, objs={}, someday={sh: mk("s1", "Learn piano")})
    w.cycle()
    assert calls == []
    dav.edit(sh, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:options\\n"))
    w.cycle()
    assert calls == ["Learn piano"]
    assert "options" not in tt.split_notes(desc_of(dav, sh))[0]


def test_go_starts_run_consumes_command_and_writes_back(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    w.cycle()
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go 2\\n"))
    st = w.cycle()
    assert st["runs"] == 1
    sid, prompt = h.started[0]
    assert sid == "task-t1" and "Chosen next step: Call the DMV" in prompt and 'room="gaquurgh"' in prompt
    t = tt.Todo(dav.get(T)[0])
    chef, block = tt.split_notes(t.text("DESCRIPTION"))
    assert "go 2" not in chef and "started option 2" in block
    assert t.get("STATUS") == "IN-PROCESS"
    w.cycle()  # still running, own write seen: nothing new
    assert len(h.started) == 1
    h.result = {"status": "completed", "output": 'Done.\nTASK_STATUS {"state": "done", "percent": 100, "note": "fee paid online"}'}
    st = w.cycle()
    assert st["finished"] == 1
    t = tt.Todo(dav.get(T)[0])
    assert t.get("STATUS") == "COMPLETED" and t.get("PERCENT-COMPLETE") == "100"
    assert "done: fee paid online" in t.text("DESCRIPTION")


def test_title_go_and_failed_run(tmp_path):
    w, dav, h, calls = worker(tmp_path, objs={T: mk(summary="go 1 Renew car registration")})
    w.cycle()
    t = tt.Todo(dav.get(T)[0])
    assert t.text("SUMMARY") == "Renew car registration" and len(h.started) == 1
    h.result = {"status": "failed", "error": "provider down"}
    w.cycle()
    assert "blocked: run failed: provider down" in desc_of(dav)


def test_block_stripped_by_other_writer_is_restored_without_model(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    w.cycle()
    dav.edit(T, lambda ics: tt.Todo(ics).dump().replace(
        tt._escape(desc_of(dav)), ""))
    w.cycle()
    assert len(calls) == 1 and tt.BLOCK_START in desc_of(dav)


def test_conflict_is_retried(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    dav.conflict_next = True
    st = w.cycle()
    assert st["conflicts"] == 1 and tt.BLOCK_START not in desc_of(dav)
    dav.edit(T, lambda ics: ics)  # next change notice
    w.cycle()
    assert tt.BLOCK_START in desc_of(dav) and len(calls) == 1  # options reused, not regenerated


def test_parse_status_fallback():
    assert tt.parse_status("no line here")["state"] == "working"
    assert tt.parse_status('x TASK_STATUS {"state": "needs_chef", "percent": 40, "note": "need ok"}') == {
        "state": "needs_chef", "percent": 40, "note": "need ok"}


def test_go_n_without_options_generates_first(tmp_path):
    w, dav, h, calls = worker(tmp_path, objs={T: mk(summary="go 3 Renew car registration")})
    w.cycle()
    assert calls == ["Renew car registration"]
    assert "Chosen next step: Ask Casey to drop it off" in h.started[0][1]


def test_step_done_but_task_not_refreshes_options_with_progress(tmp_path):
    seen = []

    def prop(summary, notes, name, due):
        seen.append(notes)
        return ["a", "b", "c"]

    w, dav, h, calls = worker(tmp_path, proposer=prop)
    w.cycle()
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go 1\\n"))
    w.cycle()
    h.result = {"status": "completed", "output": 'TASK_STATUS {"state": "working", "percent": 40, "note": "fee is $85"}'}
    w.cycle()
    w.cycle()
    t = tt.Todo(dav.get(T)[0])
    assert t.get("STATUS") == "IN-PROCESS" and t.get("PERCENT-COMPLETE") == "40"
    assert len(seen) == 2 and "fee is $85" in seen[1]


def test_every_go_opens_or_reuses_the_task_thread(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    w.cycle()
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go 2\\n"))
    w.cycle()
    (tid, th), = dav.threads.items()
    assert th["title"] == "Task: Renew car registration"
    first = dav.msgs[0]["message"]
    assert "You picked option 2: Call the DMV" in first and "Plan:\n1. look it up" in first
    assert "Task: https://hub/apps/tasks/calendars/gtd-next/tasks/t1.ics" in first
    assert f'thread_id=\\"{tid}\\"'.replace("\\\\", "\\") in h.started[0][1] or f'thread_id="{tid}"' in h.started[0][1]
    assert f"Talk thread: https://hub/call/gaquurgh#message_{tid}" in desc_of(dav)
    # the work finishes: task completed, thread closed with a check mark
    h.result = {"status": "completed", "output": 'TASK_STATUS {"state": "done", "percent": 100, "note": "booked for Tue"}'}
    w.cycle()
    assert th["title"] == "\u2705 Task: Renew car registration" and th["level"] == 3
    assert "Done: booked for Tue" in dav.msgs[-1]["message"]
    assert tt.Todo(dav.get(T)[0]).get("STATUS") == "COMPLETED"
    # Chef writes in the closed thread: nctalk reopens it (title), the task reopens
    th["title"] = "Task: Renew car registration"
    dav.chef_says(tid, "actually make it Wednesday")
    dav.msgs.append({"id": 9000, "actorId": "lumina", "message": "Moved to Wednesday.", "threadId": int(tid)})
    w.cycle()
    t = tt.Todo(dav.get(T)[0])
    assert t.get("STATUS") == "IN-PROCESS" and t.get("COMPLETED") is None
    assert "reopened in Talk. Lumina: Moved to Wednesday." in t.text("DESCRIPTION")
    # a later pick reuses the same thread (no second thread)
    h.result = None
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go 1\\n"))
    w.cycle()
    assert len(dav.threads) == 1 and len(h.started) == 2
    assert "You picked option 1" in dav.msgs[-1]["message"] and str(dav.msgs[-1]["threadId"]) == tid


def test_chef_steers_in_thread_and_lumina_closes_it(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    w.cycle()
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go\\n"))
    w.cycle()
    (tid, th), = dav.threads.items()
    h.result = {"status": "completed", "output": 'TASK_STATUS {"state": "needs_chef", "percent": 30, "note": "poll posted"}'}
    w.cycle()
    assert th["level"] == 0  # still open
    dav.chef_says(tid, "2")
    dav.msgs.append({"id": 9001, "actorId": "lumina", "message": "**Done**, you picked YMCA.", "threadId": int(tid)})
    w.cycle()
    assert "you answered in Talk. Lumina: Done, you picked YMCA." in desc_of(dav)
    w.cycle()
    assert desc_of(dav).count("you answered in Talk") == 1
    # Lumina closes the thread from the thread session after more steering
    dav.chef_says(tid, "great, wrap it up")
    th["title"] = "\u2705 " + th["title"]
    dav.msgs.append({"id": dav.mid + 1, "actorId": "lumina", "message": "Signed up, all done.", "threadId": int(tid)})
    w.cycle()
    t = tt.Todo(dav.get(T)[0])
    assert t.get("STATUS") == "COMPLETED" and "done in Talk: Signed up, all done." in t.text("DESCRIPTION")


def test_thread_recovered_from_block_when_state_is_lost(tmp_path):
    w, dav, h, calls = worker(tmp_path)
    w.cycle()
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go\\n"))
    w.cycle()
    tid = next(iter(dav.threads))
    h.result = {"status": "completed", "output": 'TASK_STATUS {"state": "working", "percent": 20, "note": "x"}'}
    w.cycle()
    w2 = tt.Worker(dav, h, tmp_path / "fresh.json", rooms={"dknestri": "gaquurgh"},
                   proposer=lambda *a: ["a", "b", "c"], planner=lambda *a: "p")
    h.result = None
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go\\n"))
    w2.cycle()
    assert len(dav.threads) == 1 and str(dav.msgs[-1]["threadId"]) == tid


def test_pick_deferred_by_run_limit_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "RUNS_PER_HOUR", 0)
    w, dav, h, calls = worker(tmp_path)
    w.cycle()
    dav.edit(T, lambda ics: ics.replace("DESCRIPTION:", "DESCRIPTION:go\\n"))
    w.cycle()
    assert h.started == [] and "go" in tt.split_notes(desc_of(dav))[0]
    monkeypatch.setattr(tt, "RUNS_PER_HOUR", 6)
    w.cycle()  # no new change notice, still picked up
    assert len(h.started) == 1


def test_backlog_beyond_the_cycle_limit_gets_options_later(tmp_path, monkeypatch):
    monkeypatch.setattr(tt, "OPTIONS_PER_CYCLE", 2)
    objs = {f"{L}t{i}.ics": mk(f"t{i}", f"Task {i}") for i in range(5)}
    w, dav, h, calls = worker(tmp_path, objs=objs)
    w.cycle()
    assert len(calls) == 2
    w.cycle()
    w.cycle()
    assert sorted(calls) == [f"Task {i}" for i in range(5)]
    w.cycle()
    assert len(calls) == 5 and not any(r.get("pending") for r in w.state["tasks"].values())
