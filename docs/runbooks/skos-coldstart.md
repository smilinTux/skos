# skos cold-start bootstrap: restore before first run

Card f15d086d. This runbook defines the ORDER a wiped or freshly provisioned
node comes back in, and documents the empty-store guard that enforces it.

## The hazard

The unified GTD store lives at `~/.skcapstone/coordination/gtd` and is
**Syncthing-replicated**. Replication is not restore: a corrupt, deleted, or
empty file propagates to every node. If skos runs and **emits** (any
`skos gtd capture` / `skos gtd upsert` / `skos ingest ...` / a scheduled
adapter) before the store has been restored or synced, it writes onto an EMPTY
store and Syncthing then pushes that empty state fleet-wide, clobbering the real
data on every other node.

So on a cold machine there is exactly one safe order: **restore the store, THEN
let skos run.** Never the reverse.

## The bootstrap order (do this on a cold / re-provisioned node)

1. **Install the code.** `git clone` + the documented install (see the README /
   `docs/gtd-ingest-SOP.md`). Do NOT start any skos timers/cron yet.

2. **Do NOT emit yet.** Keep the scheduler out of the crontab until step 4
   passes. `skos schedule install` without `--apply` is a dry run and is safe.

3. **Restore the store.** Bring `~/.skcapstone/coordination/gtd` back to real
   data by ONE of:
   - let Syncthing finish an initial sync from a healthy node, or
   - restore the newest skbackup snapshot
     (`skos backup list`, then stage + copy back per
     `docs/runbooks/skbackup-restore.md`).

4. **Preflight.** Confirm the store is populated and the guard is happy:

   ```
   skos coldstart check
   ```

   Exit 0 = safe to emit. Exit 1 = the guard would trip (initialized node,
   empty store) - go back to step 3, do not start services.

5. **Stamp the node** (only after step 4 is green):

   ```
   skos coldstart init
   ```

   This writes the local, per-node sentinel `node-initialized`. From now on, if
   this node's store ever goes empty, the guard treats it as a dangerous
   cold-start-before-restore and refuses to emit. `init` refuses on an empty
   store unless you pass `--force`.

6. **Now start the scheduler** (`skos schedule install --apply`) and any skos
   services. Emitting is safe.

## The empty-store guard (enforcement)

`skos.coldstart.guard_store()` runs at the top of the two write sinks
(`skos.gtd_ingest.capture` and `upsert`), under the store lock, before anything
is written. Decision matrix:

| node sentinel | store empty | outcome |
|---------------|-------------|---------|
| present       | yes         | **REFUSE** (`ColdStartGuardError`) - cold-start-before-restore. Nothing is written, so no empty state replicates. |
| present       | no          | proceed (steady state). |
| absent        | yes         | genuine fresh init - allowed (nothing to lose). |
| absent        | no          | proceed, and stamp the sentinel so a *later* wipe-to-empty on this node is caught. |

The guard only ever refuses or allows. It never deletes or truncates store data.

## The node sentinel

- Path: `$SKOS_COLDSTART_MARKER`, else `$SKOS_STATE_DIR/node-initialized`, else
  `$XDG_STATE_HOME/skos/node-initialized`, else `~/.local/state/skos/node-initialized`.
- It is **local and per-node on purpose**: it must live OUTSIDE the synced GTD
  store so it never travels with the data it guards. If your Syncthing folder
  root is broader than `coordination/gtd` and could cover the default state dir,
  add the marker path to `.stignore`.
- It means "this node has been set up and previously had a real store", not
  "the store is currently populated".

## Override (genuine fresh init / restore drill / tests)

Set `SKOS_ALLOW_EMPTY_STORE=1` to bypass the guard when emitting onto an empty
store is intended. Use it for a truly first-time deployment where there is
nothing to restore, or inside a restore drill. In normal operation, leave it
unset.

## Quick reference

```
skos coldstart check         # preflight: exit 1 if the guard would trip
skos coldstart check --json  # machine-readable report
skos coldstart init          # stamp node-initialized (after restore)
skos coldstart init --force  # stamp even on an empty store (deliberate)
```
