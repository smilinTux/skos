---
id: service-skcode
type: service
namespace: ops
lifecycle: canon
title: skcode (session plane, hostd)
tags: [skcode, hostd, session, harness]
edges:
  - {target: node-noroc2027-158, type: runs_on}
  - {target: service-skchat, type: connects_to}
state_refs:
  actions: [restart-hostd, archive-stale-session]
created: 2026-07-31
updated: 2026-07-31
---

# service-skcode

The skcode session plane: skcode-hostd hosts and supervises coding sessions
(PTY/tmux backed), reachable in the shell as a gated pane.

## Definition

- Host process: `skcode-hostd.service`, self API
  `http://localhost:9394/api/v1/hosts/self`.
- Manifest served at `/.well-known/skworld-module.json` (module `skcode`).
- Runs on [[node-noroc2027-158]]; proxied by [[service-skchat]] shell.

## Operator conditions

`HostdReady`, `SessionsHealthy`, `RegistryConsistent`, `AuthEnforced`.

## Known errors and runbooks

- [[ke-skcode-hostd-down]] -> [[runbook-restart-hostd]].
- [[ke-skcode-session-wedge]] -> [[runbook-archive-stale-session]] (archive is
  stop + persist; the destructive `kill-runaway-session` is MAJOR only).
