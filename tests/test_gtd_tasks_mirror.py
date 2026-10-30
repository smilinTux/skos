"""Tests for the one-way GTD -> Nextcloud Tasks mirror (pilot)."""

from skos import gtd_tasks_mirror as m


class FakeClient:
    """In-memory CalDAV with ETags and If-Match / If-None-Match semantics."""

    def __init__(self):
        self.store = {}  # url -> (ics, etag)
        self.n = 0
        self.user = self.owner = "chef"

    def cal_url(self, cal):
        return f"https://x/{cal}/"

    def _etag(self):
        self.n += 1
        return f'"e{self.n}"'

    def list_todos(self, cal):
        out = {}
        for url, (ics, etag) in self.store.items():
            if url.startswith(self.cal_url(cal)):
                p = m.parse_props(ics)
                out[p["UID"]] = (url, etag, p)
        return out

    def put(self, url, ics, etag):
        cur = self.store.get(url)
        if etag is None and cur is not None:
            return 412
        if etag is not None and (cur is None or cur[1] != etag):
            return 412
        self.store[url] = (ics, self._etag())
        return 201 if cur is None else 204

    def get(self, url):
        return self.store[url]

    def delete(self, url, etag):
        if self.store.get(url, (None, None))[1] != etag:
            return 412
        del self.store[url]
        return 204


def item(iid, text="do it", **kw):
    return {"id": iid, "text": text, "source": "manual", "context": "@home",
            "priority": "high", "created_at": "2026-10-30T10:00:00+00:00", **kw}


def loader(lists):
    return lambda f: lists.get(f, [])


def test_vtodo_roundtrip_and_categories():
    ics = m.to_vtodo(item("a1", delegate_to="bob", text="x, y; z" * 20), "waiting-for.json")
    p = m.parse_props(ics)
    assert p["UID"] == "skgtd-a1"
    assert p["X-SKGTD-ID"] == "a1"
    assert p["STATUS"] == "NEEDS-ACTION"
    assert "CATEGORIES:home,waiting-for,waiting:bob,src:manual" in ics
    assert "PRIORITY:1" in ics
    assert all(len(l.encode()) <= 75 for l in ics.split("\r\n"))


def test_create_then_unchanged_then_update():
    c = FakeClient()
    lists = {"next-actions.json": [item("a1")]}
    r = m.mirror(loader(lists), c, set())
    assert r.created == 1
    r = m.mirror(loader(lists), c, set())
    assert r.unchanged == 1 and r.created == 0
    lists["next-actions.json"] = [item("a1", text="changed")]
    r = m.mirror(loader(lists), c, set())
    assert r.updated == 1


def test_completed_in_tasks_is_left_alone():
    c = FakeClient()
    lists = {"next-actions.json": [item("a1")]}
    m.mirror(loader(lists), c, set())
    url = next(iter(c.store))
    ics, etag = c.store[url]
    c.store[url] = (m._mark_completed(ics), etag)
    lists["next-actions.json"] = [item("a1", text="source changed")]
    r = m.mirror(loader(lists), c, set())
    assert r.pending_writeback == ["a1"] and r.updated == 0
    assert "STATUS:COMPLETED" in c.store[url][0]


def test_archived_item_gets_completed_moved_item_removed():
    c = FakeClient()
    lists = {"next-actions.json": [item("a1"), item("a2")]}
    m.mirror(loader(lists), c, set())
    lists = {"next-actions.json": [], "waiting-for.json": [item("a2")]}
    r = m.mirror(loader(lists), c, {"a1"})
    assert r.completed == 1 and r.removed == 1 and r.created == 1
    done = [ics for url, (ics, _) in c.store.items() if "gtd-next" in url]
    assert len(done) == 1 and "STATUS:COMPLETED" in done[0]


def test_foreign_tasks_untouched():
    c = FakeClient()
    c.store["https://x/gtd-next/mine.ics"] = (
        "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:mine\r\nSUMMARY:mine\r\nEND:VTODO\r\nEND:VCALENDAR\r\n", '"z"')
    r = m.mirror(loader({}), c, set())
    assert "https://x/gtd-next/mine.ics" in c.store
    assert r.removed == 0


def test_concurrent_edit_is_a_conflict_not_a_lost_write():
    c = FakeClient()
    lists = {"next-actions.json": [item("a1")]}
    m.mirror(loader(lists), c, set())
    url = next(iter(c.store))
    real_list = c.list_todos

    def stale_list(cal):
        out = real_list(cal)
        # someone edits the task after we listed it
        ics, _ = c.store[url]
        c.store[url] = (ics.replace("SUMMARY:do it", "SUMMARY:edited by chef"), c._etag())
        return out

    c.list_todos = stale_list
    lists["next-actions.json"] = [item("a1", text="new source text")]
    r = m.mirror(loader(lists), c, set())
    assert r.conflicts == ["skgtd-a1"]
    assert "edited by chef" in c.store[url][0]
