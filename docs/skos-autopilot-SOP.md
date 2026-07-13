# SKOS Autopilot - Standard Operating Procedure

Companion to the design spec `skos-autopilot-architecture.md`. This is the
build / test / run / config / promotion / troubleshoot reference for operating
Autopilot v1.

## 1. What it is

A scheduled autonomy plane that reads work off the SKOS inputs (coord board
first), routes each item to a work-type executor, grades to a per-executor
quality gate, and reports what it did plus what needs a human decision as a
numbered digest the operator answers with a single number. GTD stays the spine;
Autopilot never invents a side store.

## 2. v1 posture: harness live-execution is DISABLED

By operator decision (after a security review, 2026-07-12), v1 ships with the
Claude Code harness HARD-DISABLED from live execution (posture C). v1 runs the
orchestration, routing, coord-scoring, and digest machinery in dry-run against a
deterministic `StubHarness` that never spawns a model. Canary and live modes
return a "disabled in v1" message. The real sovereign sandbox (confined Bash,
pinned egress) is the v1.5 gate, coord `07c78c7f`. Do NOT set the adapter's
`live_execution=True` until v1.5 completes and its integration test proves a
confined session cannot read secrets or reach an off-allowlist host.

## 3. Build and install (skcapstone first)

1. Land and install the skcapstone coord foundations on .158 (noroc2027):
   `cd ~/clawd/skcapstone-repos/skcapstone && pip install -e .`
   (Task.meta, Board.score_task/update_task/close_task_obsolete, unblocked +
   stale-claim helpers, coord score CLI + coord_score MCP). Hard prerequisite.
2. Install skos: `cd ~/clawd/skos && pip install -e .`
3. Confirm the CLI group: `skos autopilot --help`.

## 4. Test

- skos: `cd ~/clawd/skos && python -m pytest tests/ -q`
- skcapstone: `cd ~/clawd/skcapstone-repos/skcapstone && python -m pytest tests/test_coordination.py -q`
- The real-harness seam test spawns `claude -p` against a throwaway fixture repo
  and is gated behind an env flag (`RUN_HARNESS_IT`) so a box without the Claude
  binary skips it. It stays skipped by default and does not run live in v1.

## 5. Configure

- Config file: `~/.skcapstone/config/autopilot.yaml` (copy from
  `~/clawd/skos/docs/examples/autopilot.yaml`). Fields: `enabled`, `harness`,
  `allowed_tools`, `repo_map`, `automerge_repos`, `caps`, `digest_chat`,
  `dry_run_summary`, `epic_id`. Start with `enabled: false`.
- `repo_map` keys use the `repo:<name>` tag convention. A coord task is
  engineering-eligible only if it carries exactly one known `repo:<name>` tag.
- `automerge_repos` stays EMPTY. In v1 nothing auto-merges (posture C). Even
  post-v1.5, a repo auto-merges only when it is BOTH in `automerge_repos` AND
  `repo_map[name].automerge: true` AND `ci` is not `none` AND external CI is green.
- Digest DM target: `digest_chat` (Chef's DM). Delivered via `sk-alert`.

## 6. Run and promote (v1: dry-run only)

- `skos autopilot run --once` (dry-run is the default). Genuinely read-only: no
  coord/GTD writes, no merges, no DM beyond one opt-in summary. Runs against the
  StubHarness, reports what it would do to the run journal.
- `skos autopilot run --no-dry-run` / `--canary`: return "disabled in v1
  (posture C)" and do nothing. These come online in v1.5 after the sandbox.

Scheduled daily run: the `autopilot-daily` block for
`~/.skcapstone/config/jobs.yaml` (schedule 30 6 * * *, node noroc2027,
flock-guarded, sk-cron-run wrapped, retries 0, catchup false). Generate it with
`python -c "from skos.autopilot import config; print(config.render_autopilot_job_yaml())"`,
paste under `jobs:`, and verify with `skcapstone scheduler list | grep autopilot-daily`.

## 7. Answer the morning digest

`skos autopilot answer <n> [response]` (for example `skos autopilot answer 1 yes`).
The resolver is idempotent: re-answering a number is a no-op. The Telegram reply
door is v1.5.

## 8. Inspect

- `skos autopilot status` - latest run (phase, items, scores, tokens/cost, PRs).
- `skos autopilot list [--decisions|--runs|--claims]`.
- `skos autopilot show <run_id>` - one run's per-item trajectory.
- `skos autopilot send [--preview]` - rebuild and send (or preview) the digest.

## 9. Revert

`skos autopilot revert <task_id>` reverts the recorded merge commit and reopens
the coord task. Only meaningful once auto-merge is enabled (v1.5+); in v1 nothing
auto-merges so there is nothing to revert.

## 10. Kill switch and ceilings

- Kill switch: set `enabled: false` in autopilot.yaml, or export
  `SKOS_AUTOPILOT_OFF=1`. Checked at the top of every phase and before every
  finalize; the current run stops cleanly at the next checkpoint.
- Hard ceilings: `caps.max_tokens_per_run`, `caps.max_usd_per_day`. On breach
  Autopilot stops selecting new work, escalates a "budget hit" digest item, exits.
- Board-flood control: `caps.new_tasks_per_run` (default 10); deep-dive tasks are
  tagged `autopilot-untriaged` and are never auto-selected.

## 11. Single-node constraint (hard)

All coord task-file mutation (score_task, update_task, obsolete-close) is safe
ONLY because `autopilot-daily` is pinned to `nodes: [noroc2027]`. A second
concurrent task-file writer, or unpinning the node, reintroduces the Syncthing
write-conflict class the coordination design eliminates. The same flock lock path
is taken by every entry point, including a manual `skos autopilot run`, so an
ad-hoc run cannot overlap the scheduled one.

## 12. Troubleshoot

- Nothing selected: check tasks carry exactly one known `repo:<name>` tag, are
  unblocked (every dependency in some agent's completed_tasks), and are not tagged
  `autopilot-untriaged`.
- No digest DM: confirm `sk-alert` sends (token in `~/.hermes/.env`) and
  `digest_chat` is set; in dry-run the DM is suppressed unless `dry_run_summary`
  is opted in.
- Wedged task: a crashed run's claim is a lease; Phase 0 of the next run releases
  an autopilot-claimed uncompleted task older than `run_timeout` and prunes its
  worktree.
- "disabled in v1" on run: expected for `--no-dry-run`/`--canary` under posture C.

## 13. References

- Design: `skos-autopilot-architecture.md` (section 12 = harness security model,
  section 21 = adopted Archon patterns).
- Coord scoring: `../../skcapstone-repos/skcapstone/docs/autopilot-coord-scoring.md`.
- v1.5 sovereign sandbox: coord `07c78c7f`. Archon-leverage epic: coord `49a74ed2`.
