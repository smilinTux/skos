# skbackup — backup and restore runbook

Point-in-time backups of the durable skos state, independent of Syncthing.
Card 17660fbe; deploy-plan step 3c (`docs/deploy-plan/skos-bulletproof-deploy.md`).

## Why

Syncthing is replication, not backup: a corrupt or deleted file propagates
fleet-wide (the .41 outbox pileup proved the sync path is itself fragile).
skbackup takes a consistent, versioned snapshot so a bad write or an accidental
delete is recoverable to a known-good point in time.

## What is covered

| Source          | Live path (resolver)                                   |
|-----------------|--------------------------------------------------------|
| GTD store       | `skos.gtd_ingest.gtd_dir()` (`$SK_GTD_DIR` > default)  |
| Cron ledger     | `$SK_CRON_LEDGER` > `~/.skcapstone/logs/cron-ledger.jsonl` |
| Model registry  | `$SKMODELS_REGISTRY` > `~/.skcapstone/models/registry.yaml` |

Each snapshot is one `skos-backup-<UTC-timestamp>.tar.gz` with a `MANIFEST.json`
at the root recording every archived file and its sha256. The GTD store is read
while holding the same advisory lock (`<gtd_dir>/.gtd.lock`) every writer takes,
so a snapshot can never capture a half-written GTD list.

## Backup (manual)

```sh
skos backup run --keep 7                                  # local only
skos backup run --keep 7 --offbox cbrd21@100.x.x.x:/srv/backups/skos   # + off-box
skos backup list                                         # what is retained
skos backup verify <snapshot.tar.gz>                     # tar + every sha256
```

`run` snapshots, self-verifies, prunes to `--keep` newest, and (if `--offbox` is
given) copies the snapshot to a second host (rsync over ssh) or a mounted target.

## Scheduled backup (committed artifacts — NOT applied live by the repo)

Two committed options; pick one. Both run through `sk-cron-run.sh`, so a failure
lands in the cron ledger, captures a GTD item (`source=cron`), and fires
sk-alert.

### systemd user timer (preferred)

```sh
mkdir -p ~/.config/systemd/user
cp ~/clawd/skos/deploy/systemd/skos-backup.{service,timer} ~/.config/systemd/user/
# set the OFF-BOX target (a backup on the same box is not a backup):
systemctl --user edit skos-backup.service   # add:
#   [Service]
#   Environment=SK_BACKUP_OFFBOX=cbrd21@100.x.x.x:/srv/backups/skos
systemctl --user daemon-reload
systemctl --user enable --now skos-backup.timer     # <-- the live-enable step
systemctl --user list-timers skos-backup.timer      # confirm scheduled
```

### crontab alternative

```cron
# daily 03:17 — skos point-in-time backup, off-box to a second host
17 3 * * *  SK_BACKUP_OFFBOX=cbrd21@100.x.x.x:/srv/backups/skos /home/cbrd21/clawd/skos/scripts/skos-backup.sh
```

## Restore drill (tested, per acceptance criteria)

Restore is deliberately **staged**: it extracts into a scratch directory, never
over the live paths. Diff first, then copy back.

```sh
# 1. pick a snapshot
skos backup list

# 2. extract into a staging dir (safe — does not touch live)
skos backup restore ~/.skcapstone/backups/skos/skos-backup-<ts>.tar.gz /tmp/skos-restore

# 3. staging layout mirrors the sources:
#      /tmp/skos-restore/gtd/…              (the GTD store files)
#      /tmp/skos-restore/cron-ledger/cron-ledger.jsonl
#      /tmp/skos-restore/model-registry/registry.yaml

# 4. diff staged vs live BEFORE copying anything back
diff -ru "$(skos path 2>/dev/null; echo "$HOME/.skcapstone/coordination/gtd")" /tmp/skos-restore/gtd

# 5. restore the piece you need. For the GTD store, stop writers first
#    (the scheduler), then copy the specific list file(s) back:
cp /tmp/skos-restore/gtd/next-actions.json "$HOME/.skcapstone/coordination/gtd/next-actions.json"
```

Item-level verification: extract a snapshot, delete a test item's list file,
copy the staged copy back, and confirm the item text matches. This is exercised
by `tests/test_backup.py::test_restore_roundtrip_matches_item_content`.

## Notes

- `capabilities.yaml` records skbackup's canonical engine as `restic`. This
  materialized routine is a dependency-free tar+sha256 snapshotter so it runs on
  a cold host with nothing installed; the off-box copy provides the second tier
  restic would otherwise give. A restic backend can later slot in behind the same
  `skos backup` CLI without changing the schedule or the runbook.
- Retention default is 7 local snapshots. The durable tier is the off-box copy;
  size the off-box retention on the target host.
