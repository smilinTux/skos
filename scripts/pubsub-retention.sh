#!/usr/bin/env bash
# Cap each skcapstone pubsub topic to the newest N messages.
#
# WHY: pubsub/topics/<topic>/msg-*.json is append-only and nothing drains it.
# On 2026-11-06 noroc2027 held 287,542 message files, 1.2 GB on disk, of which
# skgateway.warn alone was 255,461 files. subscriptions.json carried a single
# entry ("test.*") with last_read null and message_count 0, so not one message
# had ever been consumed. The content was only ~166 MB; the rest was block
# overhead on a quarter-million tiny files, which is why this shows up as inode
# pressure long before it shows up as a disk-space alert.
#
# The topics are excluded from Syncthing (see the canonical .stignore), so this
# is per-node housekeeping and never propagates a delete to the fleet.
#
# Retention is per topic, newest-first by mtime. Set PUBSUB_KEEP_PER_TOPIC to
# change the cap; set PUBSUB_RETENTION_DRY_RUN=1 to report without deleting.
set -euo pipefail

ROOT="${SKCAPSTONE_HOME:-$HOME/.skcapstone}/pubsub/topics"
KEEP="${PUBSUB_KEEP_PER_TOPIC:-2000}"
DRY="${PUBSUB_RETENTION_DRY_RUN:-0}"

case "$KEEP" in ''|*[!0-9]*) echo "PUBSUB_KEEP_PER_TOPIC must be a number, got '$KEEP'" >&2; exit 64 ;; esac
[ "$KEEP" -ge 1 ] || { echo "PUBSUB_KEEP_PER_TOPIC must be >= 1" >&2; exit 64; }

if [ ! -d "$ROOT" ]; then
  echo "no pubsub topic dir at $ROOT, nothing to do"
  exit 0
fi

pruned=0
kept=0
for dir in "$ROOT"/*/; do
  [ -d "$dir" ] || continue
  topic="$(basename "$dir")"
  # -printf . counts without building an argv, so this is correct past ARG_MAX.
  n="$(find "$dir" -maxdepth 1 -type f -name 'msg-*.json' -printf . | wc -c)"
  kept=$((kept + (n < KEEP ? n : KEEP)))
  if [ "$n" -le "$KEEP" ]; then
    continue
  fi
  over=$((n - KEEP))
  if [ "$DRY" = "1" ]; then
    echo "$topic: $n messages, would delete $over (keep $KEEP)"
    pruned=$((pruned + over))
    continue
  fi
  # Newest first, drop everything past the cap. NUL-delimited so odd names are safe.
  find "$dir" -maxdepth 1 -type f -name 'msg-*.json' -printf '%T@\t%p\0' \
    | sort -z -rn \
    | tail -z -n +$((KEEP + 1)) \
    | cut -z -f2 \
    | xargs -0 -r rm -f
  after="$(find "$dir" -maxdepth 1 -type f -name 'msg-*.json' -printf . | wc -c)"
  echo "$topic: $n -> $after"
  pruned=$((pruned + n - after))
done

if [ "$DRY" = "1" ]; then
  echo "dry run: would prune $pruned message(s), keeping up to $KEEP per topic"
else
  echo "pruned $pruned message(s), keeping up to $KEEP per topic ($kept retained)"
fi
