"""SK Ops: feed fleet, job and token-spend data into Nextcloud Tables.

Part of moving the SK dashboard into Nextcloud (strangler pattern: skdashboard
keeps running, frozen, while each panel gets a Nextcloud home). Nextcloud
Tables holds the data, the Analytics app draws the charts, and a Tables
Application "SK Ops" puts it all in one menu entry.

Three tables, each upserted by a stable key so reruns change only what moved:

- "SK Token Spend": one row per (date, node, client, model) from the SKCounter
  collector's accepted observations. Newest observation wins for a bucket.
  The parse is incremental: a local cache remembers every bucket, so a run only
  reads observation files newer than the last one it saw.
- "SK Fleet Hosts": one row per fleet node from the files-as-API fleet tree
  (status/<node>/node.json + heartbeat.json). Rows for nodes gone from the
  tree are removed.
- "SK Jobs": one row per (host, job) from the sk-cron-run ledger tail, local
  and on the hosts named in SKOPS_JOB_HOSTS (read over ssh). Jobs not seen for
  7 days are removed.

- "SK Work Flow": counts for the work dashboards, one row per (area, group,
  stage): coord cards by kind and column, the SK Work Deck board by stack, GTD
  lists by age bucket plus weekly added/done, ITIL tickets by type and status,
  and memory files per agent and tier. The coord count folds ~1500 cards from
  disk (about a minute), so it is cached for an hour.
- "SK Open Tickets": the open ITIL incidents and problems, and the changes
  waiting for approval, one row each, with their age.

The SK Alerts rows are written by the alert path itself. This module only adds
two derived columns to them ("Opened day", "Hours open") so Analytics can chart
alerts per day and time to resolve; it never changes the other columns.

Config (env, e.g. ~/.config/skos/skops.env, mode 600, never in git):
  NEXTCLOUD_HOST, NEXTCLOUD_USERNAME, NEXTCLOUD_PASSWORD (app password)
  SKOPS_ESTATE        estate label for rows (default "nor")
  SKOPS_JOB_HOSTS     space separated ssh hosts whose cron ledger to read
  SKOPS_SPEND_DAYS    how far back token spend goes (default 90)
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

HOME = Path.home()
COLLECTOR_ROOT = HOME / ".local/state/skcounter-collector/observations"
FLEET_ROOT = HOME / ".skcapstone/fleet"
LEDGER = HOME / ".skcapstone/logs/cron-ledger.jsonl"
INCIDENT_DIR = HOME / ".local/state/sk-cron-run"
STATE_DIR = HOME / ".local/state/skos"
LEDGER_TAIL_BYTES = 12_000_000  # about 10 days on nor, so weekly jobs show
STALE_AFTER = timedelta(minutes=30)
LOW_DISK_GB = 10.0

# Column spec: (title, kind). kind is "line", "number", "date" or "datetime".
SPEND_TABLE = ("SK Token Spend", "💸", [
    ("Date", "date"), ("Estate", "line"), ("Node", "line"), ("Client", "line"),
    ("Provider", "line"), ("Model", "line"), ("Messages", "number"),
    ("Input tokens", "number"), ("Output tokens", "number"),
    ("Cache read tokens", "number"), ("Cache write tokens", "number"),
    ("Cost USD", "number"), ("Key", "line"), ("Month", "line"),
])
FLEET_TABLE = ("SK Fleet Hosts", "🖥️", [
    ("Host", "line"), ("Estate", "line"), ("Role", "line"), ("Status", "line"),
    ("Last seen", "datetime"), ("skcapstone", "line"), ("skmemory", "line"),
    ("Cores", "number"), ("RAM GB", "number"), ("Disk free GB", "number"),
    ("Key", "line"),
])
JOBS_TABLE = ("SK Jobs", "⏱️", [
    ("Host", "line"), ("Job", "line"), ("Last result", "line"),
    ("Last run", "datetime"), ("Last exit", "number"), ("Duration s", "number"),
    ("Consecutive failures", "number"), ("Runs 24h", "number"),
    ("Failures 24h", "number"), ("Alerting", "line"), ("Last output", "line"),
    ("Key", "line"),
])
WORK_TABLE = ("SK Work Flow", "🧭", [
    ("Area", "line"), ("Group", "line"), ("Stage", "line"), ("Items", "number"),
    ("Oldest days", "number"), ("Key", "line"),
])
TICKETS_TABLE = ("SK Open Tickets", "🎫", [
    ("Type", "line"), ("Ticket", "line"), ("Title", "line"), ("Status", "line"),
    ("Level", "line"), ("Opened", "datetime"), ("Age days", "number"), ("Key", "line"),
])
ALERTS_TITLE = "SK Alerts"  # fed elsewhere; only the two derived columns below are ours
ALERTS_DERIVED = [("Opened day", "line"), ("Hours open", "number")]
COORD_CACHE_S = 3600


# ---------------------------------------------------------------- Tables API

class TablesClient:
    """Thin client over the Nextcloud Tables API (v1 rows/columns, v2 OCS)."""

    def __init__(self, host: str, user: str, password: str, session=None):
        self.base = host.rstrip("/")
        self.s = session or requests.Session()
        self.s.auth = (user, password)
        self.s.headers.update({"OCS-APIRequest": "true", "Accept": "application/json"})

    def _v1(self, method: str, path: str, **kw) -> Any:
        r = self.s.request(method, f"{self.base}/index.php/apps/tables/api/1{path}", timeout=60, **kw)
        r.raise_for_status()
        return r.json() if r.content else None

    def _ocs(self, method: str, path: str, **kw) -> Any:
        r = self.s.request(method, f"{self.base}/ocs/v2.php/apps/tables/api/2{path}",
                           params={"format": "json"}, timeout=60, **kw)
        r.raise_for_status()
        return r.json()["ocs"]["data"]

    def tables(self) -> list[dict]:
        return self._ocs("GET", "/tables")

    def create_table(self, title: str, emoji: str) -> dict:
        return self._ocs("POST", "/tables", json={"title": title, "emoji": emoji, "template": "custom"})

    def columns(self, table_id: int) -> list[dict]:
        return self._v1("GET", f"/tables/{table_id}/columns")

    def create_column(self, table_id: int, title: str, kind: str) -> dict:
        body: dict[str, Any] = {"title": title, "mandatory": False}
        if kind == "number":
            body.update(type="number", numberDecimals=2 if "USD" in title or "GB" in title else 0)
        elif kind == "date":
            body.update(type="datetime", subtype="date")
        elif kind == "datetime":
            body.update(type="datetime")
        else:
            body.update(type="text", subtype="line")
        return self._v1("POST", f"/tables/{table_id}/columns", json=body)

    def rows(self, table_id: int) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self._v1("GET", f"/tables/{table_id}/rows", params={"limit": 500, "offset": offset})
            out.extend(page)
            if len(page) < 500:
                return out
            offset += 500

    def create_row(self, table_id: int, data: dict[int, Any]) -> dict:
        return self._v1("POST", f"/tables/{table_id}/rows", json={"data": data})

    def update_row(self, row_id: int, data: dict[int, Any]) -> dict:
        return self._v1("PUT", f"/rows/{row_id}", json={"data": data})

    def delete_row(self, row_id: int) -> None:
        self._v1("DELETE", f"/rows/{row_id}")


def ensure_table(client: TablesClient, spec) -> tuple[int, dict[str, int]]:
    """Find or create the table and its columns. Returns (table id, title->column id)."""
    title, emoji, cols = spec
    table = next((t for t in client.tables() if t["title"] == title), None) or client.create_table(title, emoji)
    have = {c["title"]: c["id"] for c in client.columns(table["id"])}
    for col_title, kind in cols:
        if col_title not in have:
            have[col_title] = client.create_column(table["id"], col_title, kind)["id"]
    return table["id"], have


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


@dataclass
class UpsertReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def upsert(client: TablesClient, table_id: int, colmap: dict[str, int], rows: list[dict],
           *, prune: Callable[[dict], bool] | None = None) -> UpsertReport:
    """Make the table match ``rows`` (dicts keyed by column title, with "Key").

    Existing rows are matched on the Key column. A row is written only when a
    value changed. Existing rows whose key is not in ``rows`` are deleted when
    ``prune(existing_values)`` returns True.
    """
    rep = UpsertReport()
    key_col = colmap["Key"]
    by_id = {v: k for k, v in colmap.items()}
    existing: dict[str, tuple[int, dict[str, str]]] = {}
    for r in client.rows(table_id):
        vals = {by_id.get(c["columnId"], ""): _norm(c["value"]) for c in r.get("data", [])}
        key = vals.get("Key", "")
        if key in existing:  # duplicate from an interrupted run: keep the first
            client.delete_row(r["id"])
            rep.deleted += 1
            continue
        existing[key] = (r["id"], vals)
    wanted = set()
    for row in rows:
        key = row["Key"]
        wanted.add(key)
        data = {colmap[k]: v for k, v in row.items() if k in colmap and v is not None}
        if key not in existing:
            client.create_row(table_id, data)
            rep.created += 1
            continue
        row_id, vals = existing[key]
        changed = {colmap[k]: v for k, v in row.items()
                   if k in colmap and v is not None and _norm(v) != vals.get(k, "")}
        if changed:
            client.update_row(row_id, changed)
            rep.updated += 1
        else:
            rep.unchanged += 1
    if prune:
        for key, (row_id, vals) in existing.items():
            if key not in wanted and key_col and prune(vals):
                client.delete_row(row_id)
                rep.deleted += 1
    return rep


# ---------------------------------------------------------------- collectors

def _local(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt_dt(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def spend_rows(estate: str, *, root: Path = COLLECTOR_ROOT, cache_path: Path | None = None,
               days: int = 90, now: datetime | None = None) -> list[dict]:
    """Daily token spend per (node, client, model) from SKCounter observations.

    Uses the "models" view (one aggregate per day, client, provider, model) of
    the harness_reported lane. For each bucket the newest observation wins.
    """
    now = now or datetime.now(timezone.utc)
    cache_path = cache_path or STATE_DIR / "skops-spend-cache.json"
    cache = {"seen_mtime": 0.0, "buckets": {}}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
    seen = cache["seen_mtime"]
    newest = seen
    for path in root.glob("*/*/*.json"):
        mtime = path.stat().st_mtime
        if mtime <= seen:
            continue
        newest = max(newest, mtime)
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if doc.get("measurement_lane") != "harness_reported":
            continue
        node, observed = doc.get("node_id", path.parent.parent.name), doc.get("observed_at", "")
        for agg in doc.get("aggregates", []):
            if agg.get("view") != "models":
                continue
            day = agg["bucket_start"][:10]
            key = f"{day}|{node}|{agg.get('client', '')}|{agg.get('model', '')}"
            old = cache["buckets"].get(key)
            if old and old["observed_at"] >= observed:
                continue
            tok, cost = agg.get("tokens") or {}, agg.get("cost") or {}
            cache["buckets"][key] = {
                "observed_at": observed, "date": day, "node": node,
                "client": agg.get("client", ""), "provider": agg.get("provider", ""),
                "model": agg.get("model", ""), "messages": agg.get("message_count", 0),
                "input": tok.get("input", 0), "output": tok.get("output", 0),
                "cache_read": tok.get("cache_read", 0), "cache_write": tok.get("cache_write", 0),
                "cost": round(float(cost.get("amount") or 0), 2)  # Tables stores 2 decimals,
            }
    cutoff = (now - timedelta(days=days)).date().isoformat()
    cache["buckets"] = {k: v for k, v in cache["buckets"].items() if v["date"] >= cutoff}
    cache["seen_mtime"] = newest
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(cache_path)
    return [{
        "Date": b["date"], "Estate": estate, "Node": b["node"], "Client": b["client"],
        "Provider": b["provider"], "Model": b["model"], "Messages": b["messages"],
        "Input tokens": b["input"], "Output tokens": b["output"],
        "Cache read tokens": b["cache_read"], "Cache write tokens": b["cache_write"],
        "Cost USD": b["cost"], "Key": key, "Month": b["date"][:7],
    } for key, b in sorted(cache["buckets"].items())]


def fleet_rows(estate: str, *, root: Path = FLEET_ROOT, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = []
    for node_dir in sorted((root / "status").glob("node-*")):
        try:
            status = json.loads((node_dir / "node.json").read_text()).get("status", {})
        except (OSError, ValueError):
            continue
        beat_ts = None
        try:
            beat_ts = json.loads((node_dir / "heartbeat.json").read_text()).get("ts")
        except (OSError, ValueError):
            pass
        role = ""
        obj = root / "objects" / "node" / f"{node_dir.name}.json"
        if obj.exists():
            try:
                role = json.loads(obj.read_text()).get("spec", {}).get("role", "")
            except ValueError:
                pass
        cap = status.get("capacity", {})
        pkgs = status.get("inventory", {}).get("packages", {}) or {}
        disk = cap.get("disk_gb")
        seen = _local(beat_ts) if beat_ts else None
        if seen is None or now - seen > STALE_AFTER:
            state = "stale"
        elif disk is not None and disk < LOW_DISK_GB:
            state = "low disk"
        else:
            state = "ok"
        host = node_dir.name.removeprefix("node-")
        rows.append({
            "Host": host, "Estate": estate, "Role": role, "Status": state,
            "Last seen": _fmt_dt(seen) if seen else None,
            "skcapstone": (status.get("versions") or {}).get("skcapstone") or pkgs.get("skcapstone", ""),
            "skmemory": pkgs.get("skmemory", ""), "Cores": cap.get("cores"),
            "RAM GB": cap.get("ram_gb"), "Disk free GB": disk, "Key": f"{estate}|{host}",
        })
    return rows


def _ledger_tail(host: str | None) -> tuple[list[dict], set[str]]:
    """Recent ledger records and the job names with an open incident."""
    if host is None:
        with LEDGER.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - LEDGER_TAIL_BYTES))
            text = fh.read().decode("utf-8", "replace")
        incidents = {p.stem for p in INCIDENT_DIR.glob("*.incident")} if INCIDENT_DIR.exists() else set()
    else:
        cmd = (f"tail -c {LEDGER_TAIL_BYTES} ~/.skcapstone/logs/cron-ledger.jsonl; echo '@@INCIDENTS@@'; "
               "ls ${XDG_STATE_HOME:-$HOME/.local/state}/sk-cron-run/ 2>/dev/null")
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                             capture_output=True, text=True, timeout=120, check=True).stdout
        text, _, inc = out.partition("@@INCIDENTS@@")
        incidents = {n.removesuffix(".incident") for n in inc.split() if n.endswith(".incident")}
    records = []
    for line in text.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue  # first line of a tail is usually cut
    return records, incidents


def job_rows(records: Iterable[dict], incidents: set[str], *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    per: dict[tuple[str, str], list[dict]] = {}
    for rec in records:
        if "job" in rec and "host" in rec and "start" in rec:
            per.setdefault((rec["host"], rec["job"]), []).append(rec)
    rows = []
    for (host, job), recs in sorted(per.items()):
        recs.sort(key=lambda r: r["start"])
        last = recs[-1]
        consecutive = 0
        for r in reversed(recs):
            if r.get("ok"):
                break
            consecutive += 1
        day = [r for r in recs if now - _local(r["start"]) <= timedelta(hours=24)]
        safe = job.replace("/", "_")
        rows.append({
            "Host": host, "Job": job, "Last result": "ok" if last.get("ok") else "failed",
            "Last run": _fmt_dt(_local(last["start"])), "Last exit": last.get("exit"),
            "Duration s": last.get("dur_s"), "Consecutive failures": consecutive,
            "Runs 24h": len(day), "Failures 24h": sum(1 for r in day if not r.get("ok")),
            "Alerting": "yes" if (safe in incidents or job in incidents) else "no",
            "Last output": "" if last.get("ok") else (last.get("tail") or "")[-240:],
            "Key": f"{host}|{job}",
        })
    return rows


# ---------------------------------------------------------------- work and tickets

GTD_LISTS = ("inbox", "next-actions", "waiting-for", "projects", "someday-maybe")
GTD_BUCKETS = ((7, "00-07 days"), (30, "08-30 days"), (90, "31-90 days"), (10**9, "91+ days"))
MEMORY_TIERS = ("short-term", "mid-term", "long-term")
CLOSED_INCIDENT = {"resolved", "closed"}
OPEN_CHANGE = {"proposed", "reviewing"}


def _days(iso: str | None, now: datetime) -> int:
    """Whole days since an ISO timestamp (0 when missing or unreadable)."""
    try:
        return max(0, (now - _local(str(iso))).days)
    except (ValueError, TypeError):
        return 0


def _val(x: Any) -> str:
    return str(getattr(x, "value", x) or "")


def _row(area: str, group: str, stage: str, items: int, oldest: int = 0) -> dict:
    return {"Area": area, "Group": group, "Stage": stage, "Items": items,
            "Oldest days": oldest, "Key": f"{area}|{group}|{stage}"}


def coord_rows(now: datetime, *, home: Path | None = None, cache_path: Path | None = None,
               max_age_s: int = COORD_CACHE_S) -> list[dict]:
    """Active coord cards by kind and column. Folding the card store is slow, so cache it."""
    cache_path = cache_path or STATE_DIR / "skops-coord-cache.json"
    try:
        cached = json.loads(cache_path.read_text())
        if now.timestamp() - cached["at"] < max_age_s:
            return cached["rows"]
    except (OSError, ValueError, KeyError):
        pass
    from skcapstone.card_store import CardStore
    counts: dict[tuple[str, str], list] = {}
    for card in CardStore(home or HOME / ".skcapstone").list_cards():
        if getattr(card, "archived", False):
            continue
        slot = counts.setdefault((_val(card.kind), _val(card.status)), [0, 0])
        slot[0] += 1
        slot[1] = max(slot[1], _days(card.created_at, now))
    rows = [_row("coord", k, c, n, old) for (k, c), (n, old) in sorted(counts.items())]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"at": now.timestamp(), "rows": rows}))
    return rows


def deck_rows(deck, board_title: str, now: datetime) -> list[dict]:
    bid = deck.find_board(board_title)
    if bid is None:
        raise LookupError(f"Deck board {board_title!r} not found")
    rows = []
    for stack in deck.stacks(bid):
        cards = stack.get("cards") or []
        oldest = max((max(0, int(now.timestamp() - c.get("createdAt", now.timestamp())) // 86400)
                      for c in cards), default=0)
        rows.append(_row("deck", board_title, stack["title"], len(cards), oldest))
    return rows


def gtd_rows(now: datetime, load: Callable[[str], list[dict]]) -> list[dict]:
    """Open GTD items by list and age bucket, plus items added and done per week."""
    rows = []
    for name in GTD_LISTS:
        buckets: dict[str, list] = {}
        for item in load(f"{name}.json"):
            age = _days(item.get("created_at"), now)
            label = next(lbl for limit, lbl in GTD_BUCKETS if age <= limit)
            slot = buckets.setdefault(label, [0, 0])
            slot[0] += 1
            slot[1] = max(slot[1], age)
        rows += [_row("gtd", name, lbl, n, old) for lbl, (n, old) in sorted(buckets.items())]
    flow: dict[tuple[str, str], int] = {}
    cutoff = now - timedelta(weeks=12)
    everything = [load(f"{n}.json") for n in GTD_LISTS] + [load("archive.json")]
    for kind, field_name, items in (("added", "created_at", [i for lst in everything for i in lst]),
                                    ("done", "completed_at", everything[-1])):
        for item in items:
            try:
                when = _local(item[field_name])
            except (KeyError, ValueError, TypeError):
                continue
            if when >= cutoff:
                monday = (when - timedelta(days=when.weekday())).date().isoformat()
                flow[(kind, monday)] = flow.get((kind, monday), 0) + 1
    rows += [_row("gtd-flow", kind, week, n) for (kind, week), n in sorted(flow.items())]
    return rows


def itil_rows(now: datetime, mgr) -> tuple[list[dict], list[dict]]:
    """(work-flow rows by type and status, open-ticket rows) from an ITILManager."""
    counts: dict[tuple[str, str], list] = {}
    tickets = []
    for kind, items, stamp in (("incident", mgr.list_incidents(), "detected_at"),
                               ("problem", mgr.list_problems(), "created_at"),
                               ("change", mgr.list_changes(), "created_at")):
        for t in items:
            status, opened = _val(t.status), getattr(t, stamp, "") or ""
            age = _days(opened, now)
            slot = counts.setdefault((kind, status), [0, 0])
            slot[0] += 1
            slot[1] = max(slot[1], age)
            is_open = ((kind == "incident" and status not in CLOSED_INCIDENT)
                       or (kind == "problem" and status != "resolved")
                       or (kind == "change" and status in OPEN_CHANGE))
            if is_open:
                level = _val(getattr(t, "severity", None)) if kind == "incident" else \
                    _val(getattr(t, "risk", None))
                tickets.append({
                    "Type": kind, "Ticket": t.id, "Title": (t.title or "")[:200], "Status": status,
                    "Level": level, "Opened": _fmt_dt(_local(opened)) if opened else None,
                    "Age days": age, "Key": t.id})
    return [_row("itil", k, s, n, old) for (k, s), (n, old) in sorted(counts.items())], tickets


def memory_rows(now: datetime, root: Path | None = None) -> list[dict]:
    """Flat memory files per agent and tier (the source of truth for skmemory)."""
    root = root or HOME / ".skcapstone/agents"
    rows = []
    for agent in sorted(p for p in root.glob("*/memory") if p.is_dir()):
        for tier in MEMORY_TIERS:
            n, oldest = 0, now.timestamp()
            try:
                with os.scandir(agent / tier) as it:
                    for e in it:
                        if e.is_file():
                            n += 1
                            oldest = min(oldest, e.stat().st_mtime)
            except OSError:
                continue
            if n:
                rows.append(_row("memory", agent.parent.name, tier, n, int((now.timestamp() - oldest) // 86400)))
    return rows


def alert_derived(rows: list[dict], by_id: dict[int, str], now: datetime | None = None) -> list[dict]:
    """Derived columns for SK Alerts rows (raw Tables rows in, upsert rows out)."""
    now = (now or datetime.now()).replace(tzinfo=None)
    out = []
    for r in rows:
        v = {by_id.get(c["columnId"], ""): _norm(c["value"]) for c in r.get("data", [])}
        if not v.get("Key") or not v.get("First seen"):
            continue
        try:
            first = datetime.strptime(v["First seen"][:16], "%Y-%m-%d %H:%M")
            end = datetime.strptime(v["Resolved at"][:16], "%Y-%m-%d %H:%M") if v.get("Resolved at") else now
        except ValueError:
            continue
        out.append({"Key": v["Key"], "Opened day": first.date().isoformat(),
                    "Hours open": max(0, int((end - first).total_seconds() // 3600))})
    return out


# ---------------------------------------------------------------- run

@dataclass
class OpsReport:
    tables: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"tables": self.tables, "errors": self.errors}


def _older_than(days: int, column: str) -> Callable[[dict], bool]:
    def check(vals: dict) -> bool:
        raw = vals.get(column, "")
        try:
            when = datetime.strptime(raw[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            return True
        return datetime.now() - when > timedelta(days=days)
    return check


def run(only: str | None = None, client: TablesClient | None = None) -> OpsReport:
    rep = OpsReport()
    estate = os.environ.get("SKOPS_ESTATE", "nor")
    client = client or TablesClient(os.environ["NEXTCLOUD_HOST"], os.environ["NEXTCLOUD_USERNAME"],
                                    os.environ["NEXTCLOUD_PASSWORD"])

    def step(name: str, spec, rows_fn, prune=None):
        if only and only != name:
            return
        try:
            table_id, colmap = ensure_table(client, spec)
            rep.tables[spec[0]] = {"id": table_id, **upsert(client, table_id, colmap, rows_fn(), prune=prune).as_dict()}
        except Exception as exc:  # one table failing must not stop the others
            rep.errors.append(f"{name}: {type(exc).__name__}: {exc}")

    step("fleet", FLEET_TABLE, lambda: fleet_rows(estate), prune=lambda vals: True)

    def jobs():
        records, incidents = _ledger_tail(None)
        for host in os.environ.get("SKOPS_JOB_HOSTS", "").split():
            try:
                more, inc = _ledger_tail(host)
                records += more
                incidents |= inc
            except (subprocess.SubprocessError, OSError) as exc:
                rep.errors.append(f"jobs: ledger from {host}: {exc}")
        return job_rows(records, incidents)

    step("jobs", JOBS_TABLE, jobs, prune=_older_than(7, "Last run"))
    days = int(os.environ.get("SKOPS_SPEND_DAYS", "90"))
    step("spend", SPEND_TABLE, lambda: spend_rows(estate, days=days),
         prune=lambda vals: vals.get("Date", "") < (datetime.now() - timedelta(days=days)).date().isoformat())

    now = datetime.now(timezone.utc)
    itil: list = []  # (flow rows, ticket rows), read once for both tables

    def itil_once():
        if not itil:
            from skcoord.itil import ITILManager
            itil.extend(itil_rows(now, ITILManager(HOME / ".skcapstone")))
        return itil

    refreshed: set[str] = set()

    def work():
        from skos.gtd_ingest import _load
        sources = [
            ("coord", {"coord"}, lambda: coord_rows(now)),
            ("deck", {"deck"}, lambda: deck_rows(_deck(), os.environ.get("SKOPS_DECK_BOARD", "SK Work"), now)),
            ("gtd", {"gtd", "gtd-flow"}, lambda: gtd_rows(now, _load)),
            ("itil", {"itil"}, lambda: itil_once()[0]),
            ("memory", {"memory"}, lambda: memory_rows(now)),
        ]
        rows: list[dict] = []
        for name, areas, fn in sources:
            try:
                rows += fn()
                refreshed.update(areas)
            except Exception as exc:  # one source failing keeps its old rows
                rep.errors.append(f"work: {name}: {type(exc).__name__}: {exc}")
        return rows

    def _deck():
        from skos.work_board import Deck
        return Deck(client.base, client.s.auth[0], client.s.auth[1])

    step("work", WORK_TABLE, work, prune=lambda vals: vals.get("Area", "") in refreshed)
    step("tickets", TICKETS_TABLE, lambda: itil_once()[1], prune=lambda vals: True)

    if not only or only == "alerts":
        try:
            table = next((t for t in client.tables() if t["title"] == ALERTS_TITLE), None)
            if table:
                have = {c["title"]: c["id"] for c in client.columns(table["id"])}
                for title, kind in ALERTS_DERIVED:
                    if title not in have:
                        have[title] = client.create_column(table["id"], title, kind)["id"]
                derived = alert_derived(client.rows(table["id"]), {v: k for k, v in have.items()})
                rep.tables[ALERTS_TITLE] = {"id": table["id"],
                                            **upsert(client, table["id"], have, derived).as_dict()}
        except Exception as exc:
            rep.errors.append(f"alerts: {type(exc).__name__}: {exc}")
    return rep
