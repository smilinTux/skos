# Runbook: skwatchdog 07:45 schedule cutover (WD-4)

Card `2405db76` (parent epic `eeac09f7`). This is the flip that makes the daily
07:45 Hermes DM the skwatchdog fleet-narrative digest instead of the plain
`sk-status report` counts message. **Chef gets exactly one morning DM after
this lands, not two.**

For the general scheduler mechanism (load/validate/render/diff/install), see
[`skos-scheduler.md`](./skos-scheduler.md). This runbook covers only the
one-job cutover and its rollback.

## What changed

One job in `deploy/schedule/jobs.yaml`, the `ops-report` job at 07:45. Only
the `command:` line moved:

```diff
   - name: ops-report
     schedule: "45 7 * * *"
-    command: "$HOME/.skenv/bin/sk-status report"
+    command: "$HOME/.skenv/bin/skos watchdog digest"
     log: "$HOME/.skcapstone/logs/sk-status.log"
```

Nothing else in the schedule moved. Same job name, same time, same log file,
same 11 other jobs untouched.

**`sk-status` is not removed.** It is still installed, still has a `report`
command, still documented in the gtd-ingest SOP. It simply stops being the
thing that DMs Chef directly. The watchdog's `itil`, `fleet`, and `scheduler`
source adapters call into the same counts machinery `sk-status` exposes; the
digest is a superset view, not a replacement engine.

## What `skos watchdog digest` does

Collects every registered watchdog source (fleet events, scheduler/cron
health, ITIL incidents/problems, coord/autocode, Atlas, git), assembles one
deterministic digest, renders a headline through skgateway (falls back to a
template if skgateway is down), publishes JSON + Markdown to
`~/.skcapstone/watchdog/digests/` (dated + `latest/`, same pattern as the
Atlas brief), then sends ONE Hermes DM. A broken or unreachable source
degrades to a noted gap in that source's section; it never blocks the digest
from publishing or sending. See `src/skos/watchdog/run.py` for the exact
order of operations (collect, assemble, render, publish, advance cursors,
send).

## Verify it is live

```bash
cd ~/clawd/skos
skos schedule diff              # expect clean once installed
crontab -l | grep 'ops-report'  # should show `skos watchdog digest`, not sk-status report
```

Run it by hand any time, safely, without sending anything or moving a cursor:

```bash
~/.skenv/bin/skos watchdog digest --dry-run --no-send
```

That previews the full Markdown digest (headline, Problems, Notable, Sources)
with nothing published and no DM sent. To publish without DMing (recovers a
lost DM without re-sending, since cursors will have already advanced):

```bash
~/.skenv/bin/skos watchdog digest --no-send
```

## Deploy

Same routine deploy flow as every other scheduler change, nothing special for
this cutover:

```bash
cd ~/clawd/skos
git pull && pip install -e .
skos schedule diff              # see the one job that will change
skos schedule install --apply   # replace-in-place; idempotent
```

## Rollback (do this half-asleep, on a phone, if the digest misbehaves)

**One line, in `deploy/schedule/jobs.yaml`.** Change the `ops-report` job's
`command:` back to:

```yaml
    command: "$HOME/.skenv/bin/sk-status report"
```

Then apply:

```bash
cd ~/clawd/skos
skos schedule install --apply
```

That is the entire rollback. Nothing else in the file changes, no other job
moves, and `sk-status report` still works today exactly as it did before this
card because it was never touched.

If you cannot get to a git checkout right now (worst case, on a phone, no
laptop), edit the crontab directly instead:

```bash
crontab -e
# find the line inside the "# >>> skos schedule (managed ...) >>>" block that
# runs `skos watchdog digest` at 07:45, replace it with `sk-status report`
```

This is a stopgap only; the next `skos schedule install --apply` from the
repo will overwrite it back to whatever `jobs.yaml` says, so follow up with
the real `git` rollback above once you are at a keyboard.

## Hard rules this cutover does not touch

- No dispatch, no GTD upsert, no coord card creation from the digest. Those
  are WD-8 and WD-9, both feature-flagged, both later. This is Phase 1,
  report-only.
- No precondition was added on the job. The digest sends even when a source
  is broken (proven in WD-3); adding a "only run if healthy" guard here would
  regress that and is explicitly out of scope.
- The env vars the job needs (`HERMES_DM`, `SKGATEWAY_URL`, `SKGATEWAY_MODEL`)
  are resolved the same way `sk-status report` already resolves `HERMES_DM`:
  from `~/.skcapstone/skos-schedule.env` (or the process environment), never
  hardcoded. `SKGATEWAY_URL` defaults to `http://localhost:18780/v1` and
  `SKGATEWAY_MODEL` to `sk-default` if unset; a missing/unreachable gateway
  only degrades the headline to its deterministic template, it does not stop
  the digest.

## Troubleshooting

| Symptom | Check |
|---|---|
| Two DMs arrive at 07:45 | Some other job is still calling `sk-status report` directly, outside `jobs.yaml` (a stray personal crontab line, or the managed block was not re-applied after this change). `crontab -l \| grep 'sk-status report'` should return nothing once cutover is applied. |
| No DM arrives at 07:45 | `skos watchdog digest` by hand and read the output; if it publishes but `sent: False`, check `hermes send` creds / `HERMES_DM` per the SOP troubleshooting table. The published `latest/` artifact is the digest of record even if the DM ping is lost. |
| Digest looks thin / missing a source | Check the `## Sources` footer in the rendered digest; a source marked not-ok degraded gracefully rather than failing the run. That is by design (WD-2/WD-3), not a bug to "fix" by blocking the job. |
| Headline reads like the deterministic template, not prose | skgateway (`SKGATEWAY_URL`) is down or slow; the digest still sent on time, this is expected fallback behavior, not a failure. |
| `skos schedule diff` shows `changed` for `ops-report` after this lands | Manifest and live crontab disagree; re-run `skos schedule install --apply`. |
