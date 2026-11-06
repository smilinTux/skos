# skos/scripts

Shell helpers for the **gtd-ingest** subsystem (the Python lives in the package:
`skos.mail`, `skos.status`, `skos.adapters.*`; CLI via `skos`, `sk-status`, `gtd-mail`).

| Script | Role | Standard |
|---|---|---|
| `sk-cron-run.sh <job> <cmd…>` | **shim only.** `exec`s `~/.skenv/bin/sk-cron-run`, the observability wrapper (run-ledger + failure→GTD + `sk-alert`), which ships with skcapstone. Kept for nodes and hand-written units that still name this path; as of 2026-11-06 nothing on nor does, so it is a compatibility path, not a live one. | [OBSERVABILITY_AND_SCHEDULING](https://github.com/smilinTux/sk-standards/blob/main/standards/OBSERVABILITY_AND_SCHEDULING_STANDARD.md) |
| `gtd-triage.sh` | deterministic Gmail noise-sweep (promotions/social/updates → `3 Read`) | - |
| `skos-backup.sh` | scheduled skbackup entrypoint: `skos backup run` inside `sk-cron-run` (GTD store + cron ledger + model registry) | [runbook](../docs/runbooks/skbackup-restore.md) |

Backup schedule units live in [`../deploy/systemd/`](../deploy/systemd/) (committed, **not** applied live).

Wired into the `noroc2027` crontab; full SOP in [`../docs/gtd-ingest-SOP.md`](../docs/gtd-ingest-SOP.md).
