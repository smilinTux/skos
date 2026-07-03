# Changelog

All notable changes to **skos** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · [SemVer](https://semver.org/).

## [Unreleased]

### Added — Unified GTD (`gtd-ingest` subsystem)
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
  `gtd-mail.py` (email adapter — capture/triage/digest + bidirectional reply/done/
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
- Pinned **`typer==0.12.5`** (click 8.1 compatibility — un-breaks the `skos` CLI).

### Tests
- `tests/test_gtd_ingest.py` (6) + `tests/test_adapters.py` (9); skcapstone ITIL/GTD (6). All green.
