"""Autopilot run journal: ~/.skcapstone/coordination/autopilot/runs/<run_id>.json.

Records per-item state (selected|claimed|implementing|round_k|finalized|
escalated|error), per-round scores, and token/cost totals, so a crashed run
resumes: a reopened journal skips finalized/escalated items and re-enters the
rest (spec section 3). Single-writer, atomic tmp + os.replace.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_TERMINAL = {"finalized", "escalated"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runs_dir() -> Path:
    env = os.environ.get("SK_AUTOPILOT_RUNS_DIR")
    if env:
        d = Path(env).expanduser()
    else:
        home = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
        d = home / "coordination" / "autopilot" / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class RunJournal:
    """One run's state file. Every mutator writes atomically."""

    def __init__(self, run_id: str, data: dict, path: Path) -> None:
        self.run_id = run_id
        self.data = data
        self.path = path

    @classmethod
    def open(cls, run_id: str) -> "RunJournal":
        path = runs_dir() / f"{run_id}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = {"run_id": run_id, "created_at": _now(),
                    "items": {}, "tokens": 0, "cost_usd": 0.0}
        return cls(run_id, data, path)

    def _save(self) -> None:
        self.data["updated_at"] = _now()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def set_item(self, ref: str, state: str, **extra) -> None:
        it = self.data["items"].setdefault(ref, {})
        it["state"] = state
        it["updated_at"] = _now()
        it.update(extra)
        self._save()

    def add_score(self, ref: str, round: int, score: int) -> None:
        it = self.data["items"].setdefault(ref, {})
        it.setdefault("scores", []).append(
            {"round": round, "score": score, "ts": _now()})
        self._save()

    def add_cost(self, tokens: int, cost_usd: float) -> None:
        self.data["tokens"] = self.data.get("tokens", 0) + tokens
        self.data["cost_usd"] = round(self.data.get("cost_usd", 0.0) + cost_usd, 6)
        self._save()

    def state(self, ref: str) -> str | None:
        it = self.data["items"].get(ref)
        return it.get("state") if it else None

    def is_terminal(self, ref: str) -> bool:
        return self.state(ref) in _TERMINAL

    def resumable_items(self) -> list[str]:
        """Refs not yet finalized/escalated: a resumed run re-enters these."""
        return [ref for ref, it in self.data["items"].items()
                if it.get("state") not in _TERMINAL]


def read_run(run_id: str) -> dict:
    """Read a run from disk. Returns {} if absent."""
    p = runs_dir() / f"{run_id}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def write_run(run_id: str, data: dict) -> None:
    """Write a run to disk atomically."""
    p = runs_dir() / f"{run_id}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    os.replace(tmp, p)


class RunHandle:
    """Per-run view used by executors: mutate one run's items through read/write_run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    def _mutate(self, ref: str, patch: dict) -> None:
        data = read_run(self.run_id) or {"run_id": self.run_id, "items": {},
                                         "tokens": 0, "cost_usd": 0.0}
        item = data.setdefault("items", {}).setdefault(ref, {})
        item.update(patch)
        item["updated_at"] = _now()
        write_run(self.run_id, data)

    def record_claim(self, ref: str, claimed_at: str) -> None:
        self._mutate(ref, {"state": "claimed", "claimed_at": claimed_at})

    def set_worktree(self, ref: str, path: str) -> None:
        self._mutate(ref, {"worktree": path})

    def worktree_for(self, ref: str) -> str | None:
        return ((read_run(self.run_id).get("items") or {}).get(ref) or {}).get("worktree")

    def set_item(self, ref: str, state: str, **extra) -> None:
        self._mutate(ref, {"state": state, **extra})


def handle(run_id: str) -> RunHandle:
    """Return a RunHandle for mutating a run's items."""
    return RunHandle(run_id)


def render_status() -> str:
    """Render status line for the latest run."""
    runs = sorted(runs_dir().glob("*.json"))
    if not runs:
        return "no autopilot runs yet"
    latest = read_run(runs[-1].stem)
    items = latest.get("items", {})
    return (f"run {latest.get('run_id')} phase={latest.get('phase')} "
            f"items={len(items)} decisions={latest.get('decisions', 0)} "
            f"dry_run={latest.get('dry_run')}")


def render_run(run_id: str) -> str:
    """Render a run as JSON."""
    return json.dumps(read_run(run_id), indent=2, default=str)


def render_list(what: str) -> list[str]:
    """Render list of runs, claims, or decisions."""
    if what == "runs":
        return [p.stem for p in sorted(runs_dir().glob("*.json"))]
    if what == "claims":
        out = []
        for p in sorted(runs_dir().glob("*.json")):
            for ref, it in (read_run(p.stem).get("items") or {}).items():
                if it.get("state") == "claimed":
                    out.append(f"{ref} claimed_at={it.get('claimed_at')}")
        return out
    # decisions live in the digest manifest, not the run journal
    from skos.gtd_ingest import gtd_dir
    mp = gtd_dir() / "autopilot-digest.json"
    if not mp.exists():
        return []
    return [f"{i['n']}. {i['prompt']}" for i in json.loads(mp.read_text()).get("items", [])]
