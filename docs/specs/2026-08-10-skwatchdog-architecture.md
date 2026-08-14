# skwatchdog: the fleet narrative watchdog and self-improvement loop

Date: 2026-08-10
Author: Fable (claude-fable-5), architecture pass over Lumina's brief
(`~/clawd/docs/fable-skwatchdog-brief.md`)
Status: PROPOSED
Scope: all of SKWorld (skos, skcapstone/Atlas, skharness/autocode, skchat,
skingest, skcomms, skgateway, skmemory, the sites)
Companion plan: `../plans/2026-08-10-skwatchdog-implementation.md`

## 1. One-paragraph summary

skwatchdog is NOT a new subsystem and NOT a new repo. It is a thin capability
module in skos (`skos/src/skos/watchdog/`) that does the one thing SKWorld
genuinely lacks from Ryan Carson's setup: turn the raw event exhaust of the
fleet (fleet events, cron ledger, ITIL records, coord board, autocode runs,
PRs, chats, email) into a once-a-day chief-of-staff narrative where every line
deep-links to the real object, plus (Phase 2) a rubric-graded self-improvement
loop over agent conversations. It reads everything, stores almost nothing (a
cursor per source and a derived daily digest artifact), and hands every
actionable finding to the systems that already own action: gtd-ingest for
items, the coord staged "Proposed" lane for code work, the autocode twin gate
for merges, ITIL for governance, Atlas for actuation. Phase 1 is report-only
and shippable this week.

## 2. Corrections to the brief (read before the design)

The brief asked for verification. Here is what the code actually says:

1. **The daily ops report already exists.** The brief frames R2 (daily
   chief-of-staff briefing) as a gap. It is half built: `sk-status report`
   composes a daily OPS report (email counts, cron run-ledger, GTD counts,
   corpus health) and DMs it to Chef at 07:45 every day via the `ops-report`
   job in `skos/deploy/schedule/jobs.yaml`. The
   OBSERVABILITY_AND_SCHEDULING_STANDARD section 3 mandates exactly this
   report ("sent always, so silence is never ambiguous"). What is missing is
   the narrative half: events instead of counts, "what actually happened"
   instead of "how many", and deep links. skwatchdog extends and absorbs this
   report; it must not become a second morning message.
2. **Deep links are already a contract field.** The brief asks whether we need
   a `skworld://` resolver or per-app deep-link contracts. The
   SKWORLD_MODULE_CONTRACT_STANDARD (schema v1.2) already REQUIRES
   `deeplinkPrefix` (`^skworld://<id>/$`) on every subapp manifest, and states
   it is "the same prefix Atlas escalations and the shell router both use."
   The scheme exists; the genuine gap is only that nothing outside the Flutter
   shell resolves it, so the digest must carry plain https fallbacks too.
3. **Test counts.** The autocode engine in skharness has roughly 271 autocode
   and autopilot test functions (605 in the repo overall). The brief's "~499
   tests" is the stale figure from the engine's previous home in
   `skos.autopilot`. The operator seat is heavily tested: roughly 489 test
   functions under `skcapstone/tests/operator_seat/`.
4. **cron-jobs.json is not a kill candidate.** `scripts/pipelines/cron-jobs.json`
   was decommissioned 2026-08-06 (CR-8.2); all 4 jobs are disabled and the file
   is retained for provenance only. There is nothing there for skwatchdog to
   subsume.
5. **The live loop registers 7 observers, not 9.** `loop.ADAPTERS` wires fleet
   plus six apps (skchat, skcode, skcomms, skmemory, skgateway, skos).
   `skdashboard_adapter.py` and `manifest_adapter.py` exist as files but are
   not in the built-in dict; manifest-driven discovery (OPS0.3,
   `discovery.py`) exists and is gated OFF behind
   `SKOPERATOR_MANIFEST_DISCOVERY`. That discovery seam is exactly how
   skwatchdog later exposes conditions to Atlas without editing skcapstone.
6. **Fleet events are read-only to us.** `fleet/events.py` restricts `emit()`
   to roles `sknoded` / `controller` / `scheduler` and states events are
   "observability, not control flow: no controller may key a decision off this
   log." The collector reads that log; it never writes it, and by the same
   discipline nothing in the control plane may key decisions off skwatchdog's
   digest.
7. **The transcript exists and the brief's summary of it is accurate** (the
   file was mid-Syncthing-sync at first read; it is one long 42KB line).
   All three automations, the token figures, the 1Password discipline, and
   the lab lock-in rant check out.
8. **The brief's central framing question ("is skwatchdog Atlas's missing
   evidence/narrative layer?") gets a split answer**, argued in section 4:
   it is the narrative layer BESIDE Atlas, not a layer INSIDE Atlas's loop.

## 3. What already exists and is reused (do not rebuild)

| Asset | Where (verified) | Role for skwatchdog |
|---|---|---|
| Atlas loop: observe, triage, route, propose, classify, park | `skcapstone/src/skcapstone/operator_seat/loop.py` et al. | The health/actuation plane. skwatchdog reads its outputs; never duplicates its loop |
| Atlas brief artifact (HTML + md, atlas.skworld.io) | `operator_seat/brief_publish.py` | The publish pattern and co-location target for the daily digest |
| Parked decisions store | `operator_seat/decisions.py` | A digest source (pending decisions surface in the morning narrative) |
| sk-alert Telegram path | `~/.skenv/bin/sk-alert`, `operator_seat/notify.py`, `fleet/alerts.py` | Delivery transport. Reused as-is |
| Daily ops report | `skos/src/skos/status.py` (`sk-status report`), jobs.yaml `ops-report` 07:45 | The seed. skwatchdog absorbs its DM slot; status.py stays as the counts engine and becomes a source |
| Scheduler-as-code + run wrapper | `skos/src/skos/schedule.py`, `scripts/sk-cron-run.sh`, cron ledger `~/.skcapstone/logs/cron-ledger.jsonl` | How the digest job is scheduled and observed; the ledger is a source |
| gtd-ingest port (capture/upsert, dedupe by (source, source_ref)) | `skos/src/skos/gtd_ingest.py` | THE output sink for actionable findings. No parallel store |
| skos adapter registry pattern | `skos/src/skos/adapter.py`, `adapters/` | The collector's adapter port copies this shape |
| Fleet event log (append-only, bounded, deduped) | `skcapstone/src/skcapstone/fleet/events.py` | Read-only source of causal history |
| ITIL engine (incidents, problems, changes, KEDB) | `skcapstone/src/skcapstone/itil.py` | Read source in Phase 1; governance target for anything that escalates |
| Autocode engine + twin gate + flood guard + staged lane | `skharness/src/skharness/autocode/` (orchestrator, engineering, digest, remediation) | The ONLY fix-dispatch path. skwatchdog files cards; it never builds, grades diffs, or merges |
| Autopilot decision digest (reply-by-number DM) | `autocode/digest.py` | The pattern for decisions inside a morning message; the watchdog digest links to it rather than replacing it |
| Module contract, deeplinkPrefix, operator facet | `sk-standards/standards/SKWORLD_MODULE_CONTRACT_STANDARD.md` | The deep-link vocabulary and the later Atlas registration seam |
| Manifest discovery (gated off) | `operator_seat/discovery.py` | Phase 4: how watchdog conditions reach Atlas without a skcapstone release |
| Remediation seam (deny-by-default, secret hard-deny) | `autocode/remediation.py` | Anything host-touching a finding proposes goes through this, never around it |
| skgateway | `http://localhost:18780/v1`, model `sk-default` | All inference. Never hardcode a model |
| SKWhisper | `~/clawd/projects/skwhisper/` | Stays separate (section 11) |

## 4. The Atlas question: peer beside, not layer inside

The brief asks whether skwatchdog is a new observe-source type for Atlas or
the narrative renderer on top of Atlas's brief. Neither, cleanly. The honest
answer is that they are two different planes that meet at files:

- **Atlas is the health/actuation plane.** Its loop is level-triggered,
  15-minute cadence, condition-shaped (`{type, status: True/False/Unknown}`),
  constrained to a ratified action catalog, and safety-critical: it is the
  thing that will one day restart services on its own. Its brief
  (`brief.py`) is pure boolean triage and must stay that way. Narrative
  cannot be expressed in that shape, and stuffing an LLM-rendered story into
  the loop would bloat the exact code whose smallness is its safety argument.
- **skwatchdog is the narrative/report plane.** Daily cadence, event-shaped,
  LLM-assisted rendering, zero actuation. Ryan's watchdog is a morning read,
  not a reconciler.

They integrate in four specific, file-shaped ways, none of which touch
`loop.py`:

1. **Atlas is a source.** The watchdog reads the fleet event log, Atlas's
   published brief/outcomes, and the parked-decision store, and folds "Atlas
   escalated X, waiting on you" into the morning narrative with a deep link
   to the decide command.
2. **Shared surfaces.** The digest publishes next to the Atlas brief (same
   `brief_publish` atomic-write pattern, same host), and skdashboard renders
   both.
3. **Shared output spine.** Both end in the same places: GTD items, coord
   cards, ITIL records. One board, one store, one governance system.
4. **Phase 4, Atlas watches the watchdog.** skos's operator facet
   (`operator_probe.py`) grows two conditions, `WatchdogDigestFresh` (health
   type: fires when no digest landed in 26h) and `GradingBacklog`. Atlas then
   treats a silent watchdog as a firing condition, which is the correct
   direction of dependency: the safety plane monitors the report plane, never
   the reverse.

## 5. Where it lives (question 1)

**`skos/src/skos/watchdog/`, a capability module in skos. No new repo.**

Why skos and not skcapstone or a fresh `skwatchdog` repo:

- skos already owns every seam the watchdog composes: the gtd-ingest sink,
  the adapter registry pattern, the scheduler-as-code pipeline and its
  ledger, `sk-status` (the report being absorbed), the secret-env resolver,
  and the operator probe where Phase 4 conditions land. The input adapters
  for email/telegram/calendar already live in `skos/adapters/`.
- A new repo buys nothing but overhead for a thin composition layer: another
  CI workflow, another OIDC trusted publisher (with the known filename
  binding trap), another skworld.module.json, another entry in the dev/CI/
  prod sync design's doctor. skos publishes as `skos` on merge to
  main already; skwatchdog ships inside it for free.
- skcapstone placement would couple a daily LLM-rendered report into the
  framework that hosts the safety loop, and would put chat/email reading
  into the same package that holds actuation. Wrong blast radius.

Module contract impact: none in Phase 1 (skwatchdog is not a subapp, it is a
capability of skos; skos's existing manifest stands). If a digest UI pane
ever ships in skdashboard, it rides skdashboard's manifest and
`skworld://skdashboard/watchdog/...` links.

CLI: `skos watchdog digest [--date D] [--dry-run] [--no-send]`,
`skos watchdog grade [--date D]` (Phase 2), `skos watchdog status`.

## 6. The collector (question 3)

### 6.1 No parallel store, by construction

The hard rule is that sources of truth stay where they are. The collector
therefore keeps only two kinds of state:

- **Cursors:** `~/.skcapstone/watchdog/cursors/<source>.json`, one tiny file
  per source holding the last-seen position (timestamp or id). Idempotent:
  losing a cursor means re-reading a window, never data loss, because
  everything downstream dedupes on `ref`.
- **Digest artifacts:** `~/.skcapstone/watchdog/digests/YYYY-MM-DD.{json,md}`
  plus a `latest/` publish dir (index.html + digest.md, `brief_publish`
  pattern). These are derived and regenerable from the sources; deleting
  them loses nothing but the rendering.

There is no event database. Events are read from their owners at digest time
over the window since the last digest.

### 6.2 The event schema

Neither existing shape fits and it is worth saying why. The fleet condition
shape (`{type, status, object}`) is boolean health with no narrative, no
timestamp, no link. The fleet event record (`{ts, node, kind, name, type,
reason, message}`) is close but is node-scoped, has no severity, no stable
cross-source ref, and no link. So the collector normalizes into one new
in-memory shape, `WatchdogEvent`:

```json
{
  "ts": "2026-08-10T06:12:03Z",
  "source": "fleet",
  "kind": "ServiceCrashLoop",
  "object": "skchat-daemon@dot41",
  "severity": "problem",
  "summary": "skchat daemon on .41 restarted 4 times between 06:02 and 06:11.",
  "link": {"uri": "skworld://skchat/ops/daemon", "http": "https://atlas.skworld.io/"},
  "ref": "fleet:dot41:2026-08-10T06:12:03Z:ServiceCrashLoop:skchat-daemon",
  "meta": {}
}
```

- `source`: `fleet | scheduler | itil | coord | autocode | atlas | git |
  skingest | chat.skchat | chat.telegram | email | grading | browser`.
- `severity`: `info | notable | problem`. Assigned deterministically by the
  adapter (a failed cron run is `problem`; a merged PR is `info`), never by
  the model.
- `ref`: the stable identity, and the dedupe key everywhere downstream
  (digest de-duplication, GTD `source_ref`, card dedupe).
- `link`: both forms always; `http` is the load-bearing one until a
  `skworld://` resolver exists outside the Flutter shell (section 8).

### 6.3 The adapter port

`WatchdogSourceAdapter` copies the `GtdSourceAdapter` shape on the skos
registry (`capability = "watchdog-source"`): implement
`collect(window) -> list[WatchdogEvent]`, register in the module. Adding a
source is one adapter, no core change, same as gtd-ingest. Every adapter is
fail-safe in the operator-probe sense: any exception degrades to a single
synthetic `{"source": name, "kind": "SourceUnavailable", "severity":
"notable"}` event, so a broken source becomes a visible line in the digest
instead of a missing digest.

Phase 1 adapters (all read-only, all local files or local CLIs):

| Adapter | Reads | Example events |
|---|---|---|
| `fleet_events` | `fleet/events.py::read()` per node | crash loops, converge actions, condition transitions |
| `scheduler` | cron run-ledger JSONL | failed/slow/missing jobs; "job X has not run in N days" (the generalized staleness watchdog) |
| `itil` | ITIL records dir via ITILManager | new/updated incidents, problems, changes awaiting CAB, scheduled changes and their deploy windows (see the Change Management note below) |
| `coord_autocode` | `~/.skcapstone/coordination/tasks/` + agent files + autocode journal | cards opened/completed, staged children awaiting release, autopilot decisions pending |
| `atlas` | published brief + decisions dir | firing/stale summary, parked escalations with decide commands |
| `git` | `git log` / `gh pr list` across configured repos | merges to main, open PRs aging, CI failures |
| `skingest` | skingest run summaries / timer state | ingest staleness (deploy-plan 3.1 surfaced here) |

### Change Management is a SOURCE, never a second notifier (added 2026-08-13)

A Change Management epic (coord epic `44f62183`) landed in parallel with the
Code section work: CAB voting, capauth change rules, ITIL change scheduling, a
`change-deploy-runner` scheduler job, and a gated deploy executor in
`skharness/autocode/change_deploy_bridge.py`. Its runner job carries its own
`notify: on_failure`.

That creates the one genuine overlap risk with this design. Both systems can
tell Chef that a deploy failed, through different channels, which is exactly
what the single-narrative-surface rule exists to prevent.

The rule, stated so it survives:

- **The watchdog READS change management. It does not notify for it.** Scheduled
  changes, missed deploy windows, CAB votes awaiting a human, and
  post-implementation reviews all arrive through the existing `itil` adapter and
  are narrated in the daily digest like any other source.
- **CM keeps its own `on_failure` notification.** That is an operational page
  about a specific job, fired at failure time; the digest is a daily narrative.
  Different jobs, different cadences, no deduplication needed between them.
- **The watchdog MUST NOT add a change-specific alert path**, subscribe to the
  deploy runner, or re-page on anything CM already paged. If a change failure
  appears in the digest, it appears as narrative with a deep link, not as an
  alarm.

Note also that the two epics already compose for free: CM's runner registers as
a `jobs.d` scheduler drop-in, so it is visible in the Code section's Jobs rail
(card C-8) and in this design's `scheduler` adapter as an ordinary job, with the
same staleness treatment as everything else. No integration work is required,
and none should be invented.

Phase 2 adds `chat.skchat` (MCP `search_messages` / `list_threads`),
`chat.telegram` (`skcapstone telegram` window read), `email` (gog, 4-C
labels), and `grading` (section 7).

### 6.4 The digest

Assembly is deterministic: bucket events by severity and source, compute
counts, keep every link. Exactly one model call renders the headline
narrative ("what mattered yesterday, in six sentences") through skgateway
`sk-default`, with a strict no-model fallback to a pure template so the
digest NEVER fails on a model outage. The model never invents lines: it
summarizes the already-assembled event list, and every bullet in the
rendered digest is generated from an event record with its link attached,
Ryan's game-changer detail.

Digest JSON shape: `{date, window: {from, to}, headline, problems: [...],
notable: [...], info_counts, per_source: {name: {ok, events, cursor}},
grading?: {...}}`. Delivery: sk-alert DM (absorbing the 07:45 ops-report
slot) plus the published `latest/` artifact beside the Atlas brief.

## 7. The grading loop (question 4)

This is the highest-leverage genuine gap (Ryan's Grace loop) and Phase 2.

- **What gets graded, in priority order:** (1) Lumina's outbound replies on
  the Telegram bridge and skchat threads (the direct Grace analog: the agent
  Chef's world actually talks to); (2) autopilot decision quality (were
  staged cards well-scoped, did released cards pass the gate first try);
  (3) GTD triage accuracy (did the noise sweep bury something Chef later
  pulled back). Not autocode PR quality: the twin gate already grades that
  independently at 1 to 5 with CI and coverage, and regrading it would be a
  second parallel grader of the same artifact.
- **The rubric:** versioned YAML in-repo
  (`skos/src/skos/watchdog/rubrics/<name>.yaml`): dimensions (answered the
  actual question, factually grounded, correct tone per soul, no banned
  punctuation, action captured to GTD when one surfaced), each 1 to 5, an
  overall floor, and a threshold (default 3). Rubric changes are commits,
  reviewable like code.
- **The grader:** reuse the autocode grader PATTERN, not its code. The
  autocode grader scores a diff against acceptance criteria inside a
  sandbox; conversations are a different artifact. What transfers is the
  discipline: an independent pass (the grader never sees the generator's
  chain of thought), a 1-to-5 integer scale, a required verdict token so a
  chatty reply cannot be misparsed as a score, and a deterministic threshold.
  Runs on ornith via skgateway; one call per conversation-day per thread.
- **A bad grade becomes:** a `grading` WatchdogEvent in the digest (always),
  and from Phase 2b a GTD item via `upsert` (`source="watchdog"`,
  `source_ref="grade:<thread>:<date>:<dimension>"`) so a persistent failure
  is ONE item that updates, not a daily new one. Code-shaped failures follow
  the Phase 3 card path (section 9). Soul/prompt-shaped failures stay GTD
  items for Chef or Lumina to act on: skwatchdog must never auto-edit souls,
  prompts, or its own rubrics (the same self-modification hazard that keeps
  skos and skharness out of the C6 dispatch allowlist).
- **Cost:** effectively zero. Ornith on .100 is local; a day of grading is
  tens of calls at 1 to 2k tokens each.

## 8. Deep links (question 5)

Reuse the contract. Every `WatchdogEvent.link` carries:

- `uri`: a `skworld://<moduleId>/<path>` built from the target module's
  `deeplinkPrefix` where one exists (skchat threads, skcode sessions,
  skdashboard board rows), or a `skworld://skos/...` internal form for
  skos-owned objects.
- `http`: the working link today. Per source: GitHub PR/commit URLs, the
  Atlas brief URL, skdashboard `:7778` routes, Gmail permalink via the gog
  account, `skworld.io` app routes for chat threads.

No new resolver is built. The markdown/DM digest renders `http` as the
clickable link and keeps `uri` in the JSON so the Flutter shell can upgrade
rendering later without re-collecting anything. Per-app deep-link path
conventions beyond the prefix are each module's own business; the watchdog
only composes `prefix + path` and never guesses.

## 9. Fix dispatch and flood discipline (question 6)

The 2026-08-08 flood (821 cards) is the design constraint. The handoff chain
is: finding -> GTD item or staged coord card -> human release -> autopilot ->
twin gate -> PR -> merge. skwatchdog's involvement ENDS at filing.

- **Phase 1: no writes at all** beyond cursors and digest artifacts. Findings
  are lines in the digest.
- **Phase 2b: GTD only.** `gtd_ingest.upsert` with `source="watchdog"` and a
  stable `source_ref` per finding. Upsert semantics mean a persisting finding
  updates one item; `unchanged` writes nothing.
- **Phase 3: cards, behind `SKWATCHDOG_CARDS=1` (default off).** Rules, all
  mandatory: cards are created ONLY as staged children in the "Proposed" lane
  under a single standing `skwatchdog-findings` epic, so nothing enters the
  autopilot claimable pool without `autopilot release`; a card requires a
  confident single `repo:<name>` tag or it stays a GTD item; hard run budget
  (default 5 new cards per day, counting skips loudly in the digest); dedupe
  by `(source, source_ref)` against the whole board before creating; and all
  GTD/card writes stand down when `store.is_frozen()` is true. Digest
  generation itself keeps running under freeze: freeze halts actuation, and
  a frozen fleet is exactly when the human wants the morning report.
- Nothing in skwatchdog ever calls the engineering executor, the remediation
  seam, or merge machinery directly. The twin gate stays untouched.

## 10. Cadence and cost (question 7)

| Job | Cadence | Model | Marginal cost |
|---|---|---|---|
| `watchdog-digest` | daily 07:45 (takes the ops-report slot) | 1 ornith call (headline), template fallback | ~0 (local .100) |
| `watchdog-grade` | daily 07:00 (before the digest, so grades appear in it) | ~10 to 40 ornith calls | ~0 |
| browser QA lane (Phase 3, optional) | Mon/Wed/Fri | ornith for triage; local CDP Chrome | ~0 plus wall-clock |
| escalation deep-dive | on demand only, human-invoked | capable model via sk-default routing | the only path that can cost real money, and it is manual |

Ryan spends $60 per browser run and settled near $5k/month. SKWorld's
equivalent spend is electricity, because skgateway routes the sweep to local
ornith and the expensive model is reserved for human-invoked decisions. This
is Ryan's parent/child routing already implemented better (`brain.py` routes
quiet to ornith, decisions to a capable model). The run budget that matters
here is not dollars but attention: the card budget (5/day) and the one-DM
rule (the digest absorbs ops-report rather than adding a message).

## 11. Browser QA (question 8)

Worth building, narrowly, and last. The only SKWorld surfaces where "a human
clicking through catches what the suite misses" applies are the skchat web
app and skworld-app; the sites are static (a link-checker/lighthouse adapter
covers them for near-zero cost). Phase 3 adds a `browser` lane: a scripted
walk (login, open thread, send message, join Space) driven over the existing
chrome-cdp setup (:9229 daily instance, agent instances :9222/:9223), 3x per
week, report-only. The Devin self-watching-video trick does not transfer and
should not be imitated with video: the working translation is
screenshot-per-step plus DOM assertions plus console/network error capture,
bundled and graded by ornith against a pass rubric. On failure the lane emits
a `browser` severity=problem event with the screenshot bundle path; the fix
follows the normal Phase 3 card path, not a special one.

## 12. What folds in, what stays (question 10)

| Thing | Verdict |
|---|---|
| 07:45 `ops-report` (`sk-status report`) | ABSORBED. The digest takes the slot; `status.py` remains the counts engine and a collector source. Rollback is a one-line jobs.yaml revert |
| skingest staleness watchdog (deploy-plan 3.1, unbuilt) | LANDED as part of Phase 1: the systemd `OnFailure=` + staleness pieces stay in skingest where they belong; the watchdog's `skingest` adapter surfaces them. The generalized "job X silent for N days" check lives once, in the `scheduler` adapter over the run-ledger |
| `scripts/pipelines/cron-jobs.json` | NOTHING TO DO. Already decommissioned, provenance only (brief corrected) |
| SKAlert | STAYS. It is the transport, not a competitor |
| SKWhisper curation | STAYS SEPARATE. whisper.md is subconscious context for agents (semantic memory, 30-min cadence); the digest is an ops narrative for the human (events, daily). They share nothing but the word "digest". A later `whisper` source adapter may quote its topic patterns; no folding |
| autopilot decision digest (`autocode/digest.py`) | STAYS. It is the reply-by-number ACTION channel; the watchdog digest links to it ("3 decisions pending, see the decisions DM") rather than duplicating numbered prompts |
| Atlas brief | STAYS. Health plane. The digest links to it and includes its summary |

## 13. Safety model

- Report-only by default at every phase boundary; each write class (GTD,
  cards) arrives behind its own explicit flag, off until Chef flips it.
- All sources are read through existing read paths; the collector holds no
  credentials beyond what skos adapters already resolve (gog keyring via
  secret-env; nothing new). Chat content read for grading stays local:
  events carry summaries and refs, never full transcripts, and the digest
  artifact inherits the privacy of the store it sits in.
- Never writes: fleet events (role-gated, and by discipline), ITIL records
  (Phase 1 to 3 read-only; escalation to incidents goes through the human or
  Atlas), souls/prompts/rubrics (self-modification hazard), anything under
  the remediation seam.
- Freeze: actuationless reporting continues; all GTD/card writes stand down
  when the fleet is frozen.
- No control loop may key a decision off the digest (same rule as fleet
  events). The digest informs the human; the human and the gated systems
  decide.
- Inference only via skgateway `sk-default`; no model names in code.

## 14. Answers to the ten questions, one line each

1. **Where:** `skos/src/skos/watchdog/`, a skos capability module; no new
   repo, no manifest change, ships in skos.
2. **Atlas relation:** peer plane beside Atlas; Atlas is a source, shares
   surfaces and output spine, and (Phase 4) observes watchdog freshness as a
   condition; nothing enters `loop.py`.
3. **Collector:** pull adapters on the skos registry normalizing to
   `WatchdogEvent`; cursors + derived digest only, no event store; fleet
   events read-only; condition shape rejected as unfit for narrative.
4. **Grading:** Lumina's outbound chat replies first, versioned YAML rubrics,
   ornith grader reusing the autocode grader's discipline (independent pass,
   1 to 5, verdict token, threshold) but not its diff-scoring code; bad grade
   -> digest line -> GTD upsert -> (flagged) staged card.
5. **Deep links:** reuse the contract's required `deeplinkPrefix`
   (`skworld://<id>/`); every event carries uri + https fallback; build no
   resolver.
6. **Dispatch:** ends at filing; staged Proposed lane only, single epic,
   repo-tag required, 5-cards/day budget, `(source, source_ref)` dedupe,
   freeze-aware; twin gate untouched.
7. **Cadence/cost:** digest daily 07:45 (absorbing ops-report), grading daily
   07:00, browser 3x/week; all ornith-local, ~zero marginal dollars; capable
   model human-invoked only.
8. **Browser QA:** yes, narrowly, Phase 3, skchat web only, over existing
   chrome-cdp; screenshots + DOM + console grading, no video.
9. **Phasing:** P1 report-only digest this week; P2 chat + grading; P3
   dispatch + browser; P4 Atlas conditions. Detail in the plan doc.
10. **Kill/fold:** absorb ops-report's slot, land skingest 3.1, leave
    cron-jobs.json (already dead), keep SKWhisper/SKAlert/autopilot-digest
    separate.
