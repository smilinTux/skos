# Changelog

All notable changes to **skos** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **SK Ops dashboards data.** `skos ops sync` now also feeds "SK Work Flow"
  (coord cards by kind and column, the SK Work Deck board by stack, GTD lists by
  age bucket plus weekly added/done, ITIL tickets by type and status, memory
  files per agent and tier) and "SK Open Tickets" (open incidents and problems,
  changes awaiting approval). SK Token Spend gets a Month column, SK Fleet Hosts a
  Down flag, SK Jobs a Failing flag (so KPI sums show 0 instead of nothing), and SK Alerts
  gets derived columns (Opened day, Hours open, Active, Active crit); no existing value is touched.
  Rows without a Key (sk-alert "fyi" rows) are never deduplicated or pruned by the
  upsert. The Analytics reports and panoramas that draw these live in Nextcloud.

### Fixed
- **SK Work ticks were never seen.** Saving a card in Deck's editor rewrites
  `1.` as `1\.` and glues the next line onto the last option, so a ticked box
  did not match. The block is now normalized before parsing, and written with
  blank lines so the editor keeps its lines apart.

### Changed
- **SK Work approvals count as the CAB vote.** An approval by someone on the
  approvers list now also records their vote on the linked ITIL change
  (subject `nextcloud:<uid>`, role `approver`). On by default;
  `SKOS_WB_CAB_BRIDGE=0` turns it off. The agent account can still never approve.

### Fixed
- **Full reports in card threads.** "Ready for review" posts in Talk keep up to
  30000 characters instead of the first 1500.
- **Task triggers reach the whole backlog.** Tasks held back by the per-cycle
  or hourly option limit (or a model failure) are now retried, up to 8 per list
  per cycle with held-back picks first; before, a list's first sync gave
  options to the first 6 tasks only.

### Added
- **SK Work board** (`work_board.py`): the Deck board "SK Work" driven by the
  same skos-task-triggers loop. Triage cards get 3 next steps from the
  skcapstone suggestion engine as tick boxes; a tick (or `go N`) queues an
  AgentRun on the linked skcoord card and opens a Talk thread "Card: <title>";
  results come back to the thread, a card comment and a report attachment.
  Cards labelled `prod` stop at Needs approval: a check mark reaction or a move
  to Approved by an approver (`SKOS_WB_APPROVERS`) is recorded on the coord
  card, anyone else's move is put back. `python -m skos.work_board --migrate`
  brings recently active coord cards onto the board.
- **`skos ops sync`**: feeds three Nextcloud Tables for the SK Ops app
  (`skops_tables.py`): SK Fleet Hosts (fleet tree status + heartbeat), SK Jobs
  (sk-cron-run ledger tail, local and over ssh for `SKOPS_JOB_HOSTS`) and SK
  Token Spend (SKCounter collector `models` view, newest observation wins,
  incremental parse with a local cache, 90-day window). Rows upsert on a Key
  column and only changed values are written. Timer `skos-ops-sync` every 15
  minutes with resource limits.
- **`skos gtd mirror-tasks`** (pilot): one-way mirror of next actions,
  waiting-fors and projects into Nextcloud Tasks over CalDAV
  (`gtd_tasks_mirror.py`). Deterministic UIDs (`skgtd-<id>`) make reruns
  upserts; a task is rewritten only when its GTD item changed, so edits made
  in Tasks survive; a task completed in Tasks while still open in GTD is
  reported as `pending_writeback`; writes use If-Match / If-None-Match so a
  concurrent edit is a skipped conflict, not a lost write. Units:
  `deploy/systemd/skos-gtd-tasks-mirror.{service,timer}` (every 15 min).
- **Task triggers: Lumina proposes next steps on Nextcloud Tasks and works the
  one Chef picks** (`skos.task_triggers`, unit
  `deploy/systemd/skos-task-triggers.service`). Every open task in the GTD
  lists shared with her gets three next steps in a block she owns at the end of
  the notes. A plain-text pick (`go`, `go 2`, `options`, `@lumina ...`, a
  `go 2` title prefix, or a `GO-n` category) starts the work: a Talk thread
  "Task: <title>" in Chef's DM with the pick, a plan and a link to the task, and
  a Hermes run that posts progress and the result there. Status goes back to
  the task; a finished task closes its thread, and later picks reuse it.

### Fixed
- **The GTD spool now carries list mutations, not just captures.** On a
  non-owner host, `spool_mutation()` queues a clarify/move/done/reopen of an
  item (target list, allowed source lists, field patch, dropped fields) and
  `spool_append()` queues a whole built item (dreaming-engine someday-maybe
  entries), so the skcapstone GTD tools stop rewriting the lists there. The
  owner applies each op onto its current copy of the item on drain. Replay is
  a logged no-op: a missing item, an item outside the expected source lists,
  one whose mutation stamp is at or after the op's `ts`, or an append whose id
  is already stored is skipped. Unknown or malformed records are logged and
  skipped instead of being misread as captures.
- **`deploy/systemd/skos-gtd-drain.{service,timer}`**: the owner host's
  10-minute `skos gtd drain-spool`, so spooled captures land even when the
  owner itself is quiet.
- **Only one host rewrites the GTD store lists; every other host spools.** The
  lists are whole-file JSON in a Syncthing-shared folder, so two hosts saving
  `inbox.json` inside the sync window made Syncthing park one version as a
  `.sync-conflict` copy and its items vanished (six on 2026-10-27, noroc2027
  vs .41). A synced `OWNER` file (or `SK_GTD_OWNER_HOST`) names the owner;
  `capture()`/`upsert()` elsewhere append to `spool/<host>.jsonl`, and the
  owner folds the spools in under the store lock (`skos gtd drain-spool`).
- **The skbrain credential drop-in now exports the DSN names consumed by the
  projector and reader** (`SKBRAIN_PG_PROJECTOR_DSN` and
  `SKBRAIN_PG_READER_DSN`). The former doubled the `SKBRAIN_` prefix, leaving a
  successfully provisioned database unreachable to `skbrain sync`.
- **A successful committed skbrain projection now refreshes observation time
  for unchanged canon nodes.** Previously content-hash idempotency skipped all
  writes and the doctor reported a healthy, repeatedly verified projection as
  permanently stale. Dry runs still make no writes.
- **The built-in skbrain pack now gates on the versions that actually ship its
  required contracts** (`skcapstone>=0.15.18`, `skos>=0.2.2`, and
  `skmemory>=0.11.17.dev1`) instead of unreleased future versions. The live
  installer can therefore prove compatibility rather than requiring an
  unreviewable `--force` bypass.
- **The skbrain pack no longer invokes the nonexistent `skcapstone cmdb seed`
  command.** CMDB is an existing canonical store with its own migration and
  reconciliation lifecycle; fabricating a seed verb would create a second
  ownership path. KEDB seeding and the idempotent skbrain projection remain.
- **The service-unit limiter audit is now part of scheduler-as-code**, completing
  the 13-job managed block. The live migration moved the shared schedule
  credential into an owner-only runtime environment file, removed legacy
  duplicate wrapped jobs, and verified a clean manifest diff.
- **skos no longer re-tests skharness's autocode engine through its own shims**
  (card `ba782c14` follow-on). `skos.autopilot.{config,ci,claude_code}` are
  `from skharness.autocode.X import *` re-exports since Wave 2 Phase B of the autocode
  extraction, but skos kept copies of skharness's behaviour tests: 16 + 6 + 2 tests
  whose names all exist in skharness, which owns supersets (19/13/8) that pass. The
  copies added no coverage and guaranteed drift, and three had drifted far enough to
  turn CI red: `DEFAULT_HARNESS` became `"pi"` while the copy asserted `"claude-code"`,
  the claude-code argv changed, and `diff_coverage` was hardened (card `53b8c8be`/S21)
  to delete any pre-existing `coverage.xml` so a planted report can never be read as a
  measurement, which the copy planted and expected back. Replaced with
  `tests/test_autocode_shim_contract.py`, asserting the only thing a shim is
  responsible for: that every public engine name is re-exported and IS the engine's
  object. Suite goes 3 failed → 0 failed (1342 passed).
- **Two unused imports that failed the `ruff --select=...,F401 src tests` gate**:
  `timezone` in `src/skos/watchdog/adapters/email.py` (from WD-6, `f8d941a`) and `time`
  in `tests/test_watchdog_adapter_sites.py`. Unrelated to the above, but they fail the
  same CI selector, so fixing only the tests would have left the gate red regardless.
- **`timer_wrap` now wraps a unit's EFFECTIVE `ExecStart`, not its base fragment**
  (card `47e32514`). systemd's effective value is the base unit plus its drop-ins in
  lexical order, with a bare `ExecStart=` resetting the list. Reading only the base file
  silently discarded every flag a drop-in contributed, which is how the wrap reverted
  `skoperator.service` from `run --execute --honor` to report-only with no error, and it
  applied to any of the 30 wrapped user timers whose flags come from a drop-in.
  `plan_wraps()` now reads `systemctl --user show <unit>.service -p ExecStartEx --value`
  (falling back to `ExecStart`) and parses the `argv[]=` field of the last record.
- **`timer_wrap` falls back to the previous base-file read**, byte-for-byte, when
  `systemctl` is absent, the query fails or returns empty, the unit is a bare template
  (`foo@.service`, which systemd refuses to resolve), or the record carries an exec
  prefix that cannot be reproduced without guessing at privileges (`+`, `!`, `@`).
  systemd is consulted only when the target directory is one of systemd's own user unit
  directories, so a fixture tree is never answered from the live manager.
- **`wrap_command()` carries a `-` (ignore-failure) prefix ahead of the runner** instead
  of leaving it in argument position, where systemd would have passed it to the job as a
  literal argument.
- **`operator_explain()` and `operator_observe()` now derive their condition list from
  one ordered constant (`_CONDITIONS`)**, so `CmdbDriftBounded` (or any other condition)
  can never again be declared by `observe` but omitted from `explain` (card `105315b6`).
  Added `tests/test_ops_operator_cli.py` asserting the two verbs emit an identical,
  identically-ordered condition-type list.
- **`skbrain doctor`'s `run_checks()` now implements all 7 checks the signed manifest
  declares**, not just 5 (card `105315b6`). Added `skbrain:kedb` (reuses
  `compute_kedb_coverage`), `skbrain:adapter` (verifies the manifest's own
  `knowledge.retriever` actually imports/resolves), and `skbrain:cron` (verifies the
  pack's fleet CronJob objects are installed under `<SKCAPSTONE_HOME>/fleet/objects/`
  and byte-identical to the shipped templates). No manifest bytes were changed. Added
  `test_doctor_implements_every_manifest_declared_check`, which fails if the manifest
  ever again declares a check with no implementation.

### Changed
- **`plan_wraps()` and `apply_wraps()` take an `effective` parameter**, defaulting to
  auto-detection, so callers and tests can inject or disable the systemd query. Plan
  entries gained `exec_source` (`effective` or `file`). Existing positional callers are
  unaffected.

### Added
- **`skos.brain.ops.kedb_coverage`: real `KedbCanonCovered` implementation** (card
  `8c6e05e3`). Cross-references SKBrain canon known-error pages (`state_refs.kedb`)
  against the authoritative KEDB fold owned by `skcoord.itil.ITILManager` (re-exported
  as `skcapstone.itil`), read via the manager's public `search_kedb("")`. Reports real
  `True`/`False` with enumerable, bidirectional gaps (`missing_from_canon`,
  `dangling_canon_refs`) in `operator observe --json`'s `KedbCanonCovered.gaps`, and
  degrades to `Unknown` only when the authoritative fold is genuinely unreachable
  (optional skcapstone/skcoord sibling absent, ITIL dirs never initialized, or a
  permission error), always with an explicit reason. Previously this condition was
  hardcoded and never reflected real coverage.
- **Licence: GPL-3.0-or-later.** Full verbatim GPLv3 text in `LICENSE`, plus
  `license = {text = "GPL-3.0-or-later"}` and the OSI classifier in `pyproject.toml`.
  The project previously declared no licence field at all. Fleet-wide decision,
  2026-08-14.
- **`SOP.md`**: the repo-level 9-section SOP, with an architecture mermaid, a
  "Start here" entry-point list, a Symptom/Check troubleshooting table, and an
  executable `docs-evidence` block (10 hermetic checks pinning the entry points, the
  `127.0.0.1:7781` web defaults, the GET-only route set, the GTD store precedence, the
  `timer_wrap` effective-ExecStart read, the blocking ruff rule set, the scheduler config paths,
  and the vault-file key handling). It links to the existing subsystem SOPs rather than
  restating them.
- **`SECURITY.md`**: honest-claims posture (crypto tier **T0 classical**, symmetric-only
  Fernet, no asymmetric key material), an experimental/unaudited statement, threat model
  with in/out of scope, trust-root table, supported-versions table, GitHub private
  vulnerability reporting with a 72h acknowledgement SLA, and a safe-harbour line.
- **`CONTRIBUTING.md`** and **`CODE_OF_CONDUCT.md`** (Contributor Covenant 2.1).
- **`.github/workflows/docs-check.yml`**: the sk-standards docs freshness gate at
  tiers 1 and 2.
- **Fresh-node E2E gate for skbrain** (card `d17892ae`). `scripts/fresh-node-gate.sh`
  builds a genuinely clean-node container (fresh `$HOME`, no `~/.skcapstone`/
  `~/.skenv`, editable-installs skos + skmemory from staged repo copies), boots a
  separate ephemeral `skmem-pg` instance, and runs the real, unmocked
  `skos install skbrain` -> `skbrain doctor` -> `skbrain operator observe` path,
  captured by `tests/test_fresh_node_gate_it.py` (opt-in via `RUN_FRESH_NODE_GATE=1`;
  skipped by default since it needs docker + the skmem-pg image). Card f3cb6231 said
  this completion gate did not exist anywhere in the fleet; only a mocked-`Effects`
  unit test did. Evidence from a real run is captured in
  `docs/evidence/skbrain-fresh-node-gate-2026-08-23.json` and
  `docs/runbooks/skbrain-fresh-node-gate.md`. On this host the harness itself passes
  but the gate it runs correctly reports `gate_pass: false`: `content_repo` fails (no
  git remote configured in the pack manifest) and `CmdbDriftBounded`/
  `KedbCanonCovered` are still hardcoded `"Unknown"` in `src/skos/brain/ops/cli.py`.
  That is the accurate state of an unfinished feature, not a harness bug, and this PR
  does not weaken the gate to make it pass.
- Added `packaging` to `pyproject.toml` dependencies and documented that a fresh
  node needs `postgresql-client-17` (not Debian bookworm's default v15) to talk to
  the v17 skmem-pg server — both real gaps the fresh-node gate surfaced.

### Documented (no code change)
- **The `timer_wrap` base-file read**, its blast radius and its detection method, in SOP
  section 8. `scripts/sk-cron-run.sh` is not implicated; it runs `"$@"` verbatim. The
  code fix landed under card `47e32514` (see Fixed, above). ⚠️ That fix does not repair
  drop-ins an earlier version already wrote, so section 8 now also carries the ordered
  operator sequence for retiring the live `zz-honor.conf` workaround.
- **`test (autopilot extra)` is a no-op GREEN without the `SKHARNESS_TOKEN` secret**: the
  gate step skips every later step, so the job reports success having run zero tests.
  Called out in SOP section 4 and `CONTRIBUTING.md`.
- **README** now carries the grep-able `Status:` / `Maturity-tier:` / `License:` header
  lines the doc standard requires, plus the crypto posture pointer.

## [0.2.0]: 2026-07-03

### Added: Unified GTD (`gtd-ingest` subsystem)
- **`gtd-ingest` port + `capture()` sink** (`skos/gtd_ingest.py`): `GtdCapture`,
  `GtdSourceAdapter` (poll/emit/drain), `registry`. Dedup by `(source, source_ref)`;
  writes the shared skcapstone GTD store (delegates to `_gtd_dir()` when present).
  Registered `gtd-ingest` core capability (default `itil`; alternates email, cron,
  telegram, voice, calendar). Docs: `docs/gtd-ingest-architecture.md` + `docs/gtd-ingest-SOP.md`.
- **Pull adapters** (`skos/adapters/`): `calendar` (timed commitments → GTD, event-id
  dedup, noise-filtered) and `telegram` (`todo:`/`task:` DM convention → GTD,
  `chat:msg_id` dedup). Drain via `skos ingest <adapter>`.
- **Native CLI:** `skos status [email|cron|gtd|docs|corpus|all|report|corpus-check]`
  and `skos ingest <adapter>` (`skos/status.py` engine; `sk-status` is now a thin shim).
- **Reporting + observability** (operational scripts under `~/clawd/scripts/`):
  `gtd-mail.py` (email adapter, capture/triage/digest + bidirectional reply/done/
  attachments), `sk-status.py` shim, `sk-cron-run.sh` (run-ledger + failure→GTD+alert).
- **Bidirectional email:** reply (safe Gmail draft by default), done→archive+read,
  show-attachment (download + Telegram delivery).
- **Monitored pipelines + context:** corpus/wiki health + maintenance-ensure +
  research-queue threshold-capture; recent-docs context source (Drive; Nextcloud-ready).

- **Packaged the operational scripts into skos** (were untracked in `~/clawd/scripts`):
  `skos.mail` (email capture/triage/digest + bidirectional) with an `EmailAdapter`
  on the `gtd-ingest` port (`skos ingest email`); shell wrappers → `skos/scripts/`;
  console scripts `sk-status`, `gtd-mail`. Crontab rewired to the packaged paths.

### Changed
- **ITIL is now a push adapter** on the `gtd-ingest` port (skcapstone `itil.py::_gtd_emit`
  emits `GtdCapture(source=itil, source_ref=<id>)` through the sink; legacy fallback).
- Pinned **`typer==0.12.5`** (click 8.1 compatibility, un-breaks the `skos` CLI).

### Tests
- `tests/test_gtd_ingest.py` (6) + `tests/test_adapters.py` (9); skcapstone ITIL/GTD (6). All green.
