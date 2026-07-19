"""gtd_triage — Agent triage MVP (card eccd3f85, [skos-ingest][S3]).

Drains raw captured items (emails, .ics invites, free text) and classifies
them into GTD buckets, writing through the ONE unified gtd_ingest sink.

Pipeline per item:
  1. Deterministic parsers first (icalendar VEVENT, RFC-822 email headers).
     A deterministic hit is confidence 1.0 and always auto-filed.
  2. Otherwise an LLM pass via skgateway (OpenAI-compat /v1, json_schema
     response_format). Endpoint/model come from env with the standard
     defaults — NEVER a hardcoded concrete model:
       SKGATEWAY_URL   (default http://localhost:18780/v1)
       SKGATEWAY_MODEL (default sk-default, auto-router)
  3. Autonomy gate: auto-file only when confidence >= AUTO_CONFIDENCE
     (0.80). Below the gate (or on any LLM failure) the item lands in
     @inbox with meta.triage_pending=True — nothing is dropped, and every
     auto decision is reversible (full decision recorded in meta.triage).

Reference-class items are routed to skingest by marking
meta.route="skingest" (the skingest drain cron picks those up); triage
itself never writes into skingest directly.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from email import policy
from email.parser import Parser

from .gtd_ingest import GtdCapture, upsert

SKGATEWAY_URL = os.environ.get("SKGATEWAY_URL", "http://localhost:18780/v1")
SKGATEWAY_MODEL = os.environ.get("SKGATEWAY_MODEL", "sk-default")  # auto-router

AUTO_CONFIDENCE = 0.80

# GTD buckets the triage may emit. Maps to gtd_ingest status values.
KINDS = ("action", "waiting", "calendar", "reference", "someday", "trash")
_KIND_TO_STATUS = {
    "action": "next",
    "waiting": "waiting",
    "calendar": "reference",   # calendar facts are reference; gog owns the calendar
    "reference": "reference",
    "someday": "someday",
    "trash": "reference",      # never hard-delete on auto; archive-equivalent
}

TRIAGE_SCHEMA = {
    "name": "gtd_triage",
    "schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "summary": {"type": "string"},
        },
        "required": ["kind", "confidence", "summary"],
        "additionalProperties": False,
    },
}


@dataclass
class TriageResult:
    """One triage decision, ready to file through gtd_ingest."""
    kind: str                     # one of KINDS
    text: str                     # normalized one-line item text
    confidence: float
    source: str                   # email | calendar | manual | ...
    source_ref: str               # stable dedup key
    auto: bool = False            # True = filed without human review
    via: str = "deterministic"    # deterministic | llm | fallback
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------- ics parser

_ICS_UNFOLD = re.compile(r"\r?\n[ \t]")
_VEVENT = re.compile(r"BEGIN:VEVENT(.*?)END:VEVENT", re.S)


def _ics_prop(block: str, name: str) -> str | None:
    """First value of ``name`` in an unfolded VEVENT block (params ignored)."""
    m = re.search(rf"^{name}[^:\n]*:(.+)$", block, re.M | re.I)
    return m.group(1).strip() if m else None


def parse_ics(text: str) -> list[dict]:
    """Deterministic, stdlib-only VEVENT extraction (uid/summary/dtstart/
    dtend/location/organizer). Returns [] when no VEVENT is present."""
    events = []
    for m in _VEVENT.finditer(_ICS_UNFOLD.sub("", text)):
        block = m.group(1)
        ev = {
            "uid": _ics_prop(block, "UID"),
            "summary": _ics_prop(block, "SUMMARY"),
            "dtstart": _ics_prop(block, "DTSTART"),
            "dtend": _ics_prop(block, "DTEND"),
            "location": _ics_prop(block, "LOCATION"),
            "organizer": _ics_prop(block, "ORGANIZER"),
        }
        if ev["summary"] or ev["dtstart"]:
            events.append(ev)
    return events


# -------------------------------------------------------------- email parser

def parse_email(raw: str) -> dict | None:
    """Deterministic RFC-822 header extraction. Returns None if the text has
    no recognizable email headers."""
    msg = Parser(policy=policy.default).parsestr(raw)
    if not msg.get("Subject") and not msg.get("From"):
        return None
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is not None:
            body = (part.get_content() or "").strip()
    except Exception:
        body = ""
    return {
        "subject": (msg.get("Subject") or "").strip(),
        "from": (msg.get("From") or "").strip(),
        "message_id": (msg.get("Message-ID") or "").strip(),
        "date": (msg.get("Date") or "").strip(),
        "body": body[:2000],
    }


# ----------------------------------------------------------------- llm layer

def _chat_json(prompt: str, *, timeout: int = 60) -> dict | None:
    """One json_schema-constrained chat call to skgateway. Returns the parsed
    object or None on ANY failure (never raises, never guesses)."""
    payload = json.dumps({
        "model": SKGATEWAY_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,  # thinking models need headroom (ornith gotcha)
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": TRIAGE_SCHEMA},
    })
    try:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", str(timeout),
             f"{SKGATEWAY_URL}/chat/completions",
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=timeout + 10)
        content = json.loads(r.stdout)["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        m = re.search(r"\{.*\}", content, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def triage_llm(text: str) -> dict | None:
    """Classify free text into a GTD bucket via skgateway. Result dict has
    kind/confidence/summary, or None when the gateway is unavailable or the
    reply does not validate."""
    prompt = (
        "You are a GTD triage assistant. Classify this captured item into "
        f"exactly one bucket of: {', '.join(KINDS)}.\n"
        "action = a concrete next physical action for the user; "
        "waiting = user is waiting on someone/something; "
        "calendar = date/time-bound event info; "
        "reference = useful info, no action; "
        "someday = maybe later; trash = noise.\n"
        "Give a confidence in [0,1] and a one-line imperative summary.\n\n"
        f"Item:\n{text[:4000]}")
    out = _chat_json(prompt)
    if not isinstance(out, dict):
        return None
    kind = out.get("kind")
    conf = out.get("confidence")
    if kind not in KINDS or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        return None
    out["confidence"] = float(conf)
    return out


# ----------------------------------------------------------------- triage

def triage_item(text: str, *, source: str = "manual",
                source_ref: str | None = None,
                use_llm: bool = True) -> list[TriageResult]:
    """Triage one raw captured payload into 1..n TriageResults.

    Deterministic parsers win (confidence 1.0, auto). Otherwise the LLM
    classifies; only confidence >= AUTO_CONFIDENCE auto-files. Anything the
    pipeline cannot confidently place falls back to @inbox for human review.
    """
    # 1. calendar invite (.ics payload)
    events = parse_ics(text) if "BEGIN:VEVENT" in text else []
    if events:
        out = []
        for ev in events:
            ref = ev["uid"] or f"{source_ref or 'ics'}:{ev['dtstart']}"
            line = f"{ev['summary'] or 'event'} @ {ev['dtstart'] or '?'}"
            if ev["location"]:
                line += f" ({ev['location']})"
            out.append(TriageResult(
                kind="calendar", text=line, confidence=1.0, source="calendar",
                source_ref=ref, auto=True, via="deterministic",
                meta={"ics": ev}))
        return out

    # 2. raw email
    mail = parse_email(text) if re.search(r"^(From|Subject):", text, re.M) else None
    if mail:
        ref = mail["message_id"] or source_ref or f"email:{hash(mail['subject'])}"
        body = f"Email from {mail['from']}: {mail['subject']}\n{mail['body']}"
        llm = triage_llm(body) if use_llm else None
        if llm:
            auto = llm["confidence"] >= AUTO_CONFIDENCE
            return [TriageResult(
                kind=llm["kind"], text=llm["summary"] or mail["subject"],
                confidence=llm["confidence"], source="email", source_ref=ref,
                auto=auto, via="llm", meta={"email": mail, "triage": llm})]
        return [TriageResult(
            kind="action", text=f"Triage email: {mail['subject']}",
            confidence=0.0, source="email", source_ref=ref, auto=False,
            via="fallback", meta={"email": mail})]

    # 3. free text
    ref = source_ref or f"triage:{abs(hash(text)) & 0xffffffff:08x}"
    llm = triage_llm(text) if use_llm else None
    if llm:
        auto = llm["confidence"] >= AUTO_CONFIDENCE
        return [TriageResult(
            kind=llm["kind"], text=llm["summary"] or text[:200],
            confidence=llm["confidence"], source=source, source_ref=ref,
            auto=auto, via="llm", meta={"triage": llm})]
    return [TriageResult(
        kind="action", text=text[:200], confidence=0.0, source=source,
        source_ref=ref, auto=False, via="fallback", meta={})]


def file_result(r: TriageResult) -> tuple[str, str]:
    """Write one TriageResult through the unified gtd_ingest sink (upsert by
    (source, source_ref) — idempotent). Below-gate results land in @inbox
    with meta.triage_pending. Reference-class items get meta.route=skingest.
    Returns (item_id, action)."""
    meta = dict(r.meta)
    meta["triage"] = {
        "kind": r.kind, "confidence": r.confidence,
        "auto": r.auto, "via": r.via,
    }
    if r.kind in ("reference", "trash") and r.auto:
        meta["route"] = "skingest"
    status = _KIND_TO_STATUS[r.kind] if r.auto else "inbox"
    if not r.auto:
        meta["triage_pending"] = True
    return upsert(GtdCapture(
        text=r.text, source=r.source, source_ref=r.source_ref,
        context="@inbox" if not r.auto else "@triaged",
        status=status, meta=meta))


def drain(payloads: list[tuple[str, str, str | None]],
          *, use_llm: bool = True) -> list[dict]:
    """Batch drain: [(text, source, source_ref), ...] → triage + file each.
    Returns an audit list of {id, action, kind, confidence, auto, via}."""
    audit = []
    for text, source, source_ref in payloads:
        for r in triage_item(text, source=source, source_ref=source_ref,
                             use_llm=use_llm):
            item_id, action = file_result(r)
            audit.append({
                "id": item_id, "action": action, "kind": r.kind,
                "confidence": r.confidence, "auto": r.auto, "via": r.via,
                "source_ref": r.source_ref,
            })
    return audit
