#!/usr/bin/env python3
"""skos.status — realtime skos status (backs `skos status` and the sk-status shim) across email, cron/scheduled work, and GTD.

  sk-status              # everything, human-readable
  sk-status email        # per-box inbox / action / waiting counts (live gog)
  sk-status cron         # last-24h run-ledger: every job's ok/fail/duration
  sk-status gtd          # unified GTD counts across all lists
  sk-status all --json   # machine-readable
  sk-status report       # compose the daily OPS report and send to Hermes DM

Phase-0 of the skos gtd-ingest framework (docs/gtd-ingest-architecture.md).
Intended to fold into `skos status` in Phase-1.
"""
from __future__ import annotations
import json, os, subprocess, sys, datetime
from pathlib import Path

GOG = os.environ.get("GOG", "/home/linuxbrew/.linuxbrew/bin/gog")
os.environ.setdefault("GOG_KEYRING_PASSWORD", "sk2026")
HOME = Path(os.environ.get("SKCAPSTONE_HOME", str(Path.home() / ".skcapstone")))
GTD_DIR = HOME / "coordination" / "gtd"
LEDGER = HOME / "logs" / "cron-ledger.jsonl"
HERMES_DM = os.environ.get("HERMES_DM", "telegram:1594678363")
ACCOUNTS = ["chefboyrdave2.1@gmail.com", "david.knestrick@gmail.com",
            "cbd2dot11@gmail.com", "jaimeanddavid2014@gmail.com", "dounoit@gmail.com"]

def _count(account: str, query: str) -> int:
    try:
        out = subprocess.run([GOG, "gmail", "list", "-a", account, query, "--max", "100", "-j"],
                             capture_output=True, text=True, timeout=60).stdout
        d = json.loads(out); n = len(d.get("threads", []))
        return n if not d.get("nextPageToken") else n  # 100 => "100(+)"
    except Exception:
        return -1

def email_status() -> dict:
    boxes = {}
    for a in ACCOUNTS:
        boxes[a.split("@")[0]] = {
            "inbox": _count(a, "label:inbox"),
            "action": _count(a, 'label:"1 Action"'),
            "waiting": _count(a, 'label:"2 Waiting"'),
            "new_today": _count(a, "in:inbox newer_than:1d -category:promotions -category:social"),
        }
    return boxes

# all accounts are scanned for recent docs; most-recent-across-all is what surfaces
DOC_ACCOUNTS = os.environ.get("GTD_DOC_ACCOUNTS", ",".join(ACCOUNTS)).split(",")
# Nextcloud GTD roots — offline during the outage (~restored 2026-08); when the
# folder reappears its p/ (projects) and r/ (reference) files are picked up
# automatically, no code change. Extra roots via GTD_DOC_DIRS (colon-separated).
NEXTCLOUD_ROOTS = [Path.home() / "dkloud.douno.it"] + \
    [Path(p) for p in os.environ.get("GTD_DOC_DIRS", "").split(":") if p]

def recent_docs(n: int = 10, days: int = 21) -> list[dict]:
    """Latest documents Chef worked on, newest first: Google Drive across the
    doc accounts + any local/Nextcloud roots that exist. Each: {name, when, where, link}."""
    import datetime as _dt
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    docs: list[dict] = []
    # --- Google Drive ---
    for acct in DOC_ACCOUNTS:
        try:
            out = subprocess.run([GOG, "drive", "ls", "-a", acct, "--all", "--max", "200", "-j"],
                                 capture_output=True, text=True, timeout=90).stdout
            for f in json.loads(out).get("files", []):
                mime = f.get("mimeType", "")
                # docs only — skip folders, audio/video/image/archives
                if "folder" in mime or mime.split("/", 1)[0] in ("audio", "video", "image"):
                    continue
                mt = f.get("modifiedTime", "")
                if mt and mt >= cutoff:
                    docs.append({"name": f.get("name", "?"), "when": mt[:10],
                                 "where": f"drive:{acct.split('@')[0]}",
                                 "link": f.get("webViewLink", "")})
        except Exception:
            continue
    # --- local / Nextcloud (dkloud p/ + r/); active once the outage clears ---
    exts = {".md", ".docx", ".doc", ".odt", ".pdf", ".xlsx", ".txt", ".gdoc"}
    for root in NEXTCLOUD_ROOTS:
        if not root.exists():
            continue
        for sub in ("p", "r", ""):
            base = root / sub if sub else root
            if not base.exists():
                continue
            for p in base.rglob("*"):
                try:
                    if p.is_file() and p.suffix.lower() in exts:
                        mt = datetime.datetime.fromtimestamp(p.stat().st_mtime, datetime.timezone.utc)
                        if mt.isoformat() >= cutoff:
                            docs.append({"name": p.name, "when": mt.date().isoformat(),
                                         "where": f"nextcloud:{sub or '/'}", "link": str(p)})
                except Exception:
                    continue
    # newest first, dedupe by name
    docs.sort(key=lambda d: d["when"], reverse=True)
    seen, out2 = set(), []
    for d in docs:
        if d["name"] in seen:
            continue
        seen.add(d["name"]); out2.append(d)
    return out2[:n]

WIKI_DIR = Path(os.environ.get("WIKI_DIR", str(Path.home() / "clawd" / "wiki")))
YT_LOG = Path(os.environ.get("YT_INGEST_LOG", str(Path.home() / "clawd" / "logs" / "youtube-ingest-cron.log")))

def corpus_status() -> dict:
    """Health of the realmwiki + ingest pipeline + skmem-pg corpus. Surfaces
    whether triage is needed and whether maintenance/ingest jobs are running."""
    import re as _re
    st: dict = {"wiki": {}, "ingest": {}, "corpus": {}}
    # --- wiki scan (dangling/orphans/status mix = triage backlog) ---
    try:
        r = subprocess.run(["/home/cbrd21/.skenv/bin/python3", str(WIKI_DIR / "tools" / "wiki_maintain.py"),
                            "scan", "--top", "1"], cwd=str(WIKI_DIR), capture_output=True, text=True, timeout=150)
        m = _re.search(r"pages\s+(\d+)\s*\|\s*dangling holes\s+(\d+)\s*\|\s*orphans\s+(\d+)", r.stdout)
        if m:
            st["wiki"].update(pages=int(m[1]), dangling=int(m[2]), orphans=int(m[3]))
        sm = _re.search(r"status:\s*(\{.*\})", r.stdout)
        if sm:
            try:
                d = json.loads(sm[1].replace("'", '"'))
                st["wiki"]["stub"] = d.get("stub", 0)
                st["wiki"]["unverified"] = d.get("unverified", 0)  # Lumina's research queue
            except Exception:
                pass
    except Exception as e:
        st["wiki"]["error"] = str(e)[:80]
    # --- wiki git: uncommitted pages + last commit age ---
    try:
        u = subprocess.run(["git", "-C", str(WIKI_DIR), "status", "--short"],
                           capture_output=True, text=True, timeout=30).stdout
        st["wiki"]["uncommitted"] = sum(1 for ln in u.splitlines() if ln.strip())
        st["wiki"]["last_commit"] = subprocess.run(
            ["git", "-C", str(WIKI_DIR), "log", "-1", "--format=%cr"],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        pass
    # --- youtube/corpus ingest: last run result from its log ---
    try:
        if YT_LOG.exists():
            tail = subprocess.run(["tail", "-c", "4000", str(YT_LOG)], capture_output=True, text=True).stdout
            mm = list(_re.finditer(r"ok=(\d+)\s+fail=(\d+)(?:\s+skip=(\d+))?", tail))
            if mm:
                last = mm[-1]
                st["ingest"].update(ok=int(last[1]), fail=int(last[2]), skip=int(last[3] or 0))
            age_h = round((datetime.datetime.now().timestamp() - YT_LOG.stat().st_mtime) / 3600, 1)
            st["ingest"]["last_run_h_ago"] = age_h
    except Exception as e:
        st["ingest"]["error"] = str(e)[:80]
    # --- skmem-pg corpus size ---
    try:
        env = {**os.environ, "PGPASSWORD": "skmemory"}
        c = subprocess.run(["psql", "-h", "localhost", "-U", "postgres", "-d", "skmemory",
                            "-tAc", "select count(*) from docs;"], capture_output=True, text=True,
                           timeout=30, env=env).stdout.strip()
        if c.isdigit():
            st["corpus"]["docs"] = int(c)
    except Exception:
        pass
    return st

WIKI_RESEARCH_THRESHOLD = int(os.environ.get("WIKI_RESEARCH_THRESHOLD", "650"))
WIKI_DANGLING_THRESHOLD = int(os.environ.get("WIKI_DANGLING_THRESHOLD", "5000"))

def corpus_check() -> None:
    """When the wiki triage backlog spikes over threshold, capture a real GTD
    Action item via the skos gtd-ingest sink (source=wiki). Week-bucketed
    source_ref => fires at most once per ISO-week, never daily spam."""
    try:
        from skos.gtd_ingest import GtdCapture, capture as sink
    except Exception as e:
        print(f"corpus-check: skos sink unavailable ({e})"); return
    st = corpus_status(); w = st.get("wiki", {})
    y, wk, _ = datetime.date.today().isocalendar()
    bucket = f"{y}W{wk:02d}"
    fired = []
    unv = w.get("unverified", 0) or 0
    if unv > WIKI_RESEARCH_THRESHOLD:
        if sink(GtdCapture(
            text=f"wiki research queue spiking: {unv} unverified pages need grading / primary-source triage",
            source="wiki", source_ref=f"wiki:research@{bucket}", context="@wiki",
            priority="high", status="next",
            meta={"wiki_unverified": unv, "wiki_dangling": w.get("dangling")})):
            fired.append(f"research={unv}>{WIKI_RESEARCH_THRESHOLD}")
    dang = w.get("dangling", 0) or 0
    if dang > WIKI_DANGLING_THRESHOLD:
        if sink(GtdCapture(
            text=f"wiki dangling-link backlog spiking: {dang} holes — wiki_maintain fill falling behind",
            source="wiki", source_ref=f"wiki:dangling@{bucket}", context="@wiki",
            priority="medium", status="next", meta={"wiki_dangling": dang})):
            fired.append(f"dangling={dang}>{WIKI_DANGLING_THRESHOLD}")
    print(f"corpus-check: {('captured ' + ', '.join(fired)) if fired else 'below thresholds — no capture'}")

def gtd_status() -> dict:
    out = {}
    for name in ("inbox", "next-actions", "projects", "waiting-for", "someday-maybe", "archive"):
        p = GTD_DIR / f"{name}.json"
        try:
            out[name] = len(json.loads(p.read_text() or "[]")) if p.exists() else 0
        except Exception:
            out[name] = -1
    # count email-sourced items
    try:
        na = json.loads((GTD_DIR / "next-actions.json").read_text() or "[]")
        wf = json.loads((GTD_DIR / "waiting-for.json").read_text() or "[]")
        out["email_next"] = sum(1 for i in na if i.get("source") == "email")
        out["email_waiting"] = sum(1 for i in wf if i.get("source") == "email")
    except Exception:
        pass
    return out

def cron_status(hours: int = 24) -> dict:
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    jobs: dict[str, dict] = {}
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            try:
                r = json.loads(line)
                t = datetime.datetime.fromisoformat(r["start"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=datetime.timezone.utc)
                if t < since:
                    continue
            except Exception:
                continue
            j = jobs.setdefault(r["job"], {"runs": 0, "ok": 0, "fail": 0, "last": None,
                                           "last_ok": None, "last_dur": None, "last_tail": ""})
            j["runs"] += 1
            j["ok" if r.get("ok") else "fail"] += 1
            if j["last"] is None or r["start"] > j["last"]:
                j["last"], j["last_ok"] = r["start"], r.get("ok")
                j["last_dur"], j["last_tail"] = r.get("dur_s"), r.get("tail", "")
    return jobs

def _fmt_n(n):  # -1 => err, 100 => "100+"
    return "ERR" if n == -1 else (f"{n}+" if n == 100 else str(n))

def render(sections: set[str]) -> str:
    L = []
    if "email" in sections:
        es = email_status(); L.append("📬 EMAIL")
        L.append(f"  {'box':22s} {'inbox':>6s} {'action':>7s} {'wait':>5s} {'new':>4s}")
        for b, v in es.items():
            L.append(f"  {b:22s} {_fmt_n(v['inbox']):>6s} {_fmt_n(v['action']):>7s} "
                     f"{_fmt_n(v['waiting']):>5s} {_fmt_n(v['new_today']):>4s}")
        L.append("")
    if "cron" in sections:
        cs = cron_status(); L.append("⏱  CRON / SCHEDULED (last 24h)")
        if not cs:
            L.append("  (no runs recorded — wrap jobs with sk-cron-run)")
        for job, v in sorted(cs.items()):
            mark = "✅" if v["last_ok"] else "❌"
            L.append(f"  {mark} {job:24s} runs={v['runs']} ok={v['ok']} fail={v['fail']} "
                     f"last={ (v['last'] or '')[:16] } {v['last_dur']}s")
            if not v["last_ok"] and v["last_tail"]:
                L.append(f"       ↳ {v['last_tail'][:120]}")
        L.append("")
    if "docs" in sections:
        rd = recent_docs(); L.append("📄 RECENT DOCS (worked on)")
        if not rd:
            L.append("  (none in window — Drive scanned; Nextcloud offline until ~Aug)")
        for d in rd:
            L.append(f"  {d['when']}  {d['name'][:52]:52s}  {d['where']}")
        L.append("")
    if "corpus" in sections:
        cs = corpus_status(); w = cs["wiki"]; ing = cs["ingest"]; co = cs["corpus"]
        L.append("🧠 CORPUS / WIKI")
        if w.get("pages"):
            triage = "⚠️ triage" if (w.get("unverified", 0) > 400 or w.get("dangling", 0) > 5000) else "ok"
            L.append(f"  wiki: {w['pages']} pages · dangling={w.get('dangling','?')} "
                     f"orphans={w.get('orphans','?')} stubs={w.get('stub','?')} "
                     f"unverified(research)={w.get('unverified','?')} [{triage}]")
        L.append(f"  wiki git: {w.get('uncommitted','?')} uncommitted · last commit {w.get('last_commit','?')}")
        if ing:
            mark = "❌" if ing.get("fail", 0) else "✅"
            L.append(f"  ingest: {mark} ok={ing.get('ok','?')} fail={ing.get('fail','?')} "
                     f"skip={ing.get('skip','?')} · {ing.get('last_run_h_ago','?')}h ago")
        if co.get("docs"):
            L.append(f"  corpus: {co['docs']} docs in skmem-pg")
        L.append("")
    if "gtd" in sections:
        gs = gtd_status(); L.append("✅ GTD (unified store)")
        L.append(f"  inbox={gs.get('inbox')} next={gs.get('next-actions')} "
                 f"projects={gs.get('projects')} waiting={gs.get('waiting-for')} "
                 f"someday={gs.get('someday-maybe')}")
        L.append(f"  from email: next={gs.get('email_next','?')} waiting={gs.get('email_waiting','?')}")
        L.append("")
    return "\n".join(L).rstrip()

def run(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if not a.startswith("--")]
    cmd = args[0] if args else "all"
    if cmd == "corpus-check":
        corpus_check()
        return
    if cmd == "report":
        d = datetime.date.today().isoformat()
        report = render({"cron", "email", "docs", "corpus", "gtd"})
        fails = sum(v["fail"] for v in cron_status().values())
        footer = (f"⚠️ {fails} job failure(s) in last 24h" if fails
                  else "✅ all scheduled work green")
        # Telegram: the tables are fixed-width, so wrap them in a monospace code
        # fence to preserve column alignment; bold title lives outside the block.
        tg_body = f"📊 *skos Ops Report — {d}*\n\n```\n{report}\n\n{footer}\n```"
        subprocess.run(["hermes", "send", "--to", HERMES_DM,
                        "--subject", f"📊 skos Ops Report — {d}", tg_body], capture_output=True, text=True)
        # console/log copy stays plain-text (no fences)
        print(f"📊 skos Ops Report — {d}\n\n{report}\n\n{footer}")
        return
    sections = {"email", "cron", "docs", "corpus", "gtd"} if cmd in ("all", "") else {cmd}
    if as_json:
        data = {}
        if "email" in sections: data["email"] = email_status()
        if "cron" in sections: data["cron"] = cron_status()
        if "docs" in sections: data["docs"] = recent_docs()
        if "corpus" in sections: data["corpus"] = corpus_status()
        if "gtd" in sections: data["gtd"] = gtd_status()
        print(json.dumps(data, indent=2))
    else:
        print(render(sections))

if __name__ == "__main__":
    run()
