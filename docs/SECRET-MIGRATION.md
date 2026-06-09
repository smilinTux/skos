# Retiring the committed secrets into skvault

For each: `skos secret set <scope>/<key> "<value>"` then delete the plaintext from the source file
and replace with a pointer comment `# value: skos secret get <scope>/<key>`.

| Secret | New skvault ref | Source to scrub |
|---|---|---|
| Cloudflare DNS token | `cloud/cloudflare_dns_token` | skworld-main/CLOUDFLARE-DNS-CONFIG.md |
| GitHub PAT | `cloud/github_pat` | SKPrivate/forge-github-config.md |
| authentik RSA keys | `core/authentik_jwt_key` | backups/authentik-db-backup-*.sql (purge from history) |
| Qdrant API key | (n/a — Qdrant decommissioned) | tools/memory-bridge/memory_bridge.py:46 (delete line) |

History scrub (authentik dump) = `git filter-repo`; coordinate with the hardening/decommission phases.
