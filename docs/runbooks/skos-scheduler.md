# Runbook: skos scheduler-as-code (deploy / rollback / cutover)

The skos gtd-ingest + observability pipeline (12 `sk-cron-run.sh`-wrapped jobs) is
**declared in the repo**, not hand-edited into a crontab. This runbook covers the
one-time cutover of the primary node (noroc2027 / .158) from the legacy hand-edited
crontab lines to the committed manifest, plus routine deploy and rollback.

Card: `29ae4313` · deploy-plan item 4b (`docs/deploy-plan/skos-bulletproof-deploy.md`).

## Artifacts

| File | Role |
|---|---|
| `deploy/schedule/jobs.yaml` | Desired state. Portable (`$HOME` / `$SKOS_REPO`); **no secret value**. Single source of truth. |
| `deploy/schedule/skos-schedule.env.example` | Template for the not-committed secret env file. |
| `src/skos/schedule.py` | Load/validate/render/diff/install engine. |
| `skos schedule {list,render,diff,install}` | Operator CLI. |
| `~/.skcapstone/skos-schedule.env` | Runtime values (owned regular file, mode 600, **not** in git or crontab). |

Design note (why crontab, not systemd timers) is in the header of `jobs.yaml`: the
live pipeline is already a user crontab, every job is wrapped in `sk-cron-run.sh`, and
one ordered crontab block reproduces the live schedule byte-for-byte for a clean
cutover. `deploy/systemd/` still ships the `skos-backup` timer (the one job that
benefits from `OnCalendar` catch-up).

## Prerequisites

```bash
cd ~/clawd/skos && pip install -e .        # provides the `skos schedule` CLI
# secret env file (gog keyring password). NEVER paste it into git.
install -m 600 deploy/schedule/skos-schedule.env.example ~/.skcapstone/skos-schedule.env
${EDITOR:-nano} ~/.skcapstone/skos-schedule.env   # set the real GOG_KEYRING_PASSWORD
```

## Validate before touching anything

```bash
skos schedule list     # parses + validates the manifest, lists the 12 jobs
skos schedule diff     # compares manifest vs live crontab; exit 0 = already matches
skos schedule render --expand   # host-concrete lines (secret still shown as $NAME)
```

`skos schedule diff` keys on job name and **ignores the secret value**, so it reports
`clean` when the live (legacy) lines already match the manifest - which is the signal
that cutover is a no-op for behavior.

## One-time cutover on .158

1. **Back up the current crontab** (rollback point):
   ```bash
   crontab -l > ~/.skcapstone/backups/crontab.$(date +%F-%H%M).bak
   ```
2. **Confirm parity** — `skos schedule diff` should already say `clean` (the manifest
   was built from the live schedule). If it shows drift, reconcile the manifest first;
   never blind-overwrite.
3. **Preview** the resulting crontab:
   ```bash
skos schedule install            # DRY RUN; prints paths/commands, never values
   ```
4. **Apply** — writes the managed block (`# >>> skos schedule (managed) >>>` …
   `# <<< skos schedule (managed) <<<`) into the crontab:
   ```bash
skos schedule install --apply
   ```
5. **Remove the legacy duplicates.** After apply, the 12 legacy hand-edited
   `sk-cron-run.sh` lines still exist *outside* the managed block. Delete exactly those
   12 lines with `crontab -e` so each job runs once. Verify:
   ```bash
   crontab -l | grep -c 'sk-cron-run.sh'   # expect 12 (all inside the managed block)
   skos schedule diff                       # expect: clean
   ```

From then on the managed block is authoritative; the legacy lines are gone.

## Routine deploy (after the cutover)

Edit `deploy/schedule/jobs.yaml`, commit, then on the node:

```bash
git pull && pip install -e .
skos schedule diff              # see what will change
skos schedule install --apply   # replace-in-place; idempotent, never duplicates
```

`install` only rewrites its own marked block; all other personal cron lines are
preserved. Re-running with no manifest change is a no-op.

## Rollback

- **Fast:** restore the saved crontab: `crontab - < ~/.skcapstone/backups/crontab.<ts>.bak`.
- **Surgical:** delete the managed block with `crontab -e` (everything between the two
  marker lines), or `git checkout <prev> -- deploy/schedule/jobs.yaml` then
  `skos schedule install --apply` to roll to a previous manifest.

## Standby / failover (deploy-plan 5b)

The same artifacts install unchanged on the standby (.41): set up the env file, run
`skos schedule diff` (expect all `missing` since .41 is idle), and keep it **not
applied** until failover. This is scheduler-only; it does not extend to skmem-pg
(per-node writable, rebuildable, never replicated).

## Troubleshooting

| Symptom | Check |
|---|---|
| job exits 78 before execution | runtime env file is a symlink, foreign-owned, or not mode 600/400; replace it atomically with a safe regular file. |
| `diff` shows `changed` for a job | manifest schedule/command differs from live; reconcile the manifest, then `install --apply`. |
| `diff` shows `extra` | a `sk-cron-run.sh` job exists live but is not declared; add it to `jobs.yaml` or remove it from the crontab. |
| job runs twice | legacy line not removed after cutover (step 5). |
| `schedule error: runner script … not found` | running from outside the repo, or a bad checkout; the runner is repo-relative (`$SKOS_REPO/scripts/sk-cron-run.sh`). |

### Credential rotation

Write the replacement env file beside the existing file with mode 600, validate its
ownership and contents locally, then atomically rename it over
`~/.skcapstone/skos-schedule.env`. Run one wrapped job as a canary. The managed
crontab does not change because it contains only `SKOS_SCHEDULE_ENV=<path>`.
Retire the old credential only after the canary succeeds. Roll back by atomically
restoring the protected prior file. Never paste either version into `crontab`, a
systemd unit, command output, or the repository.
