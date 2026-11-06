#!/usr/bin/env bash
# sk-cron-run.sh - compatibility shim. The runner itself lives in skcapstone.
#
# sk-cron-run is the estate's scheduled-job observability wrapper. It was
# written here, but on 2026-11-06 it moved to skcapstone and is now installed
# as a command at ~/.skenv/bin/sk-cron-run (a pyproject `script-files` entry,
# beside sk-alert). The move is the sk-alert move of 2026-10-30 repeated for
# the same reason: the units that most need wrapping are skcapstone's
# repo-shipped skfleet seats, which run on fleet nodes where skos is not
# installed, so a runner shipped only by skos could never wrap them.
#
# This file stays because 31 systemd drop-ins and the managed crontab block
# named it by absolute path. Deleting it would not degrade them quietly: an
# ExecStart pointing at an absent binary fails the unit at activation. The
# shim keeps every one of them working with no node change, and they migrate
# to the direct path at leisure (`skos schedule wrap-timers`, `skos schedule
# install`). It holds no logic of its own, so there is exactly one
# implementation of the wrapper.
set -uo pipefail

if [ -n "${SK_CRON_RUN_BIN:-}" ] && [ -x "${SK_CRON_RUN_BIN}" ]; then
  exec "$SK_CRON_RUN_BIN" "$@"
fi
if [ -x "$HOME/.skenv/bin/sk-cron-run" ]; then
  exec "$HOME/.skenv/bin/sk-cron-run" "$@"
fi
# PATH last, and it is not expected to resolve: a systemd user unit and cron
# both run with a PATH that excludes ~/.skenv/bin. Tried anyway so an estate
# that installs the command elsewhere still works.
if resolved="$(command -v sk-cron-run 2>/dev/null)" && [ -n "$resolved" ]; then
  exec "$resolved" "$@"
fi

# Exit 78 (EX_CONFIG) deliberately, and loudly. Returning the wrapped job's
# own exit code here would be a lie: the job never ran.
printf 'sk-cron-run.sh: sk-cron-run is not installed.\n' >&2
printf '  expected: %s\n' "$HOME/.skenv/bin/sk-cron-run" >&2
printf '  it ships with skcapstone: pip install -e ~/clawd/skcapstone-repos/skcapstone\n' >&2
printf '  the wrapped job was NOT run: %s\n' "${1:-<no job named>}" >&2
exit 78
