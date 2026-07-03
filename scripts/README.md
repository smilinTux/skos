# skos/scripts

Shell helpers for the **gtd-ingest** subsystem (the Python lives in the package:
`skos.mail`, `skos.status`, `skos.adapters.*`; CLI via `skos`, `sk-status`, `gtd-mail`).

| Script | Role | Standard |
|---|---|---|
| `sk-cron-run.sh <job> <cmd…>` | observability wrapper — run-ledger + failure→GTD + `sk-alert` | [OBSERVABILITY_AND_SCHEDULING](https://github.com/smilinTux/sk-standards/blob/main/standards/OBSERVABILITY_AND_SCHEDULING_STANDARD.md) |
| `gtd-triage.sh` | deterministic Gmail noise-sweep (promotions/social/updates → `3 Read`) | — |

Wired into the `noroc2027` crontab; full SOP in [`../docs/gtd-ingest-SOP.md`](../docs/gtd-ingest-SOP.md).
