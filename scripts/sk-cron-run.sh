#!/usr/bin/env bash
# sk-cron-run <job-name> <command...>
#
# The "cron" gtd-ingest adapter (push): run any scheduled job with observability.
#   1. append a run-ledger record (JSONL) for the daily ops report + skos status
#   2. on failure -> capture a GTD item (source=cron) AND fire sk-alert (realtime)
# Returns the wrapped command's exit code. Nothing fails silently.
set -uo pipefail
JOB="${1:?usage: sk-cron-run <job-name> <command...>}"; shift
LEDGER="$HOME/.skcapstone/logs/cron-ledger.jsonl"
GTD_INBOX="$HOME/.skcapstone/coordination/gtd/inbox.json"
PY="${PY:-/home/cbrd21/.skenv/bin/python3}"
mkdir -p "$(dirname "$LEDGER")"

start_iso=$(date -Iseconds); start_s=$(date +%s)
out="$("$@" 2>&1)"; rc=$?
dur=$(( $(date +%s) - start_s ))
tail=$(printf '%s' "$out" | tail -6 | tr '\n' ' ' | cut -c1-500)

# 1) ledger record
"$PY" - "$JOB" "$start_iso" "$dur" "$rc" "$tail" >> "$LEDGER" <<'PY'
import json,sys
job,start,dur,rc,tail=sys.argv[1:6]
print(json.dumps({"job":job,"host":"noroc2027","start":start,"dur_s":int(dur),
                  "exit":int(rc),"ok":rc=="0","tail":tail}))
PY

# 2) on failure -> GTD capture (deduped by job@date) + sk-alert
if [ "$rc" -ne 0 ]; then
  "$PY" - "$JOB" "$rc" "$tail" "$GTD_INBOX" <<'PY'
import json,sys,uuid,datetime,os
job,rc,tail,path=sys.argv[1:5]
ref=f"cron:{job}@{datetime.date.today().isoformat()}"
try: items=json.load(open(path)) if os.path.exists(path) else []
except Exception: items=[]
if not any(i.get("source_ref")==ref for i in items):
    items.append({"id":uuid.uuid4().hex[:12],
        "text":f"cron FAILED: {job} (exit {rc}) - {tail[:160]}",
        "source":"cron","source_ref":ref,"privacy":"private","context":"@ops",
        "priority":"high","energy":None,"status":"inbox",
        "created_at":datetime.datetime.now(datetime.timezone.utc).isoformat()})
    json.dump(items,open(path,"w"),indent=2,ensure_ascii=False)
PY
  if command -v sk-alert >/dev/null 2>&1; then
    printf 'cron FAILED: %s (exit %s)\n%s' "$JOB" "$rc" "$tail" | sk-alert -l crit -k "cron-$JOB" 2>/dev/null || true
  fi
fi

printf '%s\n' "$out"
exit "$rc"
