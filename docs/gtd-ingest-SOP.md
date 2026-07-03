# gtd-ingest — Standard Operating Procedures

The skos **unified-GTD subsystem**: one GTD store, one `capture()` sink, pluggable
source adapters (email · ITIL · cron · calendar · telegram), read-only context
sources (recent docs), monitored pipelines (corpus/wiki), bidirectional email, and
two daily digests. This SOP is the operational source of truth; the design/spec is
[`gtd-ingest-architecture.md`](./gtd-ingest-architecture.md).

Maturity: **T2 (working, in daily production on `noroc2027`)** · Phase 0–3 delivered.

---

## 1. Overview

**Purpose.** Make skos Chef's one GTD for all work: every input is captured into a
single store through one sink, surfaced in daily reports + on-demand status, and
acted on (reply / close / show attachment). Adding a source is one adapter class.

**Owns:** the `gtd-ingest` port + `capture()` sink (`skos.gtd_ingest`), the pull
adapters (`skos.adapters.*` — email/calendar/telegram), the realtime status + report
engine (`skos.status`), the email module (`skos.mail`), and the shell wrappers in
`skos/scripts/`. CLIs: `skos`, `sk-status`, `gtd-mail` (packaged console scripts).

**Does NOT own:** the GTD *item lifecycle verbs* (clarify/next/review) — those stay
in skcapstone's `gtd_tools` MCP; this subsystem only *captures into* and *reports
on* the shared store. It also does not send email without an explicit `--send`
(default is a reviewable draft).

---

## 2. Architecture

```mermaid
flowchart LR
  subgraph Sources
    MAIL[email x5]:::c
    ITIL[ITIL]:::c
    CRON[cron failures]:::c
    CAL[calendar]:::c
    TG[telegram]:::c
    DOCS[recent docs]:::x
    WIKI[corpus/wiki]:::m
  end
  MAIL & ITIL & CRON & CAL & TG --> SINK[["capture() sink<br/>dedupe by (source,source_ref)"]]
  SINK --> STORE[( unified GTD store<br/>~/.skcapstone/coordination/gtd/*.json )]
  DOCS & WIKI -. read-only .-> RPT
  STORE --> RPT[Ops Report 07:45]
  STORE --> BRIEF[Email Brief 06:45]
  LEDGER[(cron-ledger.jsonl)] --> RPT
  BRIEF & RPT --> HERMES[Hermes/Telegram DM]
  STORE -. bidirectional .-> ACT[reply · done→archive · show attachment]
  classDef c fill:#123,stroke:#3af; classDef x fill:#132,stroke:#3fa; classDef m fill:#231,stroke:#fa3;
```

**Start here (entry-point files):**
- `skos/src/skos/gtd_ingest.py` — the port: `GtdCapture`, `capture()` sink, `GtdSourceAdapter`, `registry`.
- `skos/src/skos/adapters/{email,calendar,telegram}.py` — pull adapters (`poll()`→captures).
- `skos/src/skos/status.py` — status/report engine (email/cron/gtd/docs/corpus).
- `skos/src/skos/mail.py` (`gtd-mail`) — email capture/triage/digest + bidirectional (reply/done/attachments).
- `skos/scripts/sk-cron-run.sh` — the cron/observability wrapper (run-ledger + failure→GTD+alert).
- `skcapstone/src/skcapstone/itil.py::_gtd_emit` — ITIL push adapter (emits through the sink).

**Store:** plain JSON under `$SK_DATA_ROOT/coordination/gtd/`
(`inbox/next-actions/projects/waiting-for/someday-maybe/archive.json`), Syncthing-synced.
The sink resolves the dir via skcapstone's `_gtd_dir()` when present, so it is
byte-identical to what the GTD/ITIL tools read (`SK_GTD_DIR` overrides).

---

## 3. Build

No build artifact — Python, editable-installed into `~/.skenv`:
```bash
cd ~/clawd/skos && pip install -e .        # provides `skos`, importable `skos.*`
```
Runtime deps used by adapters (already present): `gog` (Gmail/Calendar/Drive),
`skcapstone telegram` (Telethon), the local LLM at `.100:8082` (triage) / mxbai
(embeds), `hermes` + Telegram bot token (`~/.hermes/.env`) for delivery.
**Pin:** `typer==0.12.5` (click 8.1 compat — newer typer breaks the whole CLI).

---

## 4. Test

```bash
cd ~/clawd/skos && python -m pytest tests/test_gtd_ingest.py tests/test_adapters.py -q   # 15 tests
cd ~/clawd/skcapstone-repos/skcapstone && python -m pytest tests/ -k "itil or gtd" -q     # 6 tests
```
Green-bar gate: capture dedup, adapter registration, telegram parser/trigger,
calendar noise-filter, ITIL→sink routing + unified-store alignment.

---

## 5. Release / Deploy

**Front-end / Exposure: N/A — no network listener.** Runs as local cron jobs on
`noroc2027` (.158); the only outbound is Gmail (gog), the local LLM, and the
Telegram DM (Hermes bot). No public port.

**The daily pipeline** (all wrapped in `sk-cron-run` → run-ledger + failure→GTD+alert):

| Time (EST) | Job | Action |
|---|---|---|
| 04:45 | `wiki-maintain` | qwen drafts stubs for dangling wiki links |
| 05:30 | `youtube-ingest` | corpus ingest (wrapped) |
| 06:00 | `gtd-noise-sweep` | `gtd-triage.sh` — promotions/social/updates → `3 Read` + archive |
| 06:05 | `corpus-check` | `sk-status corpus-check` — wiki research-queue > threshold → GTD Action |
| 06:10 | `email-triage` | `gtd-mail triage` — LLM classifies primary mail → GTD labels + archive |
| 06:15 | `ingest-calendar` | `skos ingest calendar` → GTD |
| 06:16 | `ingest-telegram` | `skos ingest telegram` → GTD |
| 06:35 | `ingest-email` | `skos ingest email` — `1 Action`/`2 Waiting` labels → GTD (sink) |
| 06:45 | `email-brief` | `gtd-mail digest` — 📬 Email Brief → Telegram DM |
| 07:45 | `ops-report` | `sk-status report` — 📊 Ops Report → DM |

Every command runs under `skos/scripts/sk-cron-run.sh <job> …`.

Deploy/rollback = edit `crontab -e` (jobs are idempotent + deduped; safe to re-run).

---

## 6. Configuration / Usage

Env vars (secrets sourced from existing stores — never inlined):

| Var | Default | Used by |
|---|---|---|
| `GOG_KEYRING_PASSWORD` | `sk2026` | all gog calls |
| `SK_GTD_DIR` | (skcapstone `_gtd_dir()`) | sink store override |
| `HERMES_DM` | `telegram:1594678363` | digest delivery |
| `GTD_LLM_URL` / `GTD_LLM_MODEL` | `.100:8082/v1/...` / qwen3.6-27b-abliterated | email triage |
| `WIKI_RESEARCH_THRESHOLD` / `WIKI_DANGLING_THRESHOLD` | 650 / 5000 | corpus-check |
| `GTD_CAL_ACCOUNTS` / `GTD_CAL_DAYS` | primary / 2 | calendar adapter |
| `GTD_TG_CHAT` / `GTD_TG_LIMIT` | Chef's DM / 25 | telegram adapter |
| `GTD_DOC_ACCOUNTS` / `GTD_DOC_DIRS` | all 5 / — | recent-docs (Nextcloud roots) |

**Telegram capture convention:** DM a message prefixed `todo:` / `task:` / `gtd:` /
`remind:` — the rest becomes a GTD item. No prefix = ignored (zero noise).

---

## 7. API / Reference

**CLI (native):**
```
skos status [email|cron|gtd|docs|corpus|all|report|corpus-check] [--json]
skos ingest <email|calendar|telegram>
sk-status …            # console entry = skos.status:run (same output as `skos status`)
```
**Email adapter (`gtd-mail`):**
```
gtd-mail capture | triage | digest
gtd-mail reply <ref> --body "…" [--send] [--to addr] [-a acct]   # default: safe Gmail DRAFT
gtd-mail done <gtd_id>                                            # mark done + archive+read thread
gtd-mail attachments <ref> [--save] [--telegram] [-a acct]       # list/download/deliver
```
**Observability:** `sk-cron-run <job> <cmd…>` — wrap any scheduled job.

**Python API (the port):**
```python
from skos.gtd_ingest import GtdCapture, capture, GtdSourceAdapter, registry
capture(GtdCapture(text="…", source="<src>", source_ref="<stable-key>",
                   status="inbox|next|waiting|project|someday", context="@…",
                   priority="critical|high|medium|low", meta={...}))   # dedup by (source, source_ref)
```
**Add a pull adapter:** subclass `GtdSourceAdapter`, set `name`, implement
`poll() -> list[GtdCapture]`, add it to `skos/adapters/__init__._adapters()`, and
(optionally) a `capabilities.yaml` alternate + a wrapped cron. Drain: `skos ingest <name>`.
**Add a push adapter:** call `capture()` on your event (see `itil.py::_gtd_emit`).

---

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| Item captured twice | `source_ref` not stable/unique for that source; the sink dedupes on `(source, source_ref)`. |
| `skos` CLI: `Choice is not subscriptable` | typer too new for click 8.1 → `pip install typer==0.12.5`. |
| Sink writes to wrong dir | `SK_GTD_DIR` set unexpectedly, or skcapstone not importable → falls back to `SKCAPSTONE_HOME`. |
| Email triage does nothing | local LLM `.100:8082` down (cold/GPU) → falls back to `read`; check `curl :8082/v1/models`. |
| Report/brief not delivered | `hermes send` creds / Telegram bot token in `~/.hermes/.env`; run `sk-status report` by hand. |
| Cron ran but no ledger entry | job not wrapped in `sk-cron-run`; `tail ~/.skcapstone/logs/cron-ledger.jsonl`. |
| Calendar noise captured | add the term to `adapters/calendar.py::_NOISE`. |
| Telegram nothing captured | messages lack a trigger prefix (`todo:`…), or wrong `GTD_TG_CHAT`. |
| gog rate-limited (empty results) | avoid `--all` count storms; space calls; retry. |

---

## 9. Maturity-tier + Version reference

- **Tier:** T2 (working; daily production; test-gated). Not a crypto component
  (`CRYPTOGRAPHY_STANDARD` compliance: **N/A — no key material**; secrets are read
  from existing stores, never generated/stored here).
- **Version lifecycle:** Incubating (v3) subsystem of skos; SemVer tracks the skos package.
- **Self-report / claim evidence:** `skos status all` reports the live state of every
  surface (per-box email, cron ledger, GTD counts, corpus health) — every claim in
  this SOP is checkable there.
- **Standards conformance:** this subsystem is the **reference implementation** of
  [OBSERVABILITY_AND_SCHEDULING_STANDARD](https://github.com/smilinTux/sk-standards/blob/main/standards/OBSERVABILITY_AND_SCHEDULING_STANDARD.md)
  (all jobs wrapped → ledger + failure→GTD + sk-alert; inputs via one `source_ref`-deduped
  sink; daily ops report + on-demand status). Docs conform to
  [SK_REPO_DOC_STANDARD](https://github.com/smilinTux/sk-standards/blob/main/standards/SK_REPO_DOC_STANDARD.md)
  + [ARCHITECTURE_AND_DATAFLOW_STANDARD](https://github.com/smilinTux/sk-standards/blob/main/standards/ARCHITECTURE_AND_DATAFLOW_STANDARD.md);
  tests per [TESTING_AND_CI_STANDARD](https://github.com/smilinTux/sk-standards/blob/main/standards/TESTING_AND_CI_STANDARD.md).

## Related docs / See also
- 📐 **Spec:** [`gtd-ingest-architecture.md`](./gtd-ingest-architecture.md) — the design + phased roadmap.
- ⬆️ **Depends on:** skcapstone `gtd_tools` (store + lifecycle verbs), `gog`, local LLM, Hermes.
- ⬇️ **Used by:** ITIL (`skcapstone/itil.py`) as a push adapter; the daily digests + `skos status`.
- 📐 **Standards:** [sk-standards](https://github.com/smilinTux/sk-standards) — **observability & scheduling** (this is the reference impl), doc/SOP, architecture/dataflow, testing, version.
