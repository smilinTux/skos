# Changelog

All notable changes to **skos** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Licence: GPL-3.0-or-later.** Full verbatim GPLv3 text in `LICENSE`, plus
  `license = {text = "GPL-3.0-or-later"}` and the OSI classifier in `pyproject.toml`.
  The project previously declared no licence field at all. Fleet-wide decision,
  2026-08-14.
- **`SOP.md`**: the repo-level 9-section SOP, with an architecture mermaid, a
  "Start here" entry-point list, a Symptom/Check troubleshooting table, and an
  executable `docs-evidence` block (10 hermetic checks pinning the entry points, the
  `127.0.0.1:7781` web defaults, the GET-only route set, the GTD store precedence, the
  `timer_wrap` base-file read, the blocking ruff rule set, the scheduler config paths,
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
- **`timer_wrap._execstart_of()` reads the base `.service` file only** and never the
  effective post-drop-in `ExecStart`, so wrapping a unit silently discards flags that
  other drop-ins contributed. This reverted `skoperator.service` from
  `run --execute --honor` to report-only with no error, and it generalises to any of the
  wrapped user timers whose flags come from a drop-in. `scripts/sk-cron-run.sh` is not
  implicated; it runs `"$@"` verbatim. SOP section 8 carries the detection method and the
  live `zz-honor.conf` workaround. The code fix is owned by card `47e32514`.
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
