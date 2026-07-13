#!/usr/bin/env bash
# gtd-triage.sh: keep Gmail inboxes near zero by sweeping low-value categories
# into the "3 Read" label and out of the inbox (archive = remove INBOX label).
#
# SAFE / reversible: nothing is trashed or deleted. Messages are labelled
# "3 Read" and archived (still searchable; re-add INBOX to undo). Starred and
# important mail is never touched. Real (category:primary) mail is left in the
# inbox for human/agent judgment into 1 Action / 2 Waiting.
#
# Usage:  gtd-triage.sh [account ...]      (default: all configured accounts)
#         gtd-triage.sh --dry-run [account ...]
#
# Runs daily via cron on noroc2027 (.158). Goal: inbox = only real, un-triaged
# primary mail; everything else filed under labels.
set -uo pipefail
export GOG_KEYRING_PASSWORD="${GOG_KEYRING_PASSWORD:-sk2026}"
GOG="${GOG:-/home/linuxbrew/.linuxbrew/bin/gog}"

DEFAULT_ACCTS=(chefboyrdave2.1@gmail.com david.knestrick@gmail.com cbd2dot11@gmail.com dounoit@gmail.com jaimeanddavid2014@gmail.com)
LOGDIR="$HOME/.skcapstone/logs"; mkdir -p "$LOGDIR"
LOG="$LOGDIR/gtd-triage.log"

DRY=""; ACCTS=()
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY="--dry-run" || ACCTS+=("$a"); done
[ ${#ACCTS[@]} -eq 0 ] && ACCTS=("${DEFAULT_ACCTS[@]}")

# Categories swept to "3 Read" + archived. Exclude starred/important so nothing
# the user (or Gmail) flagged as significant is moved.
SWEEP_QUERIES=(
  "in:inbox category:promotions -is:starred -is:important"
  "in:inbox category:social -is:starred -is:important"
  "in:inbox category:forums -is:starred -is:important"
  "in:inbox category:updates -is:starred -is:important"
)

ts(){ date '+%Y-%m-%d %H:%M:%S'; }
total_swept=0; summary=""

for acct in "${ACCTS[@]}"; do
  acct_swept=0
  for q in "${SWEEP_QUERIES[@]}"; do
    # collect message IDs (col 1 of plain output; skip header line)
    mapfile -t ids < <("$GOG" gmail list -a "$acct" "$q" --all -p 2>/dev/null | tail -n +2 | awk -F'\t' 'NF>0{print $1}')
    [ ${#ids[@]} -eq 0 ] && continue
    if [ -n "$DRY" ]; then
      echo "$(ts) [DRY] $acct: would file ${#ids[@]} (${q#in:inbox }) -> 3 Read + archive" | tee -a "$LOG"
    else
      # batch in chunks of 800 (Gmail batchModify limit is 1000)
      i=0
      while [ $i -lt ${#ids[@]} ]; do
        chunk=("${ids[@]:$i:800}")
        "$GOG" gmail batch modify -a "$acct" "${chunk[@]}" --add "3 Read" --remove INBOX -y >/dev/null 2>&1
        i=$((i+800))
      done
      echo "$(ts) $acct: filed ${#ids[@]} (${q#in:inbox }) -> 3 Read + archive" | tee -a "$LOG"
    fi
    acct_swept=$((acct_swept+${#ids[@]}))
  done
  remaining=$("$GOG" gmail list -a "$acct" "in:inbox" --max 500 -j 2>/dev/null | ~/.skenv/bin/python3 -c "import sys,json
try:
 d=json.load(sys.stdin); print(str(len(d.get('threads',[])))+('+' if d.get('nextPageToken') else ''))
except: print('?')")
  summary+="${acct%@gmail.com}: swept ${acct_swept}, inbox now ${remaining} (primary mail to triage)\n"
  total_swept=$((total_swept+acct_swept))
done

echo "$(ts) gtd-triage done: swept ${total_swept} across ${#ACCTS[@]} accounts" | tee -a "$LOG"
if [ -z "$DRY" ] && command -v sk-alert >/dev/null 2>&1; then
  printf "🧹 GTD triage (noroc2027)\nswept %s noise emails -> 3 Read\n%b" "$total_swept" "$summary" | sk-alert -l info -k gtd-triage 2>/dev/null || true
fi
printf "%b" "$summary"
