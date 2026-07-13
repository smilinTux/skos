"""resolver.answer - the single shared front-door body for numbered decisions.

Both doors (the `skos autopilot answer N` CLI now, the Telegram intercept in
v1.5) converge here. It reads the per-day digest manifest, finds (qid,
source_ref) for ordinal n, records the answer, transitions the GTD decision
item via the gtd_ingest port (upsert reconciles by (source, source_ref)), marks
the manifest answered. Idempotent: re-answering an already-answered n performs
no store write.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from skos import gtd_ingest


class UnknownDecision(ValueError):
    """No manifest, or no decision numbered n in today's digest."""


def _manifest_path() -> Path:
    return gtd_ingest.gtd_dir() / "autopilot-digest.json"


def _load_manifest() -> dict:
    p = _manifest_path()
    if not p.exists():
        raise UnknownDecision("no autopilot-digest.json manifest present")
    return json.loads(p.read_text(encoding="utf-8"))


def _save_manifest(m: dict) -> None:
    _manifest_path().write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")


def answer(n: int, response: str | None = None) -> dict:
    """Resolve a numbered decision from the digest manifest.

    Args:
        n: The ordinal number from the digest.
        response: The answer text (optional).

    Returns:
        A dict with keys: n, qid, source_ref, answer, answered, action_ref,
        and (on idempotent re-answer) idempotent=True, or (on first answer)
        gtd_action=(updated|completed).

    Raises:
        UnknownDecision: If manifest is missing or n not found.
    """
    m = _load_manifest()
    entry = next((it for it in m.get("items", []) if it.get("n") == n), None)
    if entry is None:
        raise UnknownDecision(f"no decision numbered {n} in today's digest")

    qid = entry["qid"]
    source_ref = entry["source_ref"]
    result: dict = {"n": n, "qid": qid, "source_ref": source_ref,
                    "answer": response, "answered": True,
                    "action_ref": entry.get("action_ref")}

    if entry.get("answered"):                     # already resolved: no re-write
        result["idempotent"] = True
        return result

    cap = gtd_ingest.GtdCapture(
        text=entry.get("prompt", ""), source="autopilot", source_ref=source_ref,
        status="done", context="@decide", priority="high",
        meta={"decision": {"qid": qid, "prompt": entry.get("prompt"),
                           "options": entry.get("options", {}),
                           "answered": True, "answer": response,
                           "action_ref": entry.get("action_ref"),
                           "answered_at": datetime.now(timezone.utc).isoformat()}})
    _gid, action = gtd_ingest.upsert(cap)
    result["gtd_action"] = action

    entry["answered"] = True
    entry["answer"] = response
    _save_manifest(m)
    return result
