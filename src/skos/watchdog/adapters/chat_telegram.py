"""chat.telegram: read-only narration of the Telegram window (WD-6, Phase 2).

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.5: "Phase 2
adds ... `chat.telegram` (`skcapstone telegram` window read)".

THE RULE THIS MODULE EXISTS TO HOLD
-----------------------------------
SUMMARIES AND REFS ONLY. The digest is published: written to disk and served
over HTTP (capauth-gated at ``GET /api/v1/watchdog/digest``). A Telegram
message body copied into it is a copy of Chef's private correspondence
sitting in a served file.

Held structurally, the same way ``chat_skchat.py`` holds it: ``_window_rows``
is the only place poll output becomes data here, and it projects each row to
``{msg_id, sender}``. The parsed ``text`` is dropped AT THE BOUNDARY, so
there is no message text anywhere in this module's data flow for a summary
to accidentally carry.

What a line says, and what it deliberately leaves out:

  says     how many new messages in which chat, which senders (the display
           names the poll table already shows), and who spoke last.
  omits    every message body. There is no per-message line at all: one
           roll-up per chat, because a Telegram window is high volume and N
           bodyless per-message lines would be N useless lines. The deep link
           opens the real chat so Chef reads the originals there.

The read path, honestly described
---------------------------------
``skcapstone telegram poll <chat> --since <date> --limit <n>`` emits a rich
table, not JSON, so the parse is tolerant. It reuses
``adapters/grading.py``'s ``_parse_telegram_table`` rather than defining a
second one: two parsers over the same table drift independently, and a
sender column parsed two ways is exactly the kind of split universe this
codebase has been bitten by before.

``--since`` is DAY granularity (``YYYY-MM-DD``, see ``skcapstone telegram
poll --help``), so a window read is a slight OVER-read at the day boundary:
a few messages from earlier in the window's first day can be counted. That
direction is chosen deliberately. An over-read makes a count slightly
generous; an under-read would make a quiet-looking digest that is not true,
and a false all-clear is the one failure this design refuses.

Severity discipline
-------------------
  info      the ordinary case: the chat had new messages and Chef spoke last.
  notable   the newest message is not the operator's
            (``SKWATCHDOG_OPERATOR_TG_NAME``): somebody is waiting.
  problem   NOTHING here ever earns it. A ``problem`` files a GTD item (WD-8)
            and can escalate to a coord card (WD-9); no volume of unanswered
            Telegram is a fault, and the only thing that could justify one
            would be a judgement about what a message SAYS, which requires
            reading bodies, which this card forbids outright. A source that
            cannot be read at all is still only ``notable``
            (``events.source_unavailable``), by the same reasoning.

Fail-safe: no configured chat means an honest empty result, never an error
(the same quiet skip ``adapters/grading.py`` makes). A chat that cannot be
polled becomes a visible gap line; if EVERY configured chat fails, the
exception propagates so ``collect_safe`` marks the whole source not-ok in
``per_source`` rather than reporting a healthy source that read nothing.

Read-only, absolutely: this polls and never sends, never marks read, never
edits or deletes anything in any chat.
"""
from __future__ import annotations

import os
import subprocess

from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, now_iso, registry
from ...secret_env import resolve as _resolve
# Reused, never re-implemented (see module docstring): one tolerant parser
# for `skcapstone telegram poll`'s table, shared with the grading adapter.
from .grading import _parse_telegram_table

SKCAP_BIN = os.environ.get("SKCAPSTONE_BIN", "skcapstone")

#: Chef's Telegram display name as the poll table renders it. Messages from
#: anyone else arriving last are what make a chat `notable`.
OPERATOR_TG_NAME = os.environ.get("SKWATCHDOG_OPERATOR_TG_NAME", "Chef")

#: Max messages pulled per chat per run. A cap, not a cursor.
TG_LIMIT = int(os.environ.get("SKWATCHDOG_TG_LIMIT", "100"))

#: Clickable web link base. Empty by default: a public t.me URL cannot be
#: built from a poll-table row without the chat's internal numeric id, and
#: this module does not guess links. `uri` always carries the resolvable form.
TELEGRAM_WEB = os.environ.get("SKWATCHDOG_TELEGRAM_WEB", "").rstrip("/")


def configured_chats() -> list[str]:
    """Chats to narrate: ``SKWATCHDOG_TG_CHATS`` (comma-separated) if set,
    else the single capture chat the GTD telegram adapter already uses. Both
    resolve through ``skos.secret_env``, so no chat id is hardcoded in this
    public repo. Unconfigured is an empty list, which is a quiet empty run."""
    raw = _resolve("SKWATCHDOG_TG_CHATS", "") or ""
    chats = [c.strip() for c in raw.split(",") if c.strip()]
    if chats:
        return chats
    one = _resolve("GTD_TG_CHAT", "") or ""
    return [one] if one else []


def _poll(chat: str, since_day: str, limit: int) -> str:
    """The real read boundary: raw stdout of a read-only poll. Raises on any
    subprocess failure so the caller can decide between a per-chat gap line
    and a whole-source failure. Monkeypatched wholesale in tests; nothing
    else in this module shells out."""
    cmd = [SKCAP_BIN, "telegram", "poll", chat, "--limit", str(limit)]
    if since_day:
        cmd += ["--since", since_day]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=True)
    return r.stdout


def _window_rows(chat: str, since_day: str, limit: int) -> list[dict]:
    """Poll one chat and project every row to ``{msg_id, sender}``. The
    parsed message text is dropped HERE, at the boundary (module docstring),
    so no body exists downstream to leak into a summary."""
    rows = _parse_telegram_table(_poll(chat, since_day, limit))
    return [{"msg_id": r["msg_id"], "sender": r["sender"]} for r in rows]


@registry.register
class ChatTelegramAdapter(WatchdogSourceAdapter):
    name = "chat.telegram"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        chats = configured_chats()
        if not chats:
            return []

        since_day = window.since[:10]
        date = window.until[:10]
        out: list[WatchdogEvent] = []
        failures = 0
        last_error = ""

        for chat in chats:
            try:
                rows = _window_rows(chat, since_day, TG_LIMIT)
            except Exception as exc:  # noqa: BLE001 - deliberate, see module docstring
                failures += 1
                last_error = str(exc)
                out.append(source_unavailable(f"{self.name}:{chat}",
                                              ts=now_iso(), error=str(exc)))
                continue
            if rows:
                out.append(self._activity_event(chat, rows, window, date))

        if failures and failures == len(chats):
            # Every configured chat failed. Do NOT report a healthy source
            # that happened to see nothing (module docstring): let this reach
            # collect_safe so per_source["chat.telegram"].ok goes false.
            raise RuntimeError(
                f"every configured Telegram chat failed to poll ({last_error})")
        return out

    def _activity_event(self, chat: str, rows: list[dict], window: Window,
                         date: str) -> WatchdogEvent:
        """Built from counts and sender names only; no message text is in
        scope here to put into a summary."""
        last_sender = rows[-1]["sender"]
        awaiting = last_sender != OPERATOR_TG_NAME
        others = sorted({r["sender"] for r in rows if r["sender"] != OPERATOR_TG_NAME})
        if len(others) == 1:
            who = others[0]
        elif others:
            who = f"{len(others)} people"
        else:
            who = "you"
        tail = ("the last is not yours, so it is waiting on you."
                if awaiting else "you spoke last.")
        return WatchdogEvent(
            # The poll table carries no reliable per-row timestamp (the
            # existing GTD parser drops it too), so every telegram line is
            # stamped at the window's end, the same treatment itil/atlas give
            # standing items without a precise ts.
            ts=window.until, source=self.name, kind="ChatActivity", object=chat,
            severity="notable" if awaiting else "info",
            summary=(f"{len(rows)} new Telegram message(s) in {chat} "
                     f"from {who}; {tail}"),
            link=WatchdogLink(
                uri=f"skworld://skcomms/telegram/{chat}",
                http=f"{TELEGRAM_WEB}/{chat}" if TELEGRAM_WEB else ""),
            ref=f"{self.name}:{chat}:{date}",
            meta={"messages": len(rows), "senders": others,
                  "last_sender": last_sender, "awaiting_operator": awaiting,
                  "last_msg_id": rows[-1]["msg_id"]},
        )


__all__ = ["ChatTelegramAdapter", "configured_chats", "OPERATOR_TG_NAME", "TG_LIMIT"]
