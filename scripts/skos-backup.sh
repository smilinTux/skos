#!/usr/bin/env bash
# skos-backup.sh — the scheduled skbackup entrypoint (card 17660fbe / deploy 3c).
#
# Thin wrapper the systemd timer (or a crontab line) calls. It runs the real
# `skos backup run` INSIDE sk-cron-run.sh, so a failure is observable exactly
# like every other scheduled job: a run-ledger record, a GTD capture
# (source=cron), and a realtime sk-alert. Nothing fails silently.
#
# Config (env, all optional):
#   SK_BACKUP_DEST    local retention dir      (default: ~/.skcapstone/backups/skos)
#   SK_BACKUP_KEEP    snapshots to retain      (default: 7)
#   SK_BACKUP_OFFBOX  off-box target host:path or a mounted dir (default: unset -> local only)
#   SKOS_BIN          skos CLI                 (default: ~/.skenv/bin/skos)
#
# Off-box durability is the whole point of a backup (redundancy mantra). Set
# SK_BACKUP_OFFBOX to a SECOND host, never a path on this box.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKOS_BIN="${SKOS_BIN:-$HOME/.skenv/bin/skos}"
KEEP="${SK_BACKUP_KEEP:-7}"

args=(backup run --keep "$KEEP")
[ -n "${SK_BACKUP_DEST:-}" ]   && args+=(--dest "$SK_BACKUP_DEST")
[ -n "${SK_BACKUP_OFFBOX:-}" ] && args+=(--offbox "$SK_BACKUP_OFFBOX")

exec "$HERE/sk-cron-run.sh" skos-backup "$SKOS_BIN" "${args[@]}"
