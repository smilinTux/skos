"""SK Ops Tables feeder: collectors and the upsert against an in-memory fake."""

import json
from datetime import datetime, timezone

from skos import skops_tables as ops


class FakeTables:
    def __init__(self):
        self._tables, self._cols, self._rows, self.next_id, self.writes = [], {}, {}, 1, 0

    def _id(self):
        self.next_id += 1
        return self.next_id

    def tables(self):
        return self._tables

    def create_table(self, title, emoji):
        t = {"id": self._id(), "title": title}
        self._tables.append(t)
        self._cols[t["id"]], self._rows[t["id"]] = [], {}
        return t

    def columns(self, tid):
        return self._cols[tid]

    def create_column(self, tid, title, kind):
        c = {"id": self._id(), "title": title}
        self._cols[tid].append(c)
        return c

    def rows(self, tid):
        return [{"id": rid, "data": [{"columnId": c, "value": v} for c, v in d.items()]}
                for rid, d in self._rows[tid].items()]

    def create_row(self, tid, data):
        self.writes += 1
        rid = self._id()
        self._rows[tid][rid] = dict(data)
        return {"id": rid}

    def update_row(self, rid, data):
        self.writes += 1
        for rows in self._rows.values():
            if rid in rows:
                rows[rid].update(data)

    def delete_row(self, rid):
        self.writes += 1
        for rows in self._rows.values():
            rows.pop(rid, None)


def test_upsert_creates_then_only_writes_changes_and_prunes():
    fake = FakeTables()
    tid, cols = ops.ensure_table(fake, ops.FLEET_TABLE)
    rows = [{"Host": "a", "Status": "ok", "Cores": 4, "Key": "nor|a"},
            {"Host": "b", "Status": "ok", "Cores": 2, "Key": "nor|b"}]
    assert ops.upsert(fake, tid, cols, rows).created == 2
    fake.writes = 0
    rep = ops.upsert(fake, tid, cols, rows)
    assert (rep.unchanged, fake.writes) == (2, 0)
    rows[0]["Status"] = "stale"
    rep = ops.upsert(fake, tid, cols, rows[:1], prune=lambda v: True)
    assert (rep.updated, rep.deleted) == (1, 1)
    assert ops.ensure_table(fake, ops.FLEET_TABLE)[0] == tid  # found, not recreated


def _obs(tmp_path, node, name, observed, aggs, mtime):
    d = tmp_path / "obs" / node / "u"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps({"measurement_lane": "harness_reported", "node_id": node,
                             "observed_at": observed, "aggregates": aggs}))
    import os
    os.utime(p, (mtime, mtime))


def _agg(day, model, msgs, cost):
    return {"view": "models", "bucket_start": f"{day}T00:00:00Z", "client": "claude",
            "provider": "anthropic", "model": model, "message_count": msgs,
            "tokens": {"input": 10, "output": 5, "cache_read": 1, "cache_write": 0},
            "cost": {"amount": cost}}


def test_spend_newest_observation_wins_and_is_incremental(tmp_path):
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    cache = tmp_path / "cache.json"
    _obs(tmp_path, "n1", "a.json", "2026-10-29T10:00:00Z",
         [_agg("2026-10-28", "m", 5, 1.0), {"view": "daily", "bucket_start": "2026-10-28T00:00:00Z"}], 100)
    _obs(tmp_path, "n1", "b.json", "2026-10-30T10:00:00Z", [_agg("2026-10-28", "m", 9, 2.5)], 200)
    rows = ops.spend_rows("nor", root=tmp_path / "obs", cache_path=cache, now=now)
    assert len(rows) == 1 and rows[0]["Messages"] == 9 and rows[0]["Cost USD"] == 2.5
    # an older file showing up later must not override the newer bucket
    _obs(tmp_path, "n1", "c.json", "2026-10-28T10:00:00Z", [_agg("2026-10-28", "m", 1, 0.1)], 300)
    rows = ops.spend_rows("nor", root=tmp_path / "obs", cache_path=cache, now=now)
    assert rows[0]["Messages"] == 9
    assert json.loads(cache.read_text())["seen_mtime"] == 300


def test_spend_drops_buckets_past_window(tmp_path):
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    _obs(tmp_path, "n1", "a.json", "2026-10-29T10:00:00Z",
         [_agg("2026-06-01", "m", 1, 1.0), _agg("2026-10-20", "m", 1, 1.0)], 100)
    rows = ops.spend_rows("nor", root=tmp_path / "obs", cache_path=tmp_path / "c.json", now=now, days=90)
    assert [r["Date"] for r in rows] == ["2026-10-20"]


def test_fleet_rows_status(tmp_path):
    now = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
    for host, beat, disk in (("a", "2026-10-30T19:55:00Z", 30.0), ("b", "2026-10-30T10:00:00Z", 30.0),
                             ("c", "2026-10-30T19:59:00Z", 4.0)):
        d = tmp_path / "status" / f"node-{host}"
        d.mkdir(parents=True)
        (d / "node.json").write_text(json.dumps({"status": {
            "capacity": {"cores": 4, "disk_gb": disk, "ram_gb": 8},
            "versions": {"skcapstone": "0.15.1"},
            "inventory": {"packages": {"skmemory": "0.11.73"}}}}))
        (d / "heartbeat.json").write_text(json.dumps({"ts": beat}))
    rows = {r["Host"]: r for r in ops.fleet_rows("nor", root=tmp_path, now=now)}
    assert rows["a"]["Status"] == "ok" and rows["a"]["skmemory"] == "0.11.73"
    assert rows["b"]["Status"] == "stale"
    assert rows["c"]["Status"] == "low disk"


def test_job_rows_counts_consecutive_failures_and_alerting():
    now = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
    recs = [
        {"job": "j", "host": "h", "start": "2026-10-30T10:00:00-04:00", "ok": True, "exit": 0, "dur_s": 1},
        {"job": "j", "host": "h", "start": "2026-10-30T11:00:00-04:00", "ok": False, "exit": 1, "dur_s": 2, "tail": "boom"},
        {"job": "j", "host": "h", "start": "2026-10-30T12:00:00-04:00", "ok": False, "exit": 1, "dur_s": 3, "tail": "boom2"},
        {"job": "k", "host": "h", "start": "2026-10-28T12:00:00-04:00", "ok": True, "exit": 0, "dur_s": 1},
    ]
    rows = {r["Job"]: r for r in ops.job_rows(recs, {"j"}, now=now)}
    j = rows["j"]
    assert (j["Last result"], j["Consecutive failures"], j["Runs 24h"], j["Failures 24h"]) == ("failed", 2, 3, 2)
    assert j["Alerting"] == "yes" and j["Last output"] == "boom2"
    assert rows["k"]["Runs 24h"] == 0 and rows["k"]["Alerting"] == "no"
