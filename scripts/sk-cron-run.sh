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
PY="${PY:-/home/cbrd21/.skenv/bin/python3}"
SKOS_BIN="${SKOS_BIN:-$HOME/.skenv/bin/skos}"
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

# 2) on failure -> GTD capture through the ONE locked skos sink (whole-store
#    dedupe by (source, source_ref), atomic save) + sk-alert. No inline JSON
#    manipulation here: sk-cron-run is just another gtd-ingest adapter.
if [ "$rc" -ne 0 ]; then
  ref="cron:${JOB}@$(date +%F)"
  text="cron FAILED: ${JOB} (exit ${rc}) - $(printf '%s' "$tail" | cut -c1-160)"
  if [ -x "$SKOS_BIN" ]; then
    "$SKOS_BIN" gtd capture "$text" --source cron --source-ref "$ref" \
      --context @ops --priority high >/dev/null \
      || printf 'sk-cron-run: skos gtd capture failed for %s\n' "$ref" >&2
  else
    # fallback: same locked library path via python (no direct JSON writes)
    SK_GTD_TEXT="$text" SK_GTD_REF="$ref" "$PY" - <<'PY' \
      || printf 'sk-cron-run: library gtd capture failed for %s\n' "$ref" >&2
import os
from skos.gtd_ingest import GtdCapture, capture
capture(GtdCapture(text=os.environ["SK_GTD_TEXT"], source="cron",
                   source_ref=os.environ["SK_GTD_REF"],
                   context="@ops", priority="high"))
PY
  fi
  if command -v sk-alert >/dev/null 2>&1; then
    printf 'cron FAILED: %s (exit %s)\n%s' "$JOB" "$rc" "$tail" | sk-alert -l crit -k "cron-$JOB" 2>/dev/null || true
  fi
fi

printf '%s\n' "$out"
exit "$rc"
