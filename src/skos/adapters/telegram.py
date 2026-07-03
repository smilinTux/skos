"""Telegram → GTD pull adapter (skcapstone-telegram-backed).

Convention-based capture (no noise): Chef marks a DM message with a trigger
prefix — ``todo:`` / ``task:`` / ``gtd:`` / ``capture:`` / ``remind:`` — and this
adapter files the rest as a GTD next-action. Deduped by ``chat:msg_id``, so
re-polling never duplicates. Polls the last N messages of the capture chat
(default: Chef's DM) since ``--since`` / GTD_TG_SINCE.

skcapstone telegram poll emits a rich table (no JSON), so the parser is tolerant:
rows begin when the ID column holds a number; continuation lines append text.
"""
from __future__ import annotations

import os
import re
import subprocess

from ..gtd_ingest import GtdCapture, GtdSourceAdapter

SKCAP = os.environ.get("SKCAPSTONE_BIN", "skcapstone")
TG_CHAT = os.environ.get("GTD_TG_CHAT", "1594678363")   # Chef's DM
TG_LIMIT = int(os.environ.get("GTD_TG_LIMIT", "25"))
_TRIGGERS = ("todo", "task", "gtd", "capture", "remind", "action")
_TRIG_RE = re.compile(r"^\s*(?:%s)\s*[:\-]\s*(.+)$" % "|".join(_TRIGGERS), re.IGNORECASE | re.DOTALL)


def _parse_poll(text: str) -> list[tuple[str, str]]:
    """Return [(msg_id, text)] from the rich-table poll output (tolerant)."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if "│" not in line:
            continue
        cols = [c.strip() for c in line.split("│")]
        # expected: ['', ID, Date, Sender, Text, '']  (indices vary; ID col = 1)
        if len(cols) < 5:
            continue
        idcol, txtcol = cols[1], cols[-2]
        if idcol.isdigit():
            rows.append([idcol, txtcol])          # new message
        elif rows and (txtcol or not idcol):
            rows[-1][1] = (rows[-1][1] + " " + txtcol).strip()  # continuation
    return [(r[0], r[1]) for r in rows if r[0]]


class TelegramAdapter(GtdSourceAdapter):
    name = "telegram"

    def poll(self) -> list[GtdCapture]:
        cmd = [SKCAP, "telegram", "poll", TG_CHAT, "--limit", str(TG_LIMIT)]
        since = os.environ.get("GTD_TG_SINCE")
        if since:
            cmd += ["--since", since]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
        except Exception:
            return []
        caps: list[GtdCapture] = []
        for mid, body in _parse_poll(out):
            m = _TRIG_RE.match(body)
            if not m:
                continue
            task = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
            if not task:
                continue
            caps.append(GtdCapture(
                text=f"[tg] {task}", source="telegram", source_ref=f"{TG_CHAT}:{mid}",
                context="@telegram", priority="medium", status="inbox",
                meta={"tg_chat": TG_CHAT, "tg_msg_id": mid}))
        return caps
