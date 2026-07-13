"""The morning numbered digest and its stable-within-a-day manifest.

Numbers are assigned over the unanswered ``source="autopilot"`` decision items in
the GTD store, ordered by priority then created_at, and pinned in
``autopilot-digest.json`` so a reply-by-number resolves the same item all day
(spec section 9). Sending the DM is Phase F; this module only builds and persists.
"""
from __future__ import annotations

import json
from pathlib import Path

PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_GTD_FILES = ["inbox.json", "next-actions.json", "projects.json",
              "waiting-for.json", "someday-maybe.json", "archive.json"]


def _load_store_items() -> list[dict]:
    from skos.gtd_ingest import gtd_dir
    items: list[dict] = []
    for fname in _GTD_FILES:
        p = gtd_dir() / fname
        if p.exists():
            try:
                items += json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return items


def build_manifest(items: list[dict] | None = None, *, digest_date: str,
                   sent_at: str | None = None) -> dict:
    """Build the digest manifest over unanswered autopilot decision items."""
    if items is None:
        items = _load_store_items()
    unanswered = [it for it in items
                  if it.get("source") == "autopilot"
                  and not (it.get("decision") or {}).get("answered")]
    ordered = sorted(unanswered,
                     key=lambda it: (PRIORITY_RANK.get(it.get("priority") or "medium", 2),
                                     it.get("created_at") or ""))
    manifest_items = []
    for n, it in enumerate(ordered, 1):
        dec = it.get("decision") or {}
        manifest_items.append({"n": n, "qid": dec.get("qid"), "id": it.get("id"),
                               "source_ref": it.get("source_ref"), "prompt": dec.get("prompt"),
                               "options": dec.get("options"), "answered": False})
    return {"digest_date": digest_date, "sent_at": sent_at, "items": manifest_items}


def build_digest_text(manifest: dict) -> str:
    """Render the reply-by-number DM body."""
    lines = ["Morning decisions (reply with the number):"]
    for it in manifest["items"]:
        opts = it.get("options") or {}
        optstr = "/".join(opts.keys()) if isinstance(opts, dict) else ""
        suffix = f"  [{optstr}]" if optstr else ""
        lines.append(f"{it['n']}. {it['prompt']}{suffix}")
    lines.append('Reply "1 yes" or just "1".')
    return "\n".join(lines)


def write_manifest(manifest: dict) -> Path:
    """Persist the manifest to gtd_dir()/autopilot-digest.json."""
    from skos.gtd_ingest import gtd_dir
    p = gtd_dir() / "autopilot-digest.json"
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                 encoding="utf-8")
    return p
