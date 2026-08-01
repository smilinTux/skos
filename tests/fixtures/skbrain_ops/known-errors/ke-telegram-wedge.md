---
id: ke-telegram-wedge
type: known-error
namespace: ops
lifecycle: canon
title: telegram bridge silent wedge
tags: [operator, skchat, telegram, bridge]
edges:
  - {target: service-skchat, type: touches}
  - {target: node-noroc2027-158, type: touches}
state_refs:
  kedb: ke-telegram-wedge
  actions: [restart-telegram-bridge]
created: 2026-07-31
updated: 2026-07-31
---

# ke-telegram-wedge: telegram bridge silent wedge

Canon known-error for the operator condition `BridgeAlive`. Referenced in the
skchat adapter's `kedb_refs`.

## Symptoms

- `BridgeAlive` is False.
- The telegram bridge last-poll heartbeat is older than 600s while the daemon
  is up (the ConnectTimeout hang signature: it looks alive but delivers
  nothing, no new polls).

## Root cause

The telegram bridge poll loop hung on a ConnectTimeout and stopped polling
without exiting, so the process looks alive but delivers nothing.

## Workaround

Restart the wedged per-agent bridge unit
(`skchat-telegram-<agent>.service`) to clear the silent-wedge signature. Full
procedure: [[runbook-restart-telegram-bridge]].

## Permanent fix status

Workaround only. The hang is a client-timeout that does not raise; a poll
watchdog that exits on stale heartbeat would let systemd restart it
automatically (candidate permanent fix).

## Links

Remediated by [[runbook-restart-telegram-bridge]]. Affects [[service-skchat]] on
[[node-noroc2027-158]].
