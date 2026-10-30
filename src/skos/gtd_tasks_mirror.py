"""One-way mirror of the unified GTD into Nextcloud Tasks (CalDAV), pilot.

The skos GTD store stays the source of truth. This copies the open lists a
person acts on (next actions, waiting-fors, projects) into VTODO task lists on
the estate's Nextcloud, so they show up in the Tasks app and on phones.

Rules:
- Each GTD item becomes one VTODO with UID ``skgtd-<item id>``, so reruns upsert.
- A task is rewritten only when its GTD source changed (``X-SKGTD-HASH``), so an
  edit made in Tasks survives until the GTD item itself changes.
- A task the person completed in Tasks while the GTD item is still open is left
  alone and reported as ``pending_writeback`` (the pilot is one-way).
- A mirrored task whose item left the list is completed (item archived) or
  removed (item moved to another list, where it is re-created).
- Tasks without ``X-SKGTD-ID`` (made by a person or an agent) are never touched.
- Writes use If-Match / If-None-Match, so a concurrent edit gets a 412 and is
  skipped this run instead of being overwritten.

Config (env, e.g. ~/.config/skos/nextcloud-gtd.env):
  NEXTCLOUD_HOST, NEXTCLOUD_USERNAME, NEXTCLOUD_PASSWORD (app password),
  SKGTD_TASKS_OWNER (calendar owner, the person whose GTD this is).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

# GTD list file -> calendar URI (the calendar lives in the owner's account and
# is shared read-write to the agents group, so agents see "<uri>_shared_by_<owner>").
LISTS = {
    "next-actions.json": "gtd-next",
    "waiting-for.json": "gtd-waiting",
    "projects.json": "gtd-projects",
}
PRIORITY = {"high": 1, "medium": 5, "low": 9}
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
REPORT_BODY = (
    '<?xml version="1.0"?>'
    '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
    '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO"/>'
    "</c:comp-filter></c:filter></c:calendar-query>"
)


@dataclass
class MirrorReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    completed: int = 0
    removed: int = 0
    conflicts: list[str] = field(default_factory=list)
    pending_writeback: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 line folding at 75 octets."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _stamp(iso: str | None) -> str:
    if not iso:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def item_hash(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()[:16]


def categories(item: dict, list_file: str) -> list[str]:
    cats = []
    ctx = (item.get("context") or "").strip()
    if ctx:
        cats.append(ctx.lstrip("@"))
    if list_file == "waiting-for.json":
        cats.append("waiting-for")
        if item.get("delegate_to"):
            cats.append(f"waiting:{item['delegate_to']}")
    if item.get("source"):
        cats.append(f"src:{item['source']}")
    return cats


def to_vtodo(item: dict, list_file: str) -> str:
    """Render one GTD item as a VCALENDAR with a single VTODO."""
    desc = [f"GTD id: {item['id']}", f"Source: {item.get('source')}"]
    if item.get("source_ref"):
        desc.append(f"Ref: {item['source_ref']}")
    for k in ("email_from", "email_subject", "email_account"):
        if item.get(k):
            desc.append(f"{k.split('_', 1)[1].capitalize()}: {item[k]}")
    if item.get("energy"):
        desc.append(f"Energy: {item['energy']}")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//smilinTux//skos gtd mirror//EN",
        "BEGIN:VTODO",
        f"UID:skgtd-{item['id']}",
        f"DTSTAMP:{_stamp(None)}",
        f"CREATED:{_stamp(item.get('created_at'))}",
        f"SUMMARY:{_esc(item.get('text') or '(untitled)')}",
        f"DESCRIPTION:{_esc(chr(10).join(desc))}",
        "STATUS:NEEDS-ACTION",
        f"X-SKGTD-ID:{item['id']}",
        f"X-SKGTD-LIST:{list_file.removesuffix('.json')}",
        f"X-SKGTD-HASH:{item_hash(item)}",
    ]
    cats = categories(item, list_file)
    if cats:
        lines.append("CATEGORIES:" + ",".join(_esc(c) for c in cats))
    if item.get("priority") in PRIORITY:
        lines.append(f"PRIORITY:{PRIORITY[item['priority']]}")
    if item.get("due"):
        lines.append(f"DUE:{_stamp(item['due'])}")
    if item.get("start") or item.get("tickler"):
        lines.append(f"DTSTART:{_stamp(item.get('start') or item.get('tickler'))}")
    lines += ["END:VTODO", "END:VCALENDAR"]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def parse_props(ics: str) -> dict[str, str]:
    """Unfold and return the VTODO's top-level properties we care about."""
    text = re.sub(r"\r?\n[ \t]", "", ics)
    props: dict[str, str] = {}
    in_todo = False
    for line in text.splitlines():
        if line == "BEGIN:VTODO":
            in_todo = True
        elif line == "END:VTODO":
            break
        elif in_todo and ":" in line:
            name, value = line.split(":", 1)
            name = name.split(";", 1)[0].upper()
            if name in ("UID", "STATUS", "X-SKGTD-ID", "X-SKGTD-HASH", "X-SKGTD-LIST"):
                props.setdefault(name, value)
    return props


class TasksClient:
    def __init__(self, host: str, user: str, password: str, owner: str,
                 session: requests.Session | None = None):
        self.base = host.rstrip("/") + "/remote.php/dav/calendars/"
        self.user = user
        self.owner = owner
        self.s = session or requests.Session()
        self.s.auth = (user, password)

    def cal_url(self, uri: str) -> str:
        name = uri if self.user == self.owner else f"{uri}_shared_by_{self.owner}"
        return f"{self.base}{self.user}/{name}/"

    def list_todos(self, cal: str) -> dict[str, tuple[str, str, dict]]:
        """uid -> (href, etag, props) for every VTODO in the calendar."""
        r = self.s.request("REPORT", self.cal_url(cal), data=REPORT_BODY,
                           headers={"Depth": "1", "Content-Type": "application/xml"})
        r.raise_for_status()
        out = {}
        for resp in ET.fromstring(r.content).findall("d:response", NS):
            href = resp.findtext("d:href", namespaces=NS)
            etag = resp.findtext(".//d:getetag", namespaces=NS)
            data = resp.findtext(".//c:calendar-data", namespaces=NS) or ""
            props = parse_props(data)
            if props.get("UID"):
                out[props["UID"]] = (urljoin(self.base, href), etag, props)
        return out

    def put(self, url: str, ics: str, etag: str | None) -> int:
        hdr = {"Content-Type": "text/calendar; charset=utf-8"}
        hdr["If-Match" if etag else "If-None-Match"] = etag or "*"
        return self.s.put(url, data=ics.encode("utf-8"), headers=hdr).status_code

    def get(self, url: str) -> tuple[str, str | None]:
        r = self.s.get(url)
        r.raise_for_status()
        return r.text, r.headers.get("ETag")

    def delete(self, url: str, etag: str | None) -> int:
        return self.s.delete(url, headers={"If-Match": etag} if etag else {}).status_code


def _mark_completed(ics: str) -> str:
    text = re.sub(r"\r?\n[ \t]", "", ics)
    text = re.sub(r"(?m)^STATUS:.*\r?$", "STATUS:COMPLETED\r", text)
    if "STATUS:COMPLETED" not in text:
        text = text.replace("END:VTODO", "STATUS:COMPLETED\r\nEND:VTODO", 1)
    text = text.replace("END:VTODO", f"COMPLETED:{_stamp(None)}\r\nPERCENT-COMPLETE:100\r\nEND:VTODO", 1)
    return text


def mirror(load, client: TasksClient, archived_ids: set[str]) -> MirrorReport:
    """Mirror each list. ``load(list_file)`` returns that list's items."""
    rep = MirrorReport()
    for list_file, cal in LISTS.items():
        items = {i["id"]: i for i in load(list_file) if i.get("id")}
        try:
            existing = client.list_todos(cal)
        except requests.RequestException as e:
            rep.errors.append(f"{cal}: list failed: {e}")
            continue
        for iid, item in items.items():
            uid = f"skgtd-{iid}"
            ics = to_vtodo(item, list_file)
            if uid not in existing:
                code = client.put(f"{client.cal_url(cal)}{uid}.ics", ics, None)
                if code in (201, 204):
                    rep.created += 1
                elif code == 412:
                    rep.conflicts.append(uid)
                else:
                    rep.errors.append(f"{uid}: create {code}")
                continue
            href, etag, props = existing[uid]
            if props.get("STATUS") == "COMPLETED":
                rep.pending_writeback.append(iid)
                continue
            if props.get("X-SKGTD-HASH") == item_hash(item):
                rep.unchanged += 1
                continue
            code = client.put(href, ics, etag)
            if code in (201, 204):
                rep.updated += 1
            elif code == 412:
                rep.conflicts.append(uid)
            else:
                rep.errors.append(f"{uid}: update {code}")
        for uid, (href, etag, props) in existing.items():
            iid = props.get("X-SKGTD-ID")
            if not iid or iid in items:
                continue
            if iid in archived_ids:
                if props.get("STATUS") == "COMPLETED":
                    continue
                body, cur_etag = client.get(href)
                code = client.put(href, _mark_completed(body), cur_etag or etag)
                if code in (201, 204):
                    rep.completed += 1
                elif code == 412:
                    rep.conflicts.append(uid)
                else:
                    rep.errors.append(f"{uid}: complete {code}")
            else:
                code = client.delete(href, etag)
                if code in (200, 204):
                    rep.removed += 1
                elif code == 412:
                    rep.conflicts.append(uid)
                else:
                    rep.errors.append(f"{uid}: delete {code}")
    return rep


def run() -> MirrorReport:
    from skos.gtd_ingest import _load

    host = os.environ["NEXTCLOUD_HOST"]
    client = TasksClient(host, os.environ["NEXTCLOUD_USERNAME"], os.environ["NEXTCLOUD_PASSWORD"],
                         os.environ.get("SKGTD_TASKS_OWNER") or os.environ["NEXTCLOUD_USERNAME"])
    archived = {i.get("id") for i in _load("archive.json")}
    return mirror(_load, client, archived)
