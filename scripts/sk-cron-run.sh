#!/usr/bin/env bash
# sk-cron-run <job-name> <command...>
#
# The "cron" gtd-ingest adapter (push): run any scheduled job with observability.
#   1. append a run-ledger record (JSONL) for the daily ops report + skos status
#   2. on failure -> capture a GTD item (source=cron) AND fire sk-alert (realtime)
# Returns the wrapped command's exit code. Nothing fails silently.
set -uo pipefail
# Load sovereign runtime configuration from one protected file. The crontab carries
# only this path, never values. Refuse symlinks, foreign ownership, or group/world
# access before sourcing because this file is executable shell syntax.
SCHEDULE_ENV="${SKOS_SCHEDULE_ENV:-$HOME/.skcapstone/skos-schedule.env}"
if [ -e "$SCHEDULE_ENV" ]; then
  if [ -L "$SCHEDULE_ENV" ] || [ ! -f "$SCHEDULE_ENV" ]; then
    printf 'sk-cron-run: refusing unsafe schedule env file\n' >&2; exit 78
  fi
  env_uid=$(stat -c '%u' "$SCHEDULE_ENV" 2>/dev/null || printf 'invalid')
  env_mode=$(stat -c '%a' "$SCHEDULE_ENV" 2>/dev/null || printf 'invalid')
  case "$env_mode" in 600|400) ;; *)
    printf 'sk-cron-run: schedule env file must deny group/world access\n' >&2; exit 78;;
  esac
  if [ "$env_uid" != "$(id -u)" ]; then
    printf 'sk-cron-run: schedule env file has foreign owner\n' >&2; exit 78
  fi
  set -a; . "$SCHEDULE_ENV"; set +a
fi
JOB="${1:?usage: sk-cron-run <job-name> <command...>}"; shift
LEDGER="$HOME/.skcapstone/logs/cron-ledger.jsonl"
# Interpreter + host derive from the environment (no machine-specific literals):
#   PY            -> $HOME/.skenv/bin/python3 if present, else PATH python3
#   SK_CRON_HOST  -> hostname (override for tests / alt nodes)
if [ -z "${PY:-}" ]; then
  if [ -x "$HOME/.skenv/bin/python3" ]; then PY="$HOME/.skenv/bin/python3"; else PY="$(command -v python3)"; fi
fi
SK_CRON_HOST="${SK_CRON_HOST:-$(hostname)}"
SKOS_BIN="${SKOS_BIN:-$HOME/.skenv/bin/skos}"
# sk-alert must be resolved by PATH-independent means, exactly like PY and
# SKOS_BIN above. It lives in ~/.skenv/bin, which is on an interactive shell's
# PATH and on NEITHER environment that runs scheduled jobs: a systemd user unit
# (probed: not reachable) or cron (PATH=/usr/bin:/bin, and the crontab sets no
# PATH). The old `command -v sk-alert` guard therefore evaluated false every
# time and the realtime alert never fired for any scheduled job, while the GTD
# capture kept working because it goes through $SKOS_BIN's absolute path.
# Same class as the 2026-08-13 watchdog incident: cron's PATH lacked /usr/sbin,
# so `qm` exited 127 while the `kill` builtin worked fine.
if [ -z "${SK_ALERT_BIN:-}" ]; then
  if [ -x "$HOME/.skenv/bin/sk-alert" ]; then SK_ALERT_BIN="$HOME/.skenv/bin/sk-alert"
  else SK_ALERT_BIN="$(command -v sk-alert || true)"; fi
fi
mkdir -p "$(dirname "$LEDGER")"

start_iso=$(date -Iseconds); start_s=$(date +%s)
out="$("$@" 2>&1)"; rc=$?
dur=$(( $(date +%s) - start_s ))
tail=$(printf '%s' "$out" | tail -6 | tr '\n' ' ' | cut -c1-500)

# 1) ledger record
"$PY" - "$JOB" "$start_iso" "$dur" "$rc" "$tail" "$SK_CRON_HOST" >> "$LEDGER" <<'PY'
import json,sys
job,start,dur,rc,tail,host=sys.argv[1:7]
print(json.dumps({"job":job,"host":host,"start":start,"dur_s":int(dur),
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
  if [ -n "${SK_ALERT_BIN:-}" ] && [ -x "$SK_ALERT_BIN" ]; then
    # The message is an ARGUMENT, not stdin. sk-alert does not read stdin: it
    # exits 2 with "sk_alert: empty message", and the `|| true` below then
    # swallowed that, so a piped alert looked sent and never was. Two silent
    # failures were stacked on this one line: unreachable binary, wrong
    # calling convention.
    "$SK_ALERT_BIN" -l crit -k "cron-$JOB" \
      "cron FAILED: ${JOB} (exit ${rc}) - ${tail}" >/dev/null 2>&1 || true
  else
    printf 'sk-cron-run: no sk-alert binary found; %s failure was not alerted\n' "$JOB" >&2
  fi
fi

printf '%s\n' "$out"
exit "$rc"
