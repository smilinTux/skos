---
id: node-noroc2027-158
type: node
namespace: ops
lifecycle: canon
title: noroc2027 (.158, dev/primary node)
tags: [node, noroc2027, dot158, primary, funnel]
edges:
  - {target: node-100-gpu, type: connects_to}
state_refs:
  actions: []
created: 2026-07-31
updated: 2026-07-31
---

# node-noroc2027-158: the .158 dev/primary node

Hostname `noroc2027`, the .158 dev/primary box and the tailscale funnel node
(`noroc2027.tail204f0c.ts.net`). Fleet policy: .158 = dev/primary, deploys land
here FIRST with a 24h doctor-green soak before the rest of the fleet.

## Role

- Primary host for the operator-facing services: [[service-skchat]] (daemon +
  telegram bridge + shell/webui funnel), [[service-skdashboard]],
  [[service-skcode]], and [[service-skos]].
- Local skmem-pg primary (`localhost:5432`, `skmem-pg:pg17-bm25-age`); memories
  are derived from flat JSON via daily reconcile; no replication.
- Embeddings failover: .158 is local-primary for travel, else .100-primary then
  .158 local.

## Runbooks that touch this node

- [[runbook-restart-daemon]], [[runbook-restart-telegram-bridge]],
  [[runbook-restart-dashboard]], [[runbook-restart-hostd]],
  [[runbook-archive-stale-session]], [[runbook-purge-outbox]].
- Security: [[runbook-skdashboard-public-leak]],
  [[runbook-shell-embed-auth-and-signing]].

## Notes

The ops skmem-pg `ops` schema and `ops_brain` AGE graph (spec section 4) live in
THIS node's local skmem-pg instance. No secrets on this page; connection
credentials live in skvault.
