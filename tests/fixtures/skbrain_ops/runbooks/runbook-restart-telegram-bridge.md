---
id: runbook-restart-telegram-bridge
type: runbook
namespace: ops
lifecycle: canon
title: Recover a silently wedged telegram bridge
tags: [skchat, telegram, bridge, restart, operator]
edges:
  - {target: ke-telegram-wedge, type: remediates}
  - {target: service-skchat, type: touches}
  - {target: node-noroc2027-158, type: touches}
state_refs:
  kedb: ke-telegram-wedge
  actions: [restart-telegram-bridge]
created: 2026-07-31
updated: 2026-07-31
---

# Recover a silently wedged telegram bridge

Standard operator action `restart-telegram-bridge` (reversible, blast radius
low). Remediates [[ke-telegram-wedge]] on [[service-skchat]].

## Preconditions

- The fleet is NOT frozen.
- `BridgeAlive` is False: the bridge's last-poll heartbeat is older than 600s
  while the daemon is up (the ConnectTimeout silent-wedge signature). The
  daemon looks alive but delivers nothing.

## Steps

The bridge unit is per-agent: `skchat-telegram-<agent>.service`, where `<agent>`
is the active `SKAGENT` (default `lumina`).

1. Identify the agent and confirm the wedge:

   ```bash
   AGENT="${SKAGENT:-${SKCAPSTONE_AGENT:-lumina}}"
   systemctl --user status "skchat-telegram-${AGENT}.service" --no-pager | head -20
   ```

2. Restart the wedged unit:

   ```bash
   systemctl --user restart "skchat-telegram-${AGENT}.service"
   ```

## Verify

```bash
AGENT="${SKAGENT:-${SKCAPSTONE_AGENT:-lumina}}"
systemctl --user is-active "skchat-telegram-${AGENT}.service"   # -> active
journalctl --user -u "skchat-telegram-${AGENT}.service" -n 20 --no-pager   # fresh poll lines
```

New poll heartbeats within 600s mean `BridgeAlive` returns to True.

## Rollback

Idempotent; nothing to undo.

## Escalate

If it re-wedges quickly, the ConnectTimeout source (network path to Telegram)
is the real problem; raise it and consider the poll-watchdog permanent fix noted
on [[ke-telegram-wedge]].
