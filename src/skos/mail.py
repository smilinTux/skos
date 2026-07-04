#!/usr/bin/env python3
"""gtd-mail — bridge Gmail (5 accounts) into the unified skos/skcapstone GTD,
and send a daily digest to Chef's Hermes DM.

Subcommands:
  capture   Pull each account's "1 Action" / "2 Waiting" labelled threads and
            upsert them into the unified GTD store (next-actions / waiting-for)
            with source="email" (idempotent, deduped by Gmail thread id).
  triage    LLM-classify primary inbox -> GTD labels + archive (local model).
  digest    Compose the daily brief (Action + Waiting + new primary mail, across
            all boxes, sorted by what Chef cares about) and send it to Hermes DM.

  -- bidirectional (act on items; <ref> = brief E-number · GTD item id · thread id) --
  The daily digest numbers every item E1..EN and writes digest-index.json, so
  from Telegram (or any channel) Chef can just say "reply E3 …", "file E3",
  "show E3" and it resolves to the right account+thread.
  reply <ref> --body "..." [--send] [--to addr] [-a acct]
            Reply within the thread. Default = a reviewable Gmail DRAFT (safe);
            --send actually sends. Recipient defaults to the thread's sender.
  done <gtd_id>
            Mark the GTD item done AND archive+read its email thread (close loop).
  attachments <ref> [--save] [--telegram] [--to dir] [-a acct]
            List a thread's attachments; --save downloads; --telegram delivers
            each file to Chef's DM ("show attachment").

This is Phase-0 of the skos email->unified-GTD integration (see
docs/skos-email-gtd-architecture.md). It writes the SAME GTD store the ITIL
tools and manual captures use: ~/.skcapstone/coordination/gtd/*.json.
"""
from __future__ import annotations
import json, os, subprocess, sys, uuid, datetime, textwrap
from pathlib import Path

GOG = os.environ.get("GOG", "/home/linuxbrew/.linuxbrew/bin/gog")
os.environ.setdefault("GOG_KEYRING_PASSWORD", "sk2026")
GTD_DIR = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone"))) / "coordination" / "gtd"
HERMES_DM = os.environ.get("HERMES_DM", "telegram:1594678363")  # chefboyrdave2.1 aka daveK
ACCOUNTS = [
    "chefboyrdave2.1@gmail.com",
    "david.knestrick@gmail.com",
    "cbd2dot11@gmail.com",
    "jaimeanddavid2014@gmail.com",
    "dounoit@gmail.com",
]
SHORT2ACCT = {a.split("@")[0]: a for a in ACCOUNTS}

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _gog(args: list[str]) -> str:
    try:
        return subprocess.run([GOG, *args], capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return ""

def list_threads(account: str, query: str, maxn: int = 100) -> list[dict]:
    """Return [{id, from, subject, date}] for a Gmail query (single page)."""
    out = _gog(["gmail", "list", "-a", account, query, "--max", str(maxn), "-j"])
    try:
        d = json.loads(out)
    except Exception:
        return []
    rows = []
    for t in d.get("threads", []):
        rows.append({
            "id": t.get("id") or t.get("threadId"),
            "from": (t.get("from") or "").strip(),
            "subject": (t.get("subject") or t.get("snippet") or "").strip(),
            "date": (t.get("date") or t.get("internalDate") or "")[:10],
        })
    return rows

# 2026-07-03: rerouted off .100:8082 (Q3_K garble cliff) → chiap08 BeeLlama Q4_K_M (:11436). Override via GTD_LLM_URL.
LLM_URL = os.environ.get("GTD_LLM_URL", "http://100.81.238.58:11436/v1/chat/completions")
LLM_MODEL = os.environ.get("GTD_LLM_MODEL", "ornith-1.0-9b")
BUCKET2LABEL = {
    "action": "1 Action", "waiting": "2 Waiting", "read": "3 Read", "someday": "4 Someday",
    "legal": "Areas/Legal-Trust", "people": "Areas/People", "finance": "Areas/Finance",
    "health": "Areas/Health",
}
_TRIAGE_SYS = (
    "You triage Chef's (David's) email into a GTD system. For each email (sender + subject) "
    "assign EXACTLY ONE bucket:\n"
    "- action: needs Chef to personally do/reply/decide — a real ask from a person, a bill to pay, "
    "a document to submit, a task. Only if still relevant.\n"
    "- waiting: Chef is waiting on someone else's reply/delivery/support ticket.\n"
    "- read: FYI only — newsletters, marketing, receipts, order/shipping notices, verification/login "
    "codes, social, GitHub/service notifications, calendar reminders. NO action needed.\n"
    "- someday: reference or maybe-later — courses, ideas, pitches.\n"
    "- legal: trust/UCC/court/attorney/sovereignty/notary/tax legal documents.\n"
    "- people: family, kids, school (Norwalk/PowerSchool), personal relationships.\n"
    "- finance: bank statements, bills, investment/brokerage accounts.\n"
    "- health: medical, pharmacy, labs, supplements.\n"
    "When unsure and the item is clearly old or promotional, choose read. "
    'Return ONLY a JSON array of bucket strings, one per email, in order. No prose.'
)

def classify_batch(emails: list[dict]) -> list[str]:
    """Return a bucket per email via the local abliterated LLM. Falls back to 'read'."""
    listing = "\n".join(f"[{i}] from={e['from'][:50]} subject={e['subject'][:90]}"
                        for i, e in enumerate(emails))
    payload = {
        "model": LLM_MODEL, "temperature": 0, "max_tokens": 20 * len(emails) + 50,
        "messages": [{"role": "system", "content": _TRIAGE_SYS},
                     {"role": "user", "content": f"Classify these {len(emails)} emails:\n{listing}"}],
    }
    try:
        import urllib.request
        req = urllib.request.Request(LLM_URL, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            content = json.loads(r.read())["choices"][0]["message"]["content"]
        s = content[content.index("["): content.rindex("]") + 1]
        buckets = json.loads(s)
        buckets = [str(b).strip().lower() for b in buckets]
        # normalize / pad
        buckets = [b if b in BUCKET2LABEL else "read" for b in buckets]
        if len(buckets) < len(emails):
            buckets += ["read"] * (len(emails) - len(buckets))
        return buckets[:len(emails)]
    except Exception as e:
        print(f"  classify_batch fallback (read): {e}", file=sys.stderr)
        return ["read"] * len(emails)

def _labels_modify(account: str, thread_ids: list[str], label: str) -> None:
    for i in range(0, len(thread_ids), 100):
        subprocess.run([GOG, "gmail", "labels", "modify", "-a", account,
                        *thread_ids[i:i+100], "--add", label, "--remove", "INBOX", "-y"],
                       capture_output=True)

def cmd_triage(cap_per_account: int = 200) -> None:
    """Intelligently file each account's inbox (post noise-sweep) into GTD labels
    using the local LLM, then archive — so reports reflect a processed mailbox."""
    grand = {}
    for acct in ACCOUNTS:
        filed = 0
        for _ in range(cap_per_account // 40 + 1):
            rows = list_threads(acct, "in:inbox", 40)
            if not rows:
                break
            buckets = classify_batch(rows)
            by_label: dict[str, list[str]] = {}
            for r, b in zip(rows, buckets):
                if r["id"]:
                    by_label.setdefault(BUCKET2LABEL.get(b, "3 Read"), []).append(r["id"])
            for label, ids in by_label.items():
                _labels_modify(acct, ids, label)
                grand[label] = grand.get(label, 0) + len(ids)
            filed += len(rows)
            if filed >= cap_per_account:
                break
        if filed:
            print(f"  {acct.split('@')[0]}: triaged {filed}")
    print(f"gtd-mail triage: {grand}")

def _load(name: str) -> list:
    p = GTD_DIR / f"{name}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text() or "[]")
    except Exception:
        return []

def _save(name: str, items: list) -> None:
    (GTD_DIR / f"{name}.json").write_text(json.dumps(items, indent=2, ensure_ascii=False))

def _existing_email_ids() -> set[str]:
    ids = set()
    for name in ("inbox", "next-actions", "waiting-for", "someday-maybe", "archive"):
        for it in _load(name):
            tid = it.get("email_thread_id")
            if tid:
                ids.add(tid)
    return ids

def _make_email_item(account: str, row: dict, status: str, priority: str) -> dict:
    sender = row["from"][:60]
    subj = row["subject"][:120] or "(no subject)"
    return {
        "id": uuid.uuid4().hex[:12],
        "text": f"[email:{account.split('@')[0]}] {subj} — {sender}",
        "source": "email",
        "privacy": "private",
        "context": "@email",
        "priority": priority,
        "energy": None,
        "status": status,
        "created_at": _now(),
        "clarified_at": _now(),
        "email_account": account,
        "email_thread_id": row["id"],
        "email_from": sender,
        "email_subject": subj,
    }

def email_captures() -> list:
    """The email adapter's poll(): Gmail `1 Action`/`2 Waiting` threads across all
    boxes as GtdCapture objects (deduped later by the sink on the thread id)."""
    from .gtd_ingest import GtdCapture
    caps = []
    for acct in ACCOUNTS:
        for label, status, prio in (('1 Action', "next", "high"), ('2 Waiting', "waiting", "medium")):
            for row in list_threads(acct, f'label:"{label}"'):
                if not row["id"]:
                    continue
                subj = row["subject"][:120] or "(no subject)"
                sender = row["from"][:60]
                caps.append(GtdCapture(
                    text=f"[email:{acct.split('@')[0]}] {subj} — {sender}",
                    source="email", source_ref=row["id"], context="@email",
                    priority=prio, status=status,
                    meta={"email_account": acct, "email_from": sender,
                          "email_subject": subj, "email_thread_id": row["id"]}))
    return caps

def cmd_capture() -> None:
    """Emit the email captures through the skos gtd-ingest sink (source_ref-deduped)."""
    from .gtd_ingest import capture as sink
    a = w = 0
    for c in email_captures():
        if sink(c):
            if c.status == "next":
                a += 1
            else:
                w += 1
    print(f"gtd-mail capture: +{a} next-actions, +{w} waiting-for (email source)")

def cmd_digest(send: bool = True) -> str:
    """Compose + send the daily email brief to Hermes DM."""
    action, waiting, new_primary, health = [], [], [], []
    for acct in ACCOUNTS:
        short = acct.split("@")[0]
        for r in list_threads(acct, 'label:"1 Action"'):
            action.append((short, r))
        for r in list_threads(acct, 'label:"2 Waiting"'):
            waiting.append((short, r))
        # brand-new real mail that arrived in the last day and isn't triaged yet
        newp = list_threads(acct, "in:inbox newer_than:1d -category:promotions -category:social", 40)
        inbox_n = len(list_threads(acct, "label:inbox", 100))
        health.append((short, inbox_n, len(newp)))
        for r in newp[:6]:
            new_primary.append((short, r))

    d = datetime.date.today().isoformat()

    # Stable per-brief reference numbers (E1..EN) so Chef can act on any item
    # from Telegram (or any channel) by number — "reply E3", "file E3",
    # "show E3". The number→thread map is persisted to digest-index.json and
    # resolved by _resolve()/cmd_done(), so it wires reply/done/attachments.
    tid2gtd = {it.get("email_thread_id"): it.get("id")
               for _, it in _all_items() if it.get("email_thread_id")}
    index: dict[str, dict] = {}
    seq = 0
    def _ref(short: str, r: dict, bucket: str) -> str:
        nonlocal seq
        seq += 1
        key = f"E{seq}"
        index[key] = {
            "account": SHORT2ACCT.get(short, short),
            "thread_id": r["id"],
            "subject": r["subject"][:120],
            "from": r["from"][:60],
            "bucket": bucket,
            "gtd_id": tid2gtd.get(r["id"]),
        }
        return key

    # Title is carried by the Hermes --subject header on send; keep it in the
    # body only when returning the brief for standalone (non-send) use so we
    # don't print "📬 GTD Email Brief" twice in the delivered message.
    L = [] if send else [f"📬 GTD Email Brief — {d}", ""]
    L.append(f"🔴 ACTION ({len(action)}) — needs you:")
    for short, r in action[:20]:
        L.append(f"  {_ref(short, r, 'action')} [{short}] {r['subject'][:60]} — {r['from'][:26]}")
    if not action:
        L.append("  (clear)")
    L.append("")
    L.append(f"🟡 WAITING ({len(waiting)}) — on others:")
    for short, r in waiting[:15]:
        L.append(f"  {_ref(short, r, 'waiting')} [{short}] {r['subject'][:60]} — {r['from'][:26]}")
    if not waiting:
        L.append("  (clear)")
    L.append("")
    if new_primary:
        L.append(f"🆕 NEW today ({len(new_primary)} shown) — un-triaged real mail:")
        for short, r in new_primary[:15]:
            L.append(f"  {_ref(short, r, 'new')} [{short}] {r['subject'][:58]} — {r['from'][:24]}")
        L.append("")
    L.append("📊 Inbox health:")
    for short, inbox_n, newn in health:
        L.append(f"  {short:22s} inbox={inbox_n:<4d} new_today={newn}")
    L.append("")
    L.append("↩︎ Reference any item by number — “reply E3 …”, “file E3”, “show E3”.")
    body = "\n".join(L)
    # Persist the reference index so a later "act on E3" resolves to the right
    # account+thread. Written on both paths so a --no-send preview is usable too.
    _save("digest-index", {"generated": _now(), "date": d, "items": index})
    if send:
        subprocess.run(["hermes", "send", "--to", HERMES_DM, "--subject",
                        f"📬 GTD Email Brief — {d}", body],
                       capture_output=True, text=True)
    return body

# ── Bidirectional email: act on GTD items (reply · done→archive · attachments) ──
def _all_items() -> list[tuple[str, dict]]:
    out = []
    for name in ("inbox", "next-actions", "waiting-for", "someday-maybe", "projects"):
        for it in _load(name):
            out.append((name, it))
    return out

def _digest_index() -> dict:
    """Return the last brief's {E-number: {account, thread_id, ...}} map."""
    data = _load("digest-index")
    return data.get("items", {}) if isinstance(data, dict) else {}

def _brief_key(ref: str) -> str | None:
    """Normalize a user ref to a brief key: 'E3', 'e3', or bare '3' -> 'E3'."""
    r = ref.strip().upper()
    if r.startswith("E") and r[1:].isdigit():
        return r
    if r.isdigit():
        return f"E{r}"
    return None

def _resolve(ref: str, account: str | None = None) -> dict:
    """Resolve a ref (brief E-number · GTD item id · gmail thread id) -> {account, thread_id, item}."""
    # brief reference number from the daily email digest (E3 / e3 / bare 3)
    key = _brief_key(ref)
    if key:
        e = _digest_index().get(key)
        if e:
            return {"account": e["account"], "thread_id": e["thread_id"],
                    "item": None, "gtd_id": e.get("gtd_id")}
    for _, it in _all_items():
        if it.get("id") == ref or it.get("source_ref") == ref or it.get("email_thread_id") == ref:
            if it.get("email_thread_id"):
                return {"account": it.get("email_account"), "thread_id": it["email_thread_id"], "item": it}
    # treat ref as a raw thread id
    return {"account": account or ACCOUNTS[0], "thread_id": ref, "item": None}

def _thread(account: str, thread_id: str) -> dict:
    """Return {messages:[{id, from, subject, attachments:[{mid,aid,filename,mime,size}]}], last_id, subject, sender}."""
    out = subprocess.run([GOG, "gmail", "thread", "get", thread_id, "-a", account, "-j", "--full"],
                         capture_output=True, text=True, timeout=90).stdout
    try:
        th = (json.loads(out).get("thread") or {})
    except Exception:
        th = {}
    msgs = th.get("messages", [])
    res, sender, subject = [], "", ""
    def _hdr(headers, name):
        return next((h.get("value", "") for h in headers if h.get("name", "").lower() == name.lower()), "")
    def _walk(part, acc):
        body = part.get("body", {})
        if body.get("attachmentId") and part.get("filename"):
            acc.append({"aid": body["attachmentId"], "filename": part["filename"],
                        "mime": part.get("mimeType", ""), "size": body.get("size", 0)})
        for p in part.get("parts", []) or []:
            _walk(p, acc)
    for m in msgs:
        headers = m.get("payload", {}).get("headers", [])
        atts: list = []
        _walk(m.get("payload", {}), atts)
        frm, subj = _hdr(headers, "From"), _hdr(headers, "Subject")
        res.append({"id": m["id"], "from": frm, "subject": subj, "attachments": atts})
        sender = sender or frm
        subject = subject or subj
    return {"messages": res, "last_id": res[-1]["id"] if res else None,
            "subject": subject, "sender": sender}

def _sender_addr(frm: str) -> str:
    import re
    m = re.search(r"<([^>]+)>", frm)
    return m.group(1) if m else frm.strip()

def cmd_reply(ref: str, body: str, send: bool = False, account: str | None = None, to: str | None = None) -> None:
    r = _resolve(ref, account); acct, tid = r["account"], r["thread_id"]
    if not acct or not tid:
        print("reply: could not resolve account/thread", file=sys.stderr); return
    th = _thread(acct, tid)
    subj = th["subject"] or "(no subject)"
    if not subj.lower().startswith("re:"):
        subj = "Re: " + subj
    recipient = to or _sender_addr(th["sender"])
    common = ["-a", acct, "--to", recipient, "--subject", subj, "--body", body]
    if th["last_id"]:
        common += ["--reply-to-message-id", th["last_id"]]
    if send:
        args = [GOG, "gmail", "send", "--thread-id", tid] + common
        r2 = subprocess.run(args, capture_output=True, text=True, timeout=60)
        print(f"reply SENT to {recipient} on thread {tid}: {r2.returncode==0}")
    else:  # default: safe reviewable DRAFT
        args = [GOG, "gmail", "drafts", "create"] + common
        r2 = subprocess.run(args, capture_output=True, text=True, timeout=60)
        print(f"reply DRAFTED to {recipient} (review+send in Gmail): rc={r2.returncode}\n{(r2.stdout or r2.stderr)[:200]}")

def cmd_done(gtd_id: str) -> None:
    """Mark the GTD item done AND archive+read its email thread (bidirectional close).

    Accepts a brief E-number (E3) as well as a raw GTD item id. If the E-item
    was captured into the GTD store, it closes that; otherwise (un-triaged
    "NEW" mail) it just archives+marks-read the email thread directly.
    """
    key = _brief_key(gtd_id)
    if key:
        e = _digest_index().get(key)
        if e and e.get("gtd_id"):
            gtd_id = e["gtd_id"]  # fall through to normal GTD close
        elif e:
            acct, tid = e["account"], e["thread_id"]
            mids = [m["id"] for m in _thread(acct, tid)["messages"]] or [tid]
            subprocess.run([GOG, "gmail", "archive", "-a", acct, *mids], capture_output=True)
            subprocess.run([GOG, "gmail", "mark-read", "-a", acct, *mids], capture_output=True)
            print(f"done: {key} archived+read email thread {tid} ({len(mids)} msgs) — no GTD item to close")
            return
    hit = None
    for name in ("inbox", "next-actions", "waiting-for", "someday-maybe", "projects"):
        items = _load(name)
        for idx, it in enumerate(items):
            if it.get("id") == gtd_id:
                hit = (name, idx, it, items); break
        if hit:
            break
    if not hit:
        print(f"done: no GTD item {gtd_id}", file=sys.stderr); return
    name, idx, it, items = hit
    # archive + mark-read the email thread if this is an email item
    if it.get("email_thread_id") and it.get("email_account"):
        acct, tid = it["email_account"], it["email_thread_id"]
        mids = [m["id"] for m in _thread(acct, tid)["messages"]] or [tid]
        subprocess.run([GOG, "gmail", "archive", "-a", acct, *mids], capture_output=True)
        subprocess.run([GOG, "gmail", "mark-read", "-a", acct, *mids], capture_output=True)
        print(f"  archived+read email thread {tid} ({len(mids)} msgs)")
    # move GTD item -> archive.json with completed_at
    items.pop(idx); _save(name, items)
    from datetime import datetime, timezone
    it["status"] = "done"; it["completed_at"] = datetime.now(timezone.utc).isoformat()
    arch = _load("archive"); arch.append(it); _save("archive", arch)
    print(f"done: {gtd_id} ({it.get('text','')[:50]}) -> archived")

def _tg_token() -> str | None:
    env = Path.home() / ".hermes" / ".env"
    if env.exists():
        for ln in env.read_text().splitlines():
            if ln.startswith("TELEGRAM_BOT_TOKEN"):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return None

def _tg_send_file(path: Path, caption: str = "") -> bool:
    tok = _tg_token()
    chat = HERMES_DM.split(":")[-1]
    if not tok:
        return False
    r = subprocess.run(["curl", "-sS", "-F", f"chat_id={chat}", "-F", f"document=@{path}",
                        "-F", f"caption={caption[:900]}",
                        f"https://api.telegram.org/bot{tok}/sendDocument"],
                       capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout).get("ok", False)
    except Exception:
        return False

def cmd_attachments(ref: str, save: bool = False, to_dir: str | None = None,
                    account: str | None = None, telegram: bool = False) -> None:
    r = _resolve(ref, account); acct, tid = r["account"], r["thread_id"]
    th = _thread(acct, tid)
    found = [(m["id"], a) for m in th["messages"] for a in m["attachments"]]
    if not found:
        print("attachments: none on this thread"); return
    outdir = Path(to_dir or f"/tmp/gtd-attach/{tid}"); outdir.mkdir(parents=True, exist_ok=True)
    seen_files = set()
    for mid, a in found:
        line = f"  {a['filename']}  ({a['mime']}, {a['size']}B)"
        if save or telegram:
            dest = outdir / a["filename"]
            subprocess.run([GOG, "gmail", "attachment", mid, a["aid"], "-a", acct, "--out", str(dest)],
                           capture_output=True, timeout=90)
            line += f"  -> {dest}"
            if telegram and a["filename"] not in seen_files:
                ok = _tg_send_file(dest, caption=f"📎 {a['filename']} (from: {th['sender'][:40]})")
                line += f"  [tg:{'sent' if ok else 'fail'}]"
                seen_files.add(a["filename"])
        print(line)
    if save or telegram:
        print(f"SAVED to {outdir}")

def main():
    GTD_DIR.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "digest"
    def _opt(flag, default=None):
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

    if cmd == "triage":
        cmd_triage()
    elif cmd == "capture":
        cmd_capture()
    elif cmd == "reply":
        # gtd-mail reply <ref> --body "..." [--send] [-a acct] [--to addr]
        cmd_reply(sys.argv[2], _opt("--body", ""), send="--send" in sys.argv,
                  account=_opt("-a"), to=_opt("--to"))
    elif cmd == "done":
        cmd_done(sys.argv[2])
    elif cmd == "attachments":
        cmd_attachments(sys.argv[2], save="--save" in sys.argv, to_dir=_opt("--to"),
                        account=_opt("-a"), telegram="--telegram" in sys.argv)
    elif cmd == "digest":
        print(cmd_digest(send="--no-send" not in sys.argv))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    main()
