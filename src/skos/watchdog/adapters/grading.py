"""grading: the WD-7 grading-loop adapter.

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 7. Reads
Lumina's OUTBOUND replies on skchat and Telegram inside the digest window,
grades each independently against the versioned `lumina-replies` rubric
(`skos.watchdog.rubric`, `skos/watchdog/rubrics/lumina-replies.v1.yaml`) via
`skos.watchdog.grader`, and emits ORDINARY WatchdogEvents so a graded run
folds straight into the existing digest/renderer/deep-link machinery -- no
new digest key, no new render path (the WD-7 card: "fold results in as
ordinary WatchdogEvents so they render through the existing renderer and
carry deep links like everything else").

Report-only, exactly like every other Phase-1/2 source: this module NEVER
writes a GTD item, NEVER opens a card, and NEVER edits a soul, prompt, or
rubric file. A bad grade becomes one digest line; WD-8 (GTD upsert) and WD-9
(card dispatch) are separate, later, feature-flagged cards. If you find
yourself wanting this module to fix something, you have left the card.

Degrading honestly (card hard rule -- "a missing grade is fine; an invented
one is a lie in a document Chef trusts"): a skgateway outage or an
unparseable grader reply means that ONE reply is SKIPPED, never scored.
`grader.grade_one` never fabricates; this module never second-guesses that
by inventing its own fallback score either. If every reply in a run is
skipped, or the run's time budget runs out mid-list, the digest carries one
roll-up "grading gap" event, never silence and never a guess.

The digest must never be BLOCKED OR DELAYED by grading (card hard rule).
Two independent guards enforce that:
  - `GRADE_RUN_BUDGET_S`: a total wall-clock cap across every grade call in
    one run. Not a cost/joule budget (the card explicitly forbids adding
    one of those); a simple deterministic time cap so a hung skgateway (see
    inc-4b9f8e5e: ornith answers /v1/models but can hang on
    /v1/chat/completions) turns into "the rest of this run's replies are
    skipped", never "the digest is late". Once the budget is spent, every
    remaining reply counts toward the GradingGap line, exactly like any
    other skip.
  - `MAX_GRADED_PER_CHANNEL`: a per-channel cap on how many replies are
    even attempted, so a first-ever run with a wide lookback window can
    never turn into an unbounded burst of calls in the first place. A
    capped-out reply is simply not graded this run; nothing is lost (the
    source message store still has it) and nothing is retried (no loop).

Message-body privacy (the same rule WD-6 carries): raw reply/question text
is used only as ephemeral input to the grader's prompt and is NEVER placed
into a WatchdogEvent.summary or .meta -- every emitted event links to the
thread instead of quoting it.

Reading Lumina's own sends, per channel:
  skchat    `skchat.history.ChatHistory.load()` (JSONL-backed, real, clean:
            `ChatMessage.sender` is a stable CapAuth URI), lazy-imported
            inside `_load_skchat_messages` (skchat is an OPTIONAL sibling,
            exactly like skcapstone/skcoord elsewhere in this package),
            filtered to `SKWATCHDOG_LUMINA_SKCHAT_SENDER`.
  telegram  no clean self-flagged transcript exists today: the live bridge
            (skchat/scripts/bridge_consciousness.py) persists only a lossy,
            truncated two-way SUMMARY via skcapstone.memory_engine, not a
            proper archive, and the Telethon poll path
            (skmemory.importers.telegram_api) carries no "sent by me" flag
            at all. This adapter reads it the same way the existing
            `skos.adapters.telegram` GTD adapter does: shells out to
            `skcapstone telegram poll <chat>` (`_load_telegram_rows`), and
            identifies Lumina's own rows by the configured display name
            (`SKWATCHDOG_LUMINA_TG_SENDER`, default "Lumina"). This is a
            best-effort read of what exists today, not a new archive: a
            chat where no row matches that sender name simply yields no
            telegram items for that run, an honest empty result, not a
            failure. A cleaner transcript source is WD-6's to build.

Each channel read is independently fail-safe: a skchat outage does not
block telegram grading and vice versa (each degrades to its own
`SourceUnavailable`-shaped gap event via `_gather_replies`). A genuinely
unexpected exception anywhere else in `collect()` (e.g. a broken/missing
rubric file) still propagates normally to `collect_safe`, degrading the
WHOLE 'grading' source to one line -- there is no point grading half a
rubric.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .. import grader as grader_mod
from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, now_iso, registry
from ..rubric import RUBRICS_DIR, load_rubric
from ...secret_env import resolve as _resolve

RUBRIC_ID = "lumina-replies"

LUMINA_SKCHAT_SENDER = os.environ.get(
    "SKWATCHDOG_LUMINA_SKCHAT_SENDER", "capauth:lumina@skworld.io")
LUMINA_TG_SENDER = os.environ.get("SKWATCHDOG_LUMINA_TG_SENDER", "Lumina")
SKCAP_BIN = os.environ.get("SKCAPSTONE_BIN", "skcapstone")

#: Hard per-run, per-channel cap on how many replies are even attempted
#: (see module docstring). Not a retry mechanism; a capped-out reply is
#: simply not graded this run.
MAX_GRADED_PER_CHANNEL = 25

#: Total wall-clock budget across every grade call in one run (see module
#: docstring). Deliberately independent of `grader.DEFAULT_TIMEOUT_S`: a
#: single call may be fast, but many sequential calls under a partially-hung
#: gateway must still bound the WHOLE run, not just each call.
GRADE_RUN_BUDGET_S = 90.0

#: Per-call timeout used inside a grading run, shorter than
#: `grader.DEFAULT_TIMEOUT_S` on purpose: this adapter may issue up to
#: 2 * MAX_GRADED_PER_CHANNEL calls in one run, so each one gets a tighter
#: budget than a single one-off call (e.g. the digest headline) would.
PER_CALL_TIMEOUT_S = 15.0


@dataclass
class OutboundReply:
    channel: str            # "skchat" | "telegram"
    subject_ref: str        # stable id; becomes part of the WatchdogEvent.ref
    ts: str                 # ISO8601 UTC
    question: str           # best-effort context Lumina was replying to
    reply: str               # Lumina's own message text
    link: WatchdogLink


def _parse_ts(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


# --------------------------------------------------------------- skchat ----

def _load_skchat_messages(since_dt: datetime, limit: int) -> list[dict]:
    """Real read boundary (see module docstring). Raises on any failure
    (skchat absent, a corrupt store the library itself does not tolerate,
    etc.) so `_gather_replies` can degrade JUST the skchat channel to one
    gap event. Monkeypatched wholesale in tests; nothing else in this
    module ever touches skchat directly."""
    from skchat.history import ChatHistory  # optional sibling, see module docstring

    history = ChatHistory()
    messages = history.load(since=since_dt, limit=limit)
    out = []
    for m in messages:
        out.append({
            "id": getattr(m, "id", "") or "",
            "sender": getattr(m, "sender", "") or "",
            "content": getattr(m, "content", "") or "",
            "timestamp": getattr(m, "timestamp", None),
            "thread_id": getattr(m, "thread_id", "") or "",
        })
    return out


def _skchat_replies(window: Window, *, sender: str = LUMINA_SKCHAT_SENDER,
                     limit: int = 200) -> list[OutboundReply]:
    since_dt = _parse_ts(window.since) or datetime.min.replace(tzinfo=timezone.utc)
    until_dt = _parse_ts(window.until) or datetime.now(timezone.utc)
    rows = _load_skchat_messages(since_dt, limit)
    # ChatHistory.load() returns newest-first; walk oldest-first so a prior
    # message in the same thread is a genuine "what came before" context.
    ordered = list(reversed(rows))

    out: list[OutboundReply] = []
    for i, row in enumerate(ordered):
        if row["sender"] != sender:
            continue
        ts_dt = _parse_ts(row["timestamp"])
        if ts_dt is None or not (since_dt <= ts_dt <= until_dt):
            continue
        question = ""
        for prior in reversed(ordered[:i]):
            if prior["thread_id"] == row["thread_id"] and prior["sender"] != sender:
                question = prior["content"]
                break
        thread = row["thread_id"] or row["id"]
        out.append(OutboundReply(
            channel="skchat", subject_ref=f"skchat:{thread}:{row['id']}",
            ts=ts_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            question=question, reply=row["content"],
            link=WatchdogLink(uri=f"skworld://skchat/thread/{thread}", http=""),
        ))
        if len(out) >= MAX_GRADED_PER_CHANNEL:
            break
    return out


# ------------------------------------------------------------- telegram ----

def _load_telegram_rows(chat: str, limit: int) -> list[dict]:
    """Real read boundary (see module docstring): same subprocess + binary
    as `skos.adapters.telegram.TelegramAdapter.poll`, but this parser also
    keeps the Sender column that adapter discards (GTD capture only needs
    the text). Raises on any subprocess failure so `_gather_replies` can
    degrade JUST the telegram channel to one gap event. Monkeypatched
    wholesale in tests."""
    cmd = [SKCAP_BIN, "telegram", "poll", chat, "--limit", str(limit)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=True)
    return _parse_telegram_table(r.stdout)


def _parse_telegram_table(text: str) -> list[dict]:
    """Tolerant parse of `skcapstone telegram poll`'s rich-table output:
    rows shaped `['', ID, Date, Sender, Text, '']` (mirrors
    `skos.adapters.telegram._parse_poll`'s tolerance for continuation
    lines, extended to also keep the Sender column that parser discards).
    A row with no numeric ID column is a continuation of the PRIOR row's
    text, same rule as that parser."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if "│" not in line:  # box-drawing vertical bar used by the rich table
            continue
        cols = [c.strip() for c in line.split("│")]
        if len(cols) < 6:
            continue
        idcol, sender, txtcol = cols[1], cols[3], cols[-2]
        if idcol.isdigit():
            rows.append([idcol, sender, txtcol])
        elif rows and (txtcol or not idcol):
            rows[-1][2] = (rows[-1][2] + " " + txtcol).strip()
    return [{"msg_id": r[0], "sender": r[1], "text": r[2]} for r in rows if r[0]]


def _telegram_replies(window: Window, *, chat: Optional[str] = None,
                       sender_name: str = LUMINA_TG_SENDER,
                       limit: int = 200) -> list[OutboundReply]:
    chat = chat if chat is not None else _resolve("GTD_TG_CHAT", "")
    if not chat:
        return []
    rows = _load_telegram_rows(chat, limit)

    out: list[OutboundReply] = []
    for i, row in enumerate(rows):
        if row["sender"] != sender_name:
            continue
        question = ""
        for prior in reversed(rows[:i]):
            if prior["sender"] != sender_name:
                question = prior["text"]
                break
        out.append(OutboundReply(
            channel="telegram", subject_ref=f"telegram:{chat}:{row['msg_id']}",
            # No reliable per-row timestamp survives the rich-table parse
            # (the existing GTD parser drops it too); every telegram-graded
            # event is stamped at the window's own end, same treatment the
            # itil/atlas adapters give standing items that lack a precise ts.
            ts=window.until, question=question, reply=row["text"],
            link=WatchdogLink(uri=f"skworld://skcomms/telegram/{chat}/{row['msg_id']}", http=""),
        ))
        if len(out) >= MAX_GRADED_PER_CHANNEL:
            break
    return out


def _gather_replies(window: Window) -> tuple[list[OutboundReply], list[WatchdogEvent]]:
    """Read both channels. Each is independently fail-safe: a failure in one
    becomes a synthetic gap event for JUST that channel and never blocks the
    other (module docstring)."""
    replies: list[OutboundReply] = []
    gaps: list[WatchdogEvent] = []
    try:
        replies.extend(_skchat_replies(window))
    except Exception as exc:  # noqa: BLE001 - deliberate, see module docstring
        gaps.append(source_unavailable("grading.skchat", ts=now_iso(), error=str(exc)))
    try:
        replies.extend(_telegram_replies(window))
    except Exception as exc:  # noqa: BLE001 - deliberate, see module docstring
        gaps.append(source_unavailable("grading.telegram", ts=now_iso(), error=str(exc)))
    return replies, gaps


@registry.register
class GradingAdapter(WatchdogSourceAdapter):
    name = "grading"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        # A broken/missing rubric fails the WHOLE source (there is no
        # partial rubric to grade against); RubricError propagates to
        # collect_safe, unlike the two per-channel reads below.
        rubric = load_rubric(RUBRIC_ID, rubrics_dir=RUBRICS_DIR)

        replies, gap_events = _gather_replies(window)
        date = window.until[:10]
        out: list[WatchdogEvent] = list(gap_events)

        skipped = 0
        budget_exhausted = False
        deadline = time.monotonic() + GRADE_RUN_BUDGET_S

        for item in replies:
            if time.monotonic() >= deadline:
                skipped += 1
                budget_exhausted = True
                continue
            result = grader_mod.grade_one(
                rubric, item.subject_ref, question=item.question, reply=item.reply,
                timeout=PER_CALL_TIMEOUT_S)
            if not result.graded:
                skipped += 1
                continue
            out.append(self._event_for(item, result, date))

        if skipped:
            reason = ("this run's time budget ran out" if budget_exhausted else
                       "the grader was unreachable or a reply did not parse")
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="GradingGap",
                object=rubric.id, severity="notable",
                summary=(f"{skipped} reply grade(s) skipped this run "
                         f"({reason}); no score was fabricated."),
                link=WatchdogLink(uri="skworld://skos/watchdog/grading/gap", http=""),
                ref=f"grading:gap:{date}",
                meta={"skipped": skipped, "rubric_ref": rubric.rubric_ref,
                      "budget_exhausted": budget_exhausted},
            ))

        return out

    def _event_for(self, item: OutboundReply, result: "grader_mod.GradeResult",
                    date: str) -> WatchdogEvent:
        severity = "info" if result.verdict == "pass" else "notable"
        low_dims = sorted(k for k, v in result.scores.items() if v <= 2)
        low_note = f" ({', '.join(low_dims)} low)" if low_dims else ""
        summary = (f"{item.channel} reply graded {result.overall}/5 against "
                   f"{result.rubric_ref} [{result.verdict}]{low_note}.")
        return WatchdogEvent(
            ts=item.ts, source=self.name, kind="ReplyGraded",
            object=item.subject_ref, severity=severity, summary=summary,
            link=item.link, ref=f"grading:{item.subject_ref}:{date}",
            meta={"rubric_ref": result.rubric_ref, "scores": result.scores,
                  "overall": result.overall, "verdict": result.verdict,
                  "notes": result.notes, "channel": item.channel},
        )


__all__ = [
    "GradingAdapter", "OutboundReply", "RUBRIC_ID",
    "MAX_GRADED_PER_CHANNEL", "GRADE_RUN_BUDGET_S", "PER_CALL_TIMEOUT_S",
]
