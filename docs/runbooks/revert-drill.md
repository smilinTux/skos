# revert drill: roll an applied change back to baseline

A fire drill for deploys. skos autopilot/deploy can apply changes to durable
state (config, adapters, GTD lists, the model registry). This drill proves such
a change can be **reverted** to the exact pre-change known-good tree, so a bad
autopilot action is undo-able. Card 681514a5.

## Scope

"Revert" here means restoring durable **state/config files** a change touched
back to their pre-change bytes, using a `skos.backup` snapshot taken *before*
the change as the known-good baseline. Autopilot's other revert surface, a
git-merge revert, already lives in `skos.autopilot.engineering.revert` (CLI
`skos autopilot revert <task_id>`); this drill is the state/config side.

## Why

Applying a change is only half of a safe deploy. Syncthing is replication, not
recovery: it propagates a bad write fleet-wide. The revert path has to be proven
the same way you test a backup restore, not assumed. This drill exercises it end
to end and asserts a byte-for-byte return to baseline.

## The primitive

`skos.revert_drill.revert_target(snapshot, label, target)` restores one labeled
source from a pre-change snapshot back over `target`, **in place**:

- every snapshotted file is rewritten to its captured bytes (undoing edits,
  recreating deletions),
- every file under `target` the snapshot does not contain is deleted (undoing
  additions).

After the call `target` byte-matches the snapshot tree. It is **explicit** and
never auto-fired: a caller passes a concrete snapshot and target. Reverting with
a missing snapshot, an unknown label, or an absent target raises a clear
`ValueError` (a safe no-op, nothing is touched).

## Run the drill

```
skos revert-drill run                 # uses a temp scratch dir
skos revert-drill run --scratch /tmp/drill
```

It seeds a scratch target, snapshots it (baseline), applies a change (edit + add
+ delete), reverts, and asserts the target returned to baseline byte-for-byte.
It writes **only** under the scratch dir and never touches live state. Exit code
is non-zero if the target did not return to baseline.

## Operator revert of real state

The drill runs against a scratch target on purpose. To revert real state after a
bad change, take/keep a `skos backup` snapshot from before the change, then
either:

- stage and diff first (safest): `skos backup restore <snap> <staging>`, diff
  the staged tree against live, copy back what you confirm (see
  `docs/runbooks/skbackup-restore.md`); or
- in-place revert a single labeled source with `revert_target(snap, label,
  live_path)` once you have confirmed the snapshot is the intended baseline.

Always confirm the snapshot is the known-good point in time before reverting
live state.

## Test coverage

`tests/test_revert_drill.py`: the full drill returns to baseline byte-for-byte,
the drill confines all writes to the scratch dir, `revert_target` restores
edit/add/delete correctly, and the negative cases (missing snapshot, unknown
label, absent target) raise clear errors.
