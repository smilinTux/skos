"""Order / shipment → GTD **stateful** pull adapter.

Tracks deliveries the way the itil/email adapters track their sources, but
stateful: one waiting-for item per order, driven through
``ordered → shipped → out_for_delivery → delivered`` via the
:func:`skos.gtd_ingest.upsert` sink, auto-completed on delivery, with **one
Telegram ping per real state change** (nothing on a no-change poll).

Tracked orders carry no separate registry: an order is any waiting-for item with
a ``meta.order`` block (``order_id``, ``account``, ``state``, ``complete_on``).

Inference: state extraction is deterministic over the vendor's own subject
phrasing; genuine ambiguity is the only thing that would fall back to skgateway
(:18780, auto-router → ornith), and unknown always degrades to *no change*,
never a guessed transition.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from ..gtd_ingest import GtdCapture, GtdSourceAdapter, upsert, _load, _ALL_FILES

DEFAULT_STATES = ["ordered", "shipped", "out_for_delivery", "delivered"]

# Deterministic matchers over Amazon (and generic) shipment phrasing. Ordered
# most-progressed first so a single subject resolves to its furthest state.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("delivered", re.compile(r"was delivered|has been delivered|delivered:|package was left", re.I)),
    ("out_for_delivery", re.compile(r"out for delivery|arriving today|will arrive today", re.I)),
    ("shipped", re.compile(r"has shipped|shipped:|on its way|out for shipment|now preparing", re.I)),
    ("ordered", re.compile(r"order (?:confirmed|placed)|thank you for your order|has been placed", re.I)),
]


SKGATEWAY_URL = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")
SKGATEWAY_MODEL = os.environ.get("SKGATEWAY_MODEL", "sk-default")  # auto-router → ornith


def classify_llm(subjects: list[str], states: list[str]) -> str | None:
    """Ambiguity fallback: ask skgateway (auto-router → ornith) to map the subject
    lines to one of ``states``. Returns a valid state or None. Degrades to None on
    any error/timeout/invalid answer, never guesses a transition."""
    if not subjects:
        return None
    joined = "\n".join(f"- {s}" for s in subjects if s)
    prompt = (
        "You classify a package delivery's status from email subject lines.\n"
        f"Allowed states (in order): {', '.join(states)}.\n"
        "Reply with EXACTLY ONE of those state words, or 'none' if unclear. "
        "No punctuation, no explanation.\n\n"
        f"Subject lines:\n{joined}\n\nState:")
    payload = json.dumps({
        "model": SKGATEWAY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8, "temperature": 0,
    })
    try:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", "45", f"{SKGATEWAY_URL}/chat/completions",
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=50)
        ans = json.loads(r.stdout)["choices"][0]["message"]["content"].strip().lower()
    except Exception:
        return None
    ans = re.sub(r"[^a-z_ ]", "", ans).replace(" ", "_")
    return ans if ans in states else None


def classify(subjects: list[str], states: list[str], *, use_llm: bool = True) -> str | None:
    """Deterministic-first classification with an ornith fallback on ambiguity."""
    state = classify_state(subjects, states)
    if state is None and use_llm:
        state = classify_llm(subjects, states)
    return state


def _tracked_orders() -> list[dict]:
    """Every in-flight item carrying a ``meta.order`` block (archive excluded)."""
    out = []
    for fname in _ALL_FILES:
        if fname == "archive.json":
            continue
        for it in _load(fname):
            order = it.get("order")
            if isinstance(order, dict) and order.get("order_id"):
                out.append(it)
    return out


def classify_state(subjects: list[str], states: list[str]) -> str | None:
    """Furthest-along state matched across the subject lines, or None. ``states``
    defines the progression (so we never regress on a stray older email)."""
    rank = {s: i for i, s in enumerate(states)}
    best = None
    for subj in subjects:
        for state, pat in _PATTERNS:
            if state in rank and pat.search(subj or ""):
                if best is None or rank[state] > rank[best]:
                    best = state
    return best


def seed_order(order_id: str, account: str, *, vendor: str = "amazon",
               eta: str | None = None, text: str | None = None,
               states: list[str] | None = None,
               notify_tier: str = "normal") -> tuple[str | None, str]:
    """Idempotently seed a tracked order as a waiting-for item with a ``meta.order``
    block. Returns ``(item_id, action)`` with action in ``{created, exists}``. The
    first/last of ``states`` become the initial state and ``complete_on``."""
    from ..gtd_ingest import GtdCapture, capture, _find_item

    ref = f"{vendor}:{order_id}"
    _f, _i, existing, _items = _find_item("order", ref)
    if existing:
        return existing["id"], "exists"

    states = states or DEFAULT_STATES
    label = text or f"order {order_id}"
    iid = capture(GtdCapture(
        text=f"{label} - {states[0].replace('_', ' ')}",
        source="order", source_ref=ref, status="waiting",
        context="@errand", priority="low",
        meta={"order": {
            "vendor": vendor, "order_id": order_id, "account": account,
            "states": states, "state": states[0], "eta": eta,
            "complete_on": states[-1], "notify_tier": notify_tier,
        }}))
    return iid, "created"


class OrderAdapter(GtdSourceAdapter):
    name = "order"

    # NB: poll() returns (item, capture, new_state, terminal) tuples for the
    # stateful drain() below, richer than the base list[GtdCapture] because the
    # notify decision needs the transition, not just the written id.
    def poll(self):  # type: ignore[override]
        from .. import mail

        observations = []
        for it in _tracked_orders():
            o = it["order"]
            states = o.get("states") or DEFAULT_STATES
            oid = o["order_id"]
            account = o.get("account")

            subjects: list[str] = []
            if account:
                try:
                    for q in (f"from:amazon.com {oid}",
                              f"{oid}"):
                        for t in mail.list_threads(account, q, maxn=20):
                            subjects.append(t.get("subject", ""))
                except Exception:
                    subjects = []

            new_state = classify(subjects, states) or o.get("state")
            if new_state == o.get("state"):
                continue  # no change → build nothing (quiet by construction)

            terminal = new_state == o.get("complete_on", "delivered")
            label = (it.get("text") or f"order {oid}").split(" - ")[0]
            cap = GtdCapture(
                text=f"{label} - {new_state.replace('_', ' ')}",
                source="order",
                source_ref=it["source_ref"],
                status="done" if terminal else "waiting",
                context=it.get("context", "@errand"),
                priority=it.get("priority", "low"),
                meta={"order": {**o, "state": new_state}},
            )
            observations.append((it, cap, new_state, terminal))
        return observations

    def drain(self) -> list[str]:  # type: ignore[override]
        written = []
        for _it, cap, new_state, _terminal in self.poll():
            iid, action = upsert(cap)
            if action in ("updated", "completed"):
                self._notify(cap, new_state, action)
                written.append(iid)
        return written

    def _notify(self, cap: GtdCapture, new_state: str, action: str) -> None:
        label = cap.text.split(" - ")[0]
        if action == "completed":
            msg = f"✅ Delivered: {label}. Marked done + archived."
        else:
            msg = f"\U0001f4e6 {label} - {new_state.replace('_', ' ')}."
        _tg_text(msg)


def _tg_text(msg: str) -> bool:
    """One-line Telegram DM to Chef, reusing the mail adapter's bot token/chat."""
    from .. import mail

    tok = mail._tg_token()
    if not tok:
        return False
    chat = mail.HERMES_DM.split(":")[-1]
    try:
        r = subprocess.run(
            ["curl", "-sS", "-G",
             f"https://api.telegram.org/bot{tok}/sendMessage",
             "--data-urlencode", f"chat_id={chat}",
             "--data-urlencode", f"text={msg}"],
            capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout).get("ok", False)
    except Exception:
        return False
