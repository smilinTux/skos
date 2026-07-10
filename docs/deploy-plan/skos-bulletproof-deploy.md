# skos Bulletproof Deployment Plan

Date: 2026-07-10
Repo: `~/clawd/skos` (GitHub: smilinTux/skos, PUBLIC)
Branch at time of writing: `ci/github-actions` at `692e18c`, 5 commits ahead of `origin/ci/github-actions`, 10 ahead of `origin/main` (v0.2.0), plus one uncommitted fix in `src/skos/status.py`.

Definition of bulletproof used here: reproducible from scratch on a new machine, secrets never in git, HA with no single point of failure ("if you need one, get two"), CI-gated, observable, self-recovering, and documented well enough that a cold machine can stand it up.

## 1. Current State (honest)

The code is in good shape; the deployment is not.

What is genuinely strong:

- Clean ports/adapters design: one `capture()`/`upsert()` sink deduped by `(source, source_ref)`; adding a source is one adapter class.
- 312 tests passing in under 2 seconds (verified locally), with real test-per-module discipline covering the sink, adapters, resolver, renderers, secrets plane, and surfaces.
- The `sk-cron-run.sh` observability pattern (run-ledger JSONL, failure to GTD capture, sk-alert on every job) is a solid reference implementation of the observability standard.
- Documentation is exceptional: a 9-section SOP with a real troubleshooting table, architecture spec, model-registry doc, and an honest SECRET-MIGRATION.md ledger.
- A secrets plane abstraction already exists in-code (`src/skos/secrets/` with capauth and vault_file backends plus conformance tests), so the hardcoded credentials have a ready home.

What is broken or missing:

- The gog keyring password VALUE is committed to this public repo in `scripts/gtd-triage.sh`, `src/skos/status.py`, `src/skos/mail.py`, and `docs/gtd-ingest-SOP.md` (verified). That password unlocks OAuth tokens for 5 Gmail accounts. It is also inlined in the live crontab on .158.
- Personal PII is committed: 5 personal Gmail addresses, Chef's Telegram chat id, and LAN plus tailnet IPs across `mail.py`, `status.py`, `adapters/calendar.py`, `adapters/telegram.py`, `models/registry.example.yaml`, and the SOP.
- CI has never been green: the only GitHub Actions run ever (v0.2.0 push, 2026-07-03) failed on lint. `ci.yml` triggers only on `main` (verified), so the 10 commits on `ci/github-actions` never ran CI, and the blocking ruff select currently reports 26 errors locally (verified: `ruff check --select=E9,F63,F7,F82,F401 src tests`). Merging today turns main red.
- The entire production pipeline is a hand-edited user crontab on one host (.158). No crontab file, systemd units, or playbook in the repo. A rebuild of .158 loses the whole schedule.
- The GTD JSON store writes non-atomically (verified: `_save()` is a bare `write_text`, no tmp+rename, no lock), `_load()` swallows decode errors and returns `[]`, and `upsert()` does two separate saves when moving items. Three independent writers race on the same files. One corrupt write silently empties a list and Syncthing replicates the empty file fleet-wide. There is no backup; skbackup is "planned" intent only.
- Fresh install is broken by our own docs: `pyproject.toml` declares `typer>=0.12` (verified) while the SOP says newer typer breaks the CLI and must be pinned to 0.12.5. `ruamel.yaml` is used by skmodels but not declared. README advertises `curl skos.skworld.io/install.sh | sh`; no `install.sh` exists in the repo (verified).
- 5 commits of shipped-and-in-production code (the order/upsert stateful-adapter feature) exist only on the .158 disk, plus the uncommitted `status.py` fix.
- All alerting originates from the same host as the jobs. A down .158 is silent.

## 2. Target: what bulletproof means for skos

Concretely, skos is bulletproof when all of the following hold:

1. **Secrets never in git.** No credential value or PII in any tracked file or in git history. Runtime credentials resolve through the existing `src/skos/secrets/` plane (capauth or vault_file) or environment injected by the scheduler unit, never hardcoded defaults. A secret-scanning CI job blocks regressions.
2. **Reproducible from scratch.** `git clone` plus one documented bootstrap command on a cold machine yields a working `skos` CLI (correct pins, all deps declared), the full cron/timer schedule installed from in-repo artifacts, and the SOP verified against that path. No step depends on hand-edited state on .158.
3. **CI-gated.** CI runs on every push and PR (all branches), the blocking lint select passes, all 312+ tests pass, and main is protected so red cannot merge. Green bar means something.
4. **Durable data.** GTD store writes are atomic (tmp+fsync+rename) and locked across all writers; a corrupt or interrupted write can never empty a list. Point-in-time backups (skbackup materialized, restic or equivalent) cover the GTD store, cron ledger, and model registry, independent of Syncthing replication.
5. **No single point of failure.** A dead-man's switch external to .158 alerts when the pipeline stops reporting. Scheduler artifacts are deployable to a standby host (.41) with a written failover runbook. Losing .158 loses at most one backup interval of data and zero code (everything pushed).
6. **Observable and self-recovering.** Every scheduled job continues to write the run ledger and alert on failure; additionally the ledger rotates, chatty jobs cannot blow shell limits, and a missing scheduled run (not just a failing one) raises an alert.
7. **Documented for a cold start.** The SOP's deploy section describes installing the committed schedule artifacts, not `crontab -e`; the README's install instructions actually work.

## 3. Gap Analysis (severity-ordered)

| # | Severity | Area | Gap |
|---|----------|------|-----|
| 1 | critical | secrets | GOG keyring password value committed in 4 tracked files in a PUBLIC repo (gtd-triage.sh:16, status.py:19, mail.py:35, gtd-ingest-SOP.md:127) and inlined in the live crontab. Guards OAuth tokens for 5 Gmail accounts. Needs rotation plus history scrub, not just removal. |
| 2 | high | secrets/PII | 5 personal Gmail addresses, Telegram chat id 1594678363, LAN and tailnet IPs (incl. mail.py:74 hardcoding 100.81.238.58 as the LLM default) committed across source, example registry, and SOP. |
| 3 | high | CI | Only CI run ever is red; ci.yml triggers only on main so feature branches never run CI; blocking ruff select fails with 26 errors right now; no branch protection value until fixed. |
| 4 | high | GTD store durability | Non-atomic `_save()`, error-swallowing `_load()` that returns `[]`, two-save `upsert()` move, and 3 racing writers (skos sink, sk-cron-run.sh inline Python, skcapstone gtd_tools). One bad write silently wipes a list and Syncthing propagates the wipe. |
| 5 | high | backup | No backup of GTD store, cron ledger, or model registry. Syncthing is replication, not backup. skbackup capability recorded as "planned" only, never materialized. |
| 6 | high | scheduler reproducibility | Production schedule lives only in the .158 user crontab; nothing in the repo reconstructs it. sk-cron-run.sh hardcodes host `noroc2027` and the absolute interpreter path; status.py:115 hardcodes the same path. |
| 7 | high | install reproducibility | typer>=0.12 unpinned despite SOP's own warning that newer typer breaks the CLI; ruamel.yaml undeclared (skmodels silently degrades); README advertises a nonexistent install.sh. Documented bootstrap does not work from scratch. |
| 8 | medium | unpushed work | 5 production commits plus a dirty status.py fix exist only on the .158 disk. |
| 9 | medium | HA / SPOF | Everything (jobs, ledger, alerting) on .158; a down .158 is a silent failure with no external dead-man's switch and no failover host. |
| 10 | medium | model registry | skos.models resolver and skmodels CLI have zero test coverage (verified: no test references skos.models) despite precedence rules and sk-auto semantics; live registry has no schema validation and no backup. |
| 11 | medium | observability holes | cron-ledger.jsonl unbounded; sk-cron-run.sh captures full job output into a shell variable; alerts are fire-and-forget; no alert-on-missing-run; skmon not materialized. |
| 12 | low | CI workflow quality | No pip caching, no coverage report, no concurrency group, deprecated action versions (checkout@v4, setup-python@v5 on Node 20), no secret-scanning job. |
| 13 | low | repo hygiene | Stray .bak-pre file in the tree, __pycache__ litter, local main stale relative to origin/main. |

## 4. Remediation Roadmap

Phases are ordered; items inside a phase marked [P] can run in parallel with each other.

### Phase 0: Stop the bleeding (same day)

Nothing else matters while a live credential sits in a public repo and production code exists on one disk.

- **0a. Rotate the gog keyring password and remove the value everywhere** (code, SOP, live crontab lines). Route runtime resolution through the secrets plane or an env file readable only by the scheduler. Depends on nothing.
- **0b. [P] Push the 5 unpushed commits and commit the status.py fix.** Depends on nothing; trivially parallel with 0a.

### Phase 1: Public-repo cleanup

- **1a. Remove PII from tracked files** (emails, chat id, IPs) into per-agent config or the secrets plane; sanitize registry.example.yaml and the SOP. Can start immediately; independent of 0a.
- **1b. Scrub git history** (git filter-repo) for the password and PII, force-push, invalidate clones. Must follow 0a (rotate first, scrub second) and should follow 1a so the scrub covers everything at once. Update SECRET-MIGRATION.md to include skos itself.

### Phase 2: Make the CI gate real

- **2a. Fix the 26 blocking ruff errors, extend ci.yml triggers to all branches/PRs, confirm a green run on GitHub, then enable branch protection on main.** Independent of Phase 1; [P] with 1a.
- **2b. Add a secret-scanning job (gitleaks) to CI.** After 1a/1b so it starts green; after 2a so it lands on a green pipeline.

### Phase 3: Data durability

- **3a. Atomic writes plus locking in gtd_ingest.py** (tmp+fsync+rename, flock around load-modify-save, single-transaction upsert move, loud failure instead of silent `[]` on decode errors). Independent; [P] with Phase 2.
- **3b. Unify all writers on the library path**: replace sk-cron-run.sh's inline-Python capture with a `skos` CLI call so locking actually covers every writer. Depends on 3a.
- **3c. Materialize skbackup**: scheduled restic (or equivalent) point-in-time backups of the GTD store, cron ledger, and model registry, with a tested restore procedure. Independent; [P] with 3a.

### Phase 4: Reproducible install and deploy

- **4a. Fix dependency declarations**: pin typer to the known-good version, declare ruamel.yaml, verify a from-scratch venv install produces a working CLI.
- **4b. Scheduler as code**: commit the full schedule (crontab file or systemd user timers) plus an idempotent install/reconcile script; parameterize the hardcoded host and interpreter paths in sk-cron-run.sh and status.py. Cut .158 over to the committed artifacts.
- **4c. Real install.sh plus SOP/README updates** so the documented cold-start path is the tested one. Depends on 4a and 4b.

### Phase 5: HA and observability hardening

- **5a. Dead-man's switch external to .158**: pipeline heartbeats to a second box (or external monitor) that alerts when heartbeats stop. Depends on 4b (needs the schedule as code to hook into).
- **5b. Standby deployment on .41**: install the committed scheduler artifacts disabled on .41 plus a written failover runbook. Depends on 4b.
- **5c. Observability holes**: ledger rotation, stream job output to a temp file instead of a shell variable, alert-on-missing-run detection. Depends on 4b (touches the same wrapper).
- **5d. [P] Model registry tests and schema validation** for skos.models precedence and sk-auto semantics. Independent.

### Phase 6: Polish

- **6a. CI workflow quality**: pip caching, action version bumps, concurrency group, coverage report. Depends on 2a.
- **6b. Repo hygiene**: remove the stray .bak file, ensure pycache exclusions, fast-forward local main.

## 5. Task List

Mirrors the tasks returned to the coordinator. Order within priority is the recommended execution order.

| Task | Priority | Depends on |
|------|----------|------------|
| skos: rotate gog keyring password and remove the value from repo, SOP, and live crontab | critical | none |
| skos: scrub secret and PII from git history and update SECRET-MIGRATION.md | critical | rotation task, PII removal task |
| skos: push unpushed commits and commit the status.py fix | high | none |
| skos: remove committed PII into config and the secrets plane | high | none |
| skos: fix blocking ruff errors and make CI run on all branches, then protect main | high | none |
| skos: make GTD store writes atomic and locked | high | none |
| skos: materialize skbackup for GTD store, cron ledger, and model registry | high | none |
| skos: commit the scheduler as code and cut .158 over to it | high | none |
| skos: fix dependency pins and declarations for a working from-scratch install | high | none |
| skos: unify all GTD writers on the locked library path | medium | atomic writes task |
| skos: add gitleaks secret scanning to CI | medium | rotation, PII removal, CI green tasks |
| skos: ship a real install.sh and align README and SOP with the tested cold-start path | medium | dependency pins task, scheduler-as-code task |
| skos: external dead-man's switch for the .158 pipeline | medium | scheduler-as-code task |
| skos: standby scheduler deployment on .41 with failover runbook | medium | scheduler-as-code task |
| skos: harden sk-cron-run.sh observability (rotation, output streaming, missing-run alerts) | medium | scheduler-as-code task |
| skos: test coverage and schema validation for the model registry | medium | none |
| skos: CI workflow quality pass (caching, action bumps, concurrency, coverage) | low | CI green task |
| skos: repo hygiene sweep | low | none |
