"""chat.skchat: read-only narration of skchat thread activity (WD-6, Phase 2).

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.3's source
table and section 6.5 ("Phase 2 adds `chat.skchat` (MCP `search_messages` /
`list_threads`)").

THE RULE THIS MODULE EXISTS TO HOLD
-----------------------------------
SUMMARIES AND REFS ONLY. The digest is a published artifact: it is written
to disk and served over HTTP by skcode-hostd at
``GET /api/v1/watchdog/digest`` (capauth-gated, scope ``skcode.stream``).
A message body copied into it is a copy of Chef's private correspondence
sitting in a served file, forever, for anyone who can reach that endpoint.

This module holds that rule STRUCTURALLY rather than by good intentions:
``_load_messages`` is the ONLY place skchat data enters this module, and it
projects each message down to ``{id, sender, thread_id, timestamp,
delivery_status}``. It never copies ``content``. There is therefore no
message body anywhere in this module's data flow to leak into a summary,
even by accident, even under a future edit -- the body is dropped at the
boundary, not filtered at the exit.

What a line says, and what it deliberately leaves out:

  says     how many new messages, which thread (id), which sender identities
           (short capauth local-parts), and who spoke last. All
           sender/thread-level facts, the band the card allows.
  omits    message bodies (never read), and thread TITLES. A thread title is
           user-authored prose that often restates the topic of the
           conversation, which is exactly the content this card protects; the
           same judgement that keeps email subject lines out of
           `adapters/email.py` keeps thread titles out of here. Thread
           identity is carried by the thread id plus its participants, which
           is enough to find the thread, and the deep link opens the real
           thread so Chef reads the original there.

Why `list_threads` and `load`, and NOT `search_messages`
--------------------------------------------------------
``search_messages`` is a relevance-ranked full-text search: it needs a query
string and returns the best matches, not everything in a time window. A
digest window read must be COMPLETE for its window (an incomplete read is a
false all-clear, the one failure mode this whole design forbids), and
inventing query terms to feed it would mean guessing at content. So the
window read is ``ChatHistory.load(since=...)`` (the same real read boundary
``adapters/grading.py`` already uses) and ``list_threads()`` supplies thread
identity. ``search_messages`` stays unused on purpose.

Severity discipline
-------------------
Chat is a high-volume source, and a ``problem`` here files a GTD item (WD-8)
and can escalate to a coord card (WD-9). So:

  info      the ordinary case: a thread had new messages and Chef spoke last.
  notable   a thread whose newest message in the window is NOT the operator's
            (``SKWATCHDOG_OPERATOR_SKCHAT_ID``): somebody is waiting on a
            reply. Worth a human's eye, never an alarm.
  problem   EXACTLY ONE thing earns it, and it is not correspondence at all:
            messages sitting in ``delivery_status == "failed"``. That is an
            outbound message someone believed was sent that never left the
            box, which is an infrastructure fault with a real fix, genuinely
            rare, and assessable without reading a single body. No volume of
            unanswered chat is ever a ``problem``: "Chef has not replied yet"
            is not a fault, and turning it into tracked work would flood the
            GTD from a source that is supposed to narrate, not nag.

Fail-safe: ``skchat`` is an OPTIONAL sibling package, imported lazily inside
the read boundary so an absent skchat degrades THIS ONE adapter to a single
``SourceUnavailable`` digest line via ``skos.watchdog.port.collect_safe``,
never a missing digest and never a silent empty read that would look like a
quiet day. Thread metadata is the one best-effort read: ``list_threads``
failing costs participant names, not the whole source, so it degrades to
"no thread metadata" and the message read carries on.

Read-only, absolutely: nothing here sends, marks read, edits, or deletes
anything in any thread.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from ..events import WatchdogEvent, WatchdogLink
from ..port import Window, WatchdogSourceAdapter, registry

#: Chef's own skchat identity. Messages from anyone else, arriving last in a
#: thread, are what makes that thread `notable` (someone is waiting).
OPERATOR_ID = os.environ.get("SKWATCHDOG_OPERATOR_SKCHAT_ID", "capauth:chef@skworld.io")

#: How many messages the window read pulls before window filtering. A cap,
#: not a cursor: the cursor (skos.watchdog.cursor) decides the window; this
#: only stops a first-ever run with a wide lookback from loading the world.
MESSAGE_READ_LIMIT = int(os.environ.get("SKWATCHDOG_SKCHAT_MSG_LIMIT", "500"))

#: How many threads get their own digest line. Beyond this, the remainder
#: collapses into one honest roll-up line rather than flooding the digest.
MAX_THREADS_REPORTED = int(os.environ.get("SKWATCHDOG_SKCHAT_MAX_THREADS", "20"))

#: Clickable web link base for a skchat thread. Empty by default on purpose:
#: `adapters/grading.py` sets `http=""` for skchat threads for the same
#: reason, that there is no invented URL here. Set this to make the digest's
#: skchat lines clickable; `uri` always carries the resolvable form.
SKCHAT_WEB = os.environ.get("SKWATCHDOG_SKCHAT_WEB", "").rstrip("/")


def _parse_ts(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def _short_id(identity: str) -> str:
    """`capauth:chef@skworld.io` -> `chef`. Identity-level, short enough for a
    digest line, and it never touches message content."""
    s = str(identity or "").split(":", 1)[-1]
    return s.split("@", 1)[0] or "someone"


def _status_value(status) -> str:
    """delivery_status arrives as a DeliveryStatus enum from the library and
    as a plain string from a JSON-backed store; both normalize here."""
    return str(getattr(status, "value", status) or "").lower()


def _load_messages(since_dt: datetime, limit: int) -> list[dict]:
    """The ONLY place skchat message data enters this module.

    Projects every message to ``{id, sender, thread_id, timestamp,
    delivery_status}``. ``content`` is deliberately NOT copied: the body is
    dropped here, at the boundary, so no later edit to this module can leak
    one into a published digest (module docstring).

    Raises on any failure (skchat absent, a store the library itself will not
    open) so ``collect_safe`` can degrade this whole source to one visible
    line. Monkeypatched wholesale in tests; nothing else here touches skchat.
    """
    from skchat.history import ChatHistory  # optional sibling, see module docstring

    history = ChatHistory()
    out = []
    for m in history.load(since=since_dt, limit=limit):
        out.append({
            "id": getattr(m, "id", "") or "",
            "sender": getattr(m, "sender", "") or "",
            "thread_id": getattr(m, "thread_id", "") or "",
            "timestamp": getattr(m, "timestamp", None),
            "delivery_status": _status_value(getattr(m, "delivery_status", "")),
        })
    return out


def _load_thread_meta() -> dict[str, dict]:
    """Thread identity via the MCP surface's ``list_threads``. Best-effort by
    design (module docstring): a failure here costs participant names, not
    the source, so the caller swallows it and carries on with the message
    read. Titles are read but never propagated (see the omits list)."""
    from skchat.history import ChatHistory  # optional sibling, see module docstring

    meta: dict[str, dict] = {}
    for t in ChatHistory().list_threads(limit=MAX_THREADS_REPORTED * 5):
        tid = str(t.get("thread_id") or "")
        if tid:
            meta[tid] = {"participants": list(t.get("participants") or [])}
    return meta


@registry.register
class ChatSkchatAdapter(WatchdogSourceAdapter):
    name = "chat.skchat"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        since_dt = _parse_ts(window.since) or datetime.min.replace(tzinfo=timezone.utc)
        until_dt = _parse_ts(window.until) or datetime.now(timezone.utc)
        rows = _load_messages(since_dt, MESSAGE_READ_LIMIT)

        try:
            thread_meta = _load_thread_meta()
        except Exception:  # noqa: BLE001 - best-effort enrichment only, see docstring
            thread_meta = {}

        # thread_id -> rows in this window, oldest first (so [-1] is "who
        # spoke last", the fact that decides notable vs info).
        by_thread: dict[str, list[dict]] = {}
        for row in rows:
            ts_dt = _parse_ts(row["timestamp"])
            if ts_dt is None or not (since_dt <= ts_dt <= until_dt):
                continue
            row["_ts"] = ts_dt
            by_thread.setdefault(row["thread_id"] or row["id"], []).append(row)
        for msgs in by_thread.values():
            msgs.sort(key=lambda r: r["_ts"])

        date = window.until[:10]
        ordered = sorted(by_thread.items(), key=lambda kv: kv[1][-1]["_ts"], reverse=True)

        out: list[WatchdogEvent] = []
        for thread_id, msgs in ordered[:MAX_THREADS_REPORTED]:
            out.append(self._activity_event(thread_id, msgs, thread_meta, date))
            failed = [m for m in msgs if m["delivery_status"] == "failed"]
            if failed:
                out.append(self._delivery_failed_event(thread_id, failed, date))

        remainder = ordered[MAX_THREADS_REPORTED:]
        if remainder:
            n_msgs = sum(len(m) for _, m in remainder)
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="ThreadActivityRollup",
                object="skchat", severity="info",
                summary=(f"{len(remainder)} further skchat thread(s) had activity "
                         f"({n_msgs} message(s)), not itemized here."),
                link=self._link(""),
                ref=f"{self.name}:rollup:{date}",
                meta={"threads": len(remainder), "messages": n_msgs},
            ))
        return out

    # -- event builders. Every summary below is built from counts and
    # -- identities only; no `content` value exists in scope to put in one.

    def _activity_event(self, thread_id: str, msgs: list[dict],
                         thread_meta: dict[str, dict], date: str) -> WatchdogEvent:
        last_sender = msgs[-1]["sender"]
        awaiting = last_sender != OPERATOR_ID
        others = sorted({m["sender"] for m in msgs if m["sender"] != OPERATOR_ID})
        if len(others) == 1:
            who = _short_id(others[0])
        elif others:
            who = f"{len(others)} people"
        else:
            who = "you"
        tail = ("the last is not yours, so it is waiting on you."
                if awaiting else "you spoke last.")
        return WatchdogEvent(
            ts=msgs[-1]["_ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            source=self.name, kind="ThreadActivity", object=thread_id,
            severity="notable" if awaiting else "info",
            summary=(f"{len(msgs)} new skchat message(s) in thread "
                     f"{thread_id[:8]} from {who}; {tail}"),
            link=self._link(thread_id),
            ref=f"{self.name}:{thread_id}:{date}",
            meta={"messages": len(msgs), "senders": others,
                  "last_sender": last_sender, "awaiting_operator": awaiting,
                  "participants": thread_meta.get(thread_id, {}).get("participants", [])},
        )

    def _delivery_failed_event(self, thread_id: str, failed: list[dict],
                                date: str) -> WatchdogEvent:
        """The one `problem` this adapter can raise: an infrastructure fault
        (a message that never left), not a judgement about correspondence.
        See the severity-discipline section of the module docstring."""
        return WatchdogEvent(
            ts=failed[-1]["_ts"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            source=self.name, kind="MessageDeliveryFailed", object=thread_id,
            severity="problem",
            summary=(f"{len(failed)} skchat message(s) in thread {thread_id[:8]} "
                     f"are stuck in delivery status failed."),
            link=self._link(thread_id),
            ref=f"{self.name}:{thread_id}:delivery-failed:{date}",
            meta={"failed": len(failed),
                  "message_ids": [m["id"] for m in failed]},
        )

    def _link(self, thread_id: str) -> WatchdogLink:
        if not thread_id:
            return WatchdogLink(uri="skworld://skchat/threads",
                                http=f"{SKCHAT_WEB}/" if SKCHAT_WEB else "")
        return WatchdogLink(
            uri=f"skworld://skchat/thread/{thread_id}",
            http=f"{SKCHAT_WEB}/app/#/chat/thread/{thread_id}" if SKCHAT_WEB else "")


__all__ = ["ChatSkchatAdapter", "MAX_THREADS_REPORTED", "MESSAGE_READ_LIMIT",
           "OPERATOR_ID"]
