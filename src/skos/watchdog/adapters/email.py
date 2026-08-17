"""email: read-only narration of the 4-C Gmail lanes via gog (WD-6, Phase 2).

Spec: docs/specs/2026-08-10-skwatchdog-architecture.md, section 6.5 ("Phase 2
adds ... `email` (gog, 4-C labels)") and section 13 ("the collector holds no
credentials beyond what skos adapters already resolve, gog keyring via
secret-env; nothing new").

THE RULE THIS MODULE EXISTS TO HOLD
-----------------------------------
SUMMARIES AND REFS ONLY. The digest is a published artifact: written to disk
and served over HTTP by skcode-hostd at ``GET /api/v1/watchdog/digest``
(capauth-gated, scope ``skcode.stream``). An email body copied into it is a
copy of Chef's private mail sitting in a served file.

Held structurally, not by care: ``_search_thread_ids`` is the only place gog
output becomes data in this module, and it returns a list of THREAD IDS.
Subjects, senders, snippets and bodies are discarded at that boundary, so no
later edit here can leak one into a published line, because none is in scope.

THE SUBJECT-LINE JUDGEMENT CALL
-------------------------------
The card leaves subjects to judgement. This adapter takes the strict side:
NO subject lines, and no sender addresses either. Two reasons.

1. Grain. These lines roll up across boxes: "4 new in 1 Action" is the useful
   sentence, exactly the card's own example. Four subject lines in its place
   would be four lines of noise in a six-sentence narrative, and the deep
   link is one click from the real thing.
2. Publication. A subject is the sender's prose about a private matter, and
   this file is served, cached and re-read. A count is never sensitive; a
   subject sometimes is, and there is no way to tell which is which without
   reading it. When the useful version and the safe version are this close
   together, take the safe one.

So an email line carries: a count, a label, how many boxes it spans, and a
Gmail deep link to that label in the box with the most of them. Thread ids
(opaque handles, not content) ride in ``meta`` so the Code section's Digest
tab can offer per-thread links later without re-collecting anything.

Severity discipline
-------------------
Mail is the highest-volume source in the whole digest, and a ``problem``
files a GTD item (WD-8) and can escalate to a coord card (WD-9).

  info      everything, ordinarily: new mail in `2 Waiting` / `3 Read` /
            `4 Someday`, and backlog counts under the notable threshold.
  notable   new mail in `1 Action` (that lane is, by the 4-C convention's own
            definition, work that needs Chef personally), and an active lane
            whose backlog has crossed ``SKWATCHDOG_EMAIL_BACKLOG_NOTABLE``.
  problem   NOTHING here ever earns it. Unread mail is not a fault; a full
            `1 Action` lane is a workload, not an incident. The only email
            that could justify tracked work is one whose CONTENT is urgent,
            and judging that means reading bodies, which this card forbids
            outright. A mailbox that cannot be read at all is still only
            ``notable`` (``events.source_unavailable``).

Fail-safe, and the false-all-clear trap
---------------------------------------
``skos.mail.list_threads`` is deliberately NOT reused for the read, despite
being the existing gog path, because it swallows every failure and returns
``[]``. Through that function an expired gog token is indistinguishable from
an empty mailbox, which would publish a calm "0 new" line while the mailbox
was never actually read: a textbook false all-clear. ``_search_thread_ids``
raises instead, so a broken box becomes a visible line. Per box: one gap
event, and the other boxes still report. If EVERY box fails, the exception
propagates so ``collect_safe`` marks the whole source not-ok in
``per_source`` rather than reporting a healthy source that read nothing.
``EMAIL_RUN_BUDGET_S`` caps the whole run, so a hung gog cannot delay the
digest; boxes skipped by the budget are reported, never silently dropped.

Read-only, absolutely: every gog invocation carries ``--readonly`` (gogcli's
own runtime block on mutating API requests) and ``--no-input`` (never prompt,
fail instead, since this runs from cron). Nothing here labels, archives,
marks read, drafts, or sends.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from typing import Optional

from ..events import WatchdogEvent, WatchdogLink, source_unavailable
from ..port import Window, WatchdogSourceAdapter, now_iso, registry

#: The 4-C cadence labels, in Chef's own order. Mirrors
#: ``skos.mail.EMAIL_LABELS``; `active` marks the two lanes that also get a
#: standing backlog line (the two that stay in the inbox and represent work).
LABELS: tuple[tuple[str, bool], ...] = (
    ("1 Action", True),
    ("2 Waiting", True),
    ("3 Read", False),
    ("4 Someday", False),
)

#: Backlog size at which an active lane stops being routine and becomes worth
#: a human's eye. Never a `problem`, at any size (see severity discipline).
BACKLOG_NOTABLE = int(os.environ.get("SKWATCHDOG_EMAIL_BACKLOG_NOTABLE", "25"))

#: Per box, per label search cap. A count that hits the cap is reported as
#: "N+", never as a precise number the read cannot support.
MAX_PER_LABEL = int(os.environ.get("SKWATCHDOG_EMAIL_MAX_PER_LABEL", "100"))

#: How many thread ids ride along in meta. Handles for a future per-thread
#: link, capped so one busy day cannot bloat the published artifact.
THREAD_IDS_IN_META = 20

#: Total wall-clock budget across every gog call in one run. The digest must
#: never be delayed by mail; boxes past the budget are reported as skipped.
EMAIL_RUN_BUDGET_S = float(os.environ.get("SKWATCHDOG_EMAIL_BUDGET_S", "120"))

#: Per-call timeout. Several calls happen per box, so each gets a tighter
#: bound than a one-off invocation would.
PER_CALL_TIMEOUT_S = 45.0


class EmailReadError(RuntimeError):
    """A box could not be read. Never confused with "the box was empty"."""


def _accounts() -> list[str]:
    """The operator's Gmail boxes, resolved the way every other skos gog
    caller resolves them (``GTD_MAIL_ACCOUNTS`` via ``skos.secret_env``), so
    no address is hardcoded in this public repo."""
    from ...secret_env import accounts
    return accounts()


def _gog_bin() -> str:
    """Resolved, never assumed: see ``skos.gogbin`` for why a hardcoded
    Homebrew path was wrong three separate ways."""
    from ... import gogbin
    return gogbin.find_gog() or "gog"


def _epoch(ts: str) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError, TypeError):
        return None


def _search_thread_ids(account: str, query: str, maxn: int) -> list[str]:
    """The ONLY place gog output becomes data here, and it yields THREAD IDS
    ONLY: subject, sender, snippet and body are dropped at this boundary
    (module docstring), so none of them exists downstream to leak.

    The query is POSITIONAL on ``gog gmail search`` (there is no ``-q``
    flag). ``GOG_KEYRING_PASSWORD`` is made available to the child the same
    way ``skos.mail`` does it, from the env or the gitignored operator env
    file, never from this repo.

    Raises ``EmailReadError`` on any failure, so an unreadable box can never
    be mistaken for an empty one. Monkeypatched wholesale in tests; nothing
    else in this module runs gog.
    """
    from ...secret_env import ensure
    ensure("GOG_KEYRING_PASSWORD")
    cmd = [_gog_bin(), "gmail", "search", query, "-a", account,
           "--max", str(maxn), "--readonly", "--no-input", "-j"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=PER_CALL_TIMEOUT_S, check=True)
    except Exception as exc:  # noqa: BLE001 - re-raised as EmailReadError below
        raise EmailReadError(f"gog search failed for {account}: {exc}") from exc
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise EmailReadError(f"gog returned unparseable JSON for {account}") from exc
    ids = []
    for t in (data.get("threads") or []):
        tid = t.get("id") or t.get("threadId")
        if tid:
            ids.append(str(tid))
    return ids


def _label_query(label: str, since_epoch: Optional[int] = None) -> str:
    q = f'label:"{label}"'
    if since_epoch is not None:
        q += f" after:{since_epoch}"
    return q


def _slug(label: str) -> str:
    return label.lower().replace(" ", "-")


def _gmail_label_url(account: str, label: str) -> str:
    """Gmail's own permalink for a label in a specific box (spec section 8:
    "Gmail permalink via the gog account"). Spaces are ``+`` in Gmail's
    fragment syntax."""
    return f"https://mail.google.com/mail/u/{account}/#label/{label.replace(' ', '+')}"


@registry.register
class EmailAdapter(WatchdogSourceAdapter):
    name = "email"

    def collect(self, window: Window) -> list[WatchdogEvent]:
        accounts = _accounts()
        if not accounts:
            return []

        since_epoch = _epoch(window.since)
        date = window.until[:10]
        deadline = time.monotonic() + EMAIL_RUN_BUDGET_S

        # label -> {account: [thread ids]} for arrivals, and label ->
        # {account: count} for standing backlog. Counts only; see docstring.
        new_by_label: dict[str, dict[str, list[str]]] = {l: {} for l, _ in LABELS}
        backlog_by_label: dict[str, dict[str, int]] = {
            l: {} for l, active in LABELS if active}

        out: list[WatchdogEvent] = []
        failures = 0
        skipped: list[str] = []
        last_error = ""

        for account in accounts:
            if time.monotonic() >= deadline:
                skipped.append(account)
                continue
            try:
                for label, active in LABELS:
                    new_by_label[label][account] = _search_thread_ids(
                        account, _label_query(label, since_epoch), MAX_PER_LABEL)
                    if active:
                        backlog_by_label[label][account] = len(_search_thread_ids(
                            account, _label_query(label), MAX_PER_LABEL))
            except EmailReadError as exc:
                failures += 1
                last_error = str(exc)
                # Partial reads for this box are dropped rather than reported:
                # a half-read box would understate its own counts, and an
                # understated count is the quiet lie this adapter refuses.
                for label, _ in LABELS:
                    new_by_label[label].pop(account, None)
                    backlog_by_label.get(label, {}).pop(account, None)
                out.append(source_unavailable(f"{self.name}:{account.split('@')[0]}",
                                              ts=now_iso(), error=str(exc)))

        if failures and failures == len(accounts):
            # Every box failed. Never publish a calm "nothing new" for a
            # mailbox nobody actually read (module docstring).
            raise EmailReadError(f"every configured mailbox failed to read ({last_error})")

        for label, _active in LABELS:
            ev = self._new_mail_event(label, new_by_label[label], window, date)
            if ev is not None:
                out.append(ev)
        for label in backlog_by_label:
            # No box read means no backlog claim to make. A "0 open" line
            # drawn from zero successful reads would be the false all-clear
            # this adapter exists to avoid.
            if backlog_by_label[label]:
                out.append(self._backlog_event(label, backlog_by_label[label], window, date))

        if skipped:
            out.append(WatchdogEvent(
                ts=window.until, source=self.name, kind="MailReadBudgetSpent",
                object="email", severity="notable",
                summary=(f"{len(skipped)} mailbox(es) were not read this run: the "
                         f"run's time budget was spent. No count below covers them."),
                link=WatchdogLink(uri="skworld://skos/watchdog/email", http=""),
                ref=f"{self.name}:budget-spent:{date}",
                meta={"skipped_boxes": len(skipped)},
            ))
        return out

    # -- event builders. Every summary is built from counts, a label name and
    # -- a box count; no subject, sender or body is in scope to put in one.

    def _new_mail_event(self, label: str, by_account: dict[str, list[str]],
                         window: Window, date: str) -> Optional[WatchdogEvent]:
        total = sum(len(v) for v in by_account.values())
        if not total:
            return None
        boxes = [a for a, v in by_account.items() if v]
        busiest = max(boxes, key=lambda a: len(by_account[a]))
        capped = any(len(v) >= MAX_PER_LABEL for v in by_account.values())
        count = f"{total}+" if capped else str(total)
        where = "1 box" if len(boxes) == 1 else f"{len(boxes)} boxes"
        thread_ids = [t for a in boxes for t in by_account[a]][:THREAD_IDS_IN_META]
        return WatchdogEvent(
            ts=window.until, source=self.name, kind="NewMailInLabel", object=label,
            severity="notable" if label == "1 Action" else "info",
            summary=f"{count} new in {label} across {where}.",
            link=WatchdogLink(uri=f"skworld://skos/watchdog/email/{_slug(label)}",
                              http=_gmail_label_url(busiest, label)),
            ref=f"{self.name}:new:{_slug(label)}:{date}",
            meta={"label": label, "new": total, "capped": capped,
                  "by_account": {a.split("@")[0]: len(v) for a, v in by_account.items()},
                  "thread_ids": thread_ids},
        )

    def _backlog_event(self, label: str, by_account: dict[str, int],
                        window: Window, date: str) -> WatchdogEvent:
        """Emitted on every run that read at least one box, including when
        the count is zero, so "nothing is piling up" is a visible statement
        rather than an absence (the same reasoning behind the scheduler
        adapter's SchedulerHealthy line)."""
        total = sum(by_account.values())
        capped = any(v >= MAX_PER_LABEL for v in by_account.values())
        count = f"{total}+" if capped else str(total)
        busiest = (max(by_account, key=lambda a: by_account[a])
                   if by_account else "")
        return WatchdogEvent(
            ts=window.until, source=self.name, kind="MailBacklog", object=label,
            severity="notable" if total >= BACKLOG_NOTABLE else "info",
            summary=f"{count} open in {label} across {len(by_account)} box(es).",
            link=WatchdogLink(
                uri=f"skworld://skos/watchdog/email/{_slug(label)}",
                http=_gmail_label_url(busiest, label) if busiest else ""),
            ref=f"{self.name}:backlog:{_slug(label)}:{date}",
            meta={"label": label, "open": total, "capped": capped,
                  "threshold": BACKLOG_NOTABLE,
                  "by_account": {a.split("@")[0]: v for a, v in by_account.items()}},
        )


__all__ = ["EmailAdapter", "EmailReadError", "LABELS", "BACKLOG_NOTABLE",
           "MAX_PER_LABEL", "EMAIL_RUN_BUDGET_S"]
