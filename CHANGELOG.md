# Changelog

All notable changes to **skos** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · [SemVer](https://semver.org/).

## [Unreleased]

### Fixed
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

### Changed
- **`plan_wraps()` and `apply_wraps()` take an `effective` parameter**, defaulting to
  auto-detection, so callers and tests can inject or disable the systemd query. Plan
  entries gained `exec_source` (`effective` or `file`). Existing positional callers are
  unaffected.

### Added
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
