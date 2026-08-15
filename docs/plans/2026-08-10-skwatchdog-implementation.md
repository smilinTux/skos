# skwatchdog: phased implementation plan

Date: 2026-08-10
Author: Fable (claude-fable-5)
Status: PROPOSED (no cards created, no config touched; this doc is the plan)
Spec: `../specs/2026-08-10-skwatchdog-architecture.md`

## 0. Ground rules carried through every phase

- Report-only until a flag is deliberately flipped; each write class has its
  own flag.
- No parallel stores: cursors + derived digest artifacts only; findings land
  in GTD/coord/ITIL, never a new list.
- All inference via skgateway `sk-default`; digest must render (template
  fallback) with the model down.
- Every scheduled job wrapped by `sk-cron-run.sh` (run-ledger + failure ->
  GTD + sk-alert), per the observability standard.
- Tests first-class: pure assembly/rendering functions with injectable
  probes, mirroring `operator_probe.py` and `brief.py` style.
- No em or en dashes in any emitted text (digest renderer includes a lint).

## Phase 1: the narrative digest, report-only (this week)

What lands: `skos/src/skos/watchdog/` with the `WatchdogEvent` type, the
adapter port, cursor store, digest assembly + renderer, DM delivery, and the
first seven read-only adapters (fleet events, scheduler ledger, ITIL, coord/
autocode board, Atlas brief + parked decisions, git/PRs, skingest). Plus the
long-scoped skingest staleness watchdog (deploy-plan 3.1) in skingest itself.

- Repos: `skos` (module + adapters + CLI + tests), `skingest` (3.1 only).
- Writes: cursor files and digest artifacts under `~/.skcapstone/watchdog/`,
  one sk-alert DM per day. Nothing else.
- Test strategy: hermetic unit tests per adapter (injected readers, fixture
  files for the ledger/board/events), assembly and severity tests, renderer
  golden tests (including the no-model fallback and the no-dash lint),
  one end-to-end dry-run test (`skos watchdog digest --dry-run --no-send`).
- The flip that enables it: edit `skos/deploy/schedule/jobs.yaml`, replace
  the `ops-report` job command with `skos watchdog digest`, run
  `skos schedule install`. One line, Chef's hand.
- Rollback: revert that line, `skos schedule install` again. The digest
  module keeps working on demand; `sk-status report` is untouched and
  resumes as before.

Exit criteria: three consecutive mornings of a digest Chef actually reads,
every problem line clickable, zero duplicate DMs, sources that broke shown
as SourceUnavailable lines rather than missing digests.

## Phase 2: chat sources and the grading loop (next)

- 2a: `chat.skchat`, `chat.telegram`, `email` collector adapters (read-only,
  windowed, summaries + refs only). Repos: `skos`.
- 2b: rubric grading v1: `rubrics/lumina-replies.yaml`, the ornith grader
  with verdict token, grades folded into the digest as a `grading` section.
  Report-only. Repos: `skos`.
- 2c: findings -> GTD behind `SKWATCHDOG_GTD=1`: `upsert` with
  `source="watchdog"`, stable `source_ref`, freeze-aware. Repos: `skos`.
- Test strategy: grader parsing tests (verdict token required, chatty reply
  rejected), rubric schema validation, upsert dedupe tests against a temp
  GTD store, privacy test (no full transcript ever serialized into an event
  or digest).
- Flip: `SKWATCHDOG_GTD=1` in the scheduler env file. Rollback: unset it;
  existing items age out through normal GTD processing.

## Phase 3: dispatch and the browser lane (flagged, later)

- 3a: card dispatch behind `SKWATCHDOG_CARDS=1`: staged Proposed children
  under one standing `skwatchdog-findings` epic, repo-tag required, 5/day
  budget, board-wide `(source, source_ref)` dedupe, freeze-aware. Promotion
  stays human (`autopilot release`). Repos: `skos`.
  SHIPPED as `skos/src/skos/watchdog/cards.py` (WD-9). Four things worth
  knowing before flipping it:
  - The dedupe ledger is the board's own `coordination/tasks/*.json`, read in
    full with no status filter. Coord task files are immutable and archiving
    only appends an id to `archive/<host>.jsonl`, so a card a human judged and
    archived is still a file and its finding never returns. There is no side
    list of "what we filed"; the board is asked.
  - A card is an ESCALATION of a WD-8 GTD item, never a duplicate of one. It
    is filed only when the finding is repo-attributable, WD-8 already has an
    OPEN item for it, and that item was opened by an EARLIER run. So
    `SKWATCHDOG_CARDS=1` does nothing at all while `SKWATCHDOG_GTD` is off,
    and a one-morning blip never reaches the board.
  - The 5/day budget counts cards already filed for that digest date off the
    board, so re-running a digest cannot spend it twice. Anything over budget
    is dropped, named individually in a `logging.warning` and in the run
    report, never silently truncated, and is reconsidered next run.
  - The staged lane is the existing one: the same `autopilot-staged` +
    `autopilot-untriaged` pair `skharness.autocode.orchestrator` puts on a
    child born into the Proposed lane, so `skos autopilot release <epic>`
    promotes a watchdog card exactly like any other staged child.
- 3b: browser QA lane for skchat web over chrome-cdp, Mon/Wed/Fri,
  screenshots + DOM asserts + console/network capture, ornith-graded,
  report-only events. Repos: `skchat` (the walk script + assertions),
  `skos` (the `browser` adapter).
- Flip: each behind its own env flag, default off. Rollback: unset; staged
  cards can be bulk-archived since they never left the Proposed lane.

## Phase 4: Atlas closes the loop

- `WatchdogDigestFresh` + `GradingBacklog` conditions added to skos's
  operator probe (`operator_probe.py`) and mirrored in Atlas's
  `skos_adapter` (or delivered via manifest discovery once
  `SKOPERATOR_MANIFEST_DISCOVERY` is on). A silent watchdog becomes a firing
  condition with `restart_service`/`replay_errors` already in the ratified
  catalog. Repos: `skos`, `skcapstone`.
- Rollback: conditions are observe-only; removing them restores the prior
  brief byte-identically.

## Proposed coord epic + child cards (PROPOSED ONLY, not created)

Epic: `skwatchdog: fleet narrative watchdog + self-improvement loop`
(tags: `repo:skos`, `epic`; children below proposed for the staged lane).

| # | Title | Repo tag | Size | Phase |
|---|---|---|---|---|
| WD-1 | watchdog core: WatchdogEvent type, adapter port + registry, cursor store, digest assembly | repo:skos | M | 1 |
| WD-2 | collector adapters wave 1: fleet events, scheduler ledger, ITIL, coord/autocode, Atlas, git/PRs | repo:skos | M | 1 |
| WD-3 | digest renderer + deep links (uri + https) + ornith headline with template fallback + DM delivery + publish beside Atlas brief | repo:skos | M | 1 |
| WD-4 | schedule cutover: ops-report slot -> `skos watchdog digest`; runbook with rollback | repo:skos | S | 1 |
| WD-5 | skingest staleness watchdog (deploy-plan 3.1): OnFailure= sk-alert units, staleness check, run summary; watchdog `skingest` adapter reads it | repo:skingest | S | 1 |
| WD-6 | chat/email collector adapters: skchat threads, telegram window, gog email (summaries + refs only) | repo:skos | M | 2 |
| WD-7 | grading loop v1: rubric YAML schema + lumina-replies rubric + ornith grader (verdict token, threshold) + digest grading section | repo:skos | M | 2 |
| WD-8 | findings -> GTD upsert behind SKWATCHDOG_GTD (dedupe, freeze-aware) | repo:skos | S | 2 |
| WD-9 | card dispatch behind SKWATCHDOG_CARDS: staged lane, single epic, repo-tag gate, 5/day budget, board dedupe | repo:skos | M | 3 |
| WD-10 | browser QA lane: scripted skchat walk over chrome-cdp + screenshot/console bundle + graded verdict, report-only | repo:skchat | L | 3 |
| WD-11 | operator facet: WatchdogDigestFresh + GradingBacklog conditions (skos probe + Atlas adapter) | repo:skos | S | 4 |
| WD-12 | sites link-check adapter (static sites coverage, lighthouse optional) | repo:skos | S | 3 |

Sizing: S under a day, M one to two days, L several days. WD-1 through WD-5
are the week-one set; WD-1/2/3 are sequential-ish, WD-5 is parallel.

## Transfers / already-better / genuine-gap: the video vs SKWorld reality

| Ryan's idea | Verdict | Why |
|---|---|---|
| Production watchdog: daily event summary of what customers/systems did | GENUINE GAP (partial) | `sk-status report` (07:45 daily) and the Atlas brief exist but are counts and boolean health; the narrative event digest is missing. This is skwatchdog Phase 1 |
| Every finding deep-links to the real UI | GENUINE GAP | Cheapest, highest-leverage detail in the whole video. The `skworld://` prefix contract already exists; nothing emits links today |
| Rubric-graded self-improvement loop (Grace) | GENUINE GAP | No SKWorld equivalent grades agent conversations. Phase 2, the highest-leverage new capability |
| E2E browser QA loop | PARTIAL GAP | chrome-cdp E2E tooling and the Flutter sandbox exist; the scheduled autonomous walk + triage handoff does not. Phase 3, skchat only |
| Agent watches its own video and fixes what it sees | DOES NOT TRANSFER | That is Devin harness machinery. Screenshots + DOM + console logs graded by a model achieve the outcome without building video annotation |
| Failure kicks off a child session that opens a PR | ALREADY BETTER | The autocode engine does this with an independent grader, external CI, and diff-coverage in a twin gate. Ryan merges whatever the child produced; SKWorld physically cannot merge below 5/5 + green CI |
| Cloud VMs, "working locally is caveman" | ALREADY BETTER, and wrong for this context | Chef owns the fleet; isolated worktrees + sandboxes give the same collision-free parallelism with sovereignty. Renting VMs would be a regression |
| Parent/child model routing (expensive parent, cheap children) | ALREADY BETTER | skgateway `sk-default` + Atlas `brain.py` route quiet passes to local ornith and decisions to a capable model, fleet-wide, by policy not by hand |
| $20k/month tokens, settling near $5k/engineer | DOES NOT TRANSFER | Local inference makes the sweep free. The scarce budget in SKWorld is Chef's attention, which is why the card budget and one-DM rule exist |
| Keys never go to agents (1Password, paste per task) | ALREADY BETTER | skvault + capauth-gated actuation + the remediation seam's hard deny on secret markers is the same discipline, enforced in code instead of by habit |
| Don't build your factory on a frontier-lab harness | ALREADY SOLVED DIFFERENTLY | SKWorld owns the router (skgateway) and can point `claude --model ornith-big` at its own metal. The lock-in risk Ryan hedges with vendor choice, Chef removed with ownership |
| "You are now a manager of agents; it makes you more technical" | TRANSFERS AS FRAMING | This is the Operator Seat philosophy already written down in the 2026-07-29 spec. Nothing to build |
| Pin 2-3 threads, ~25-minute check cadence, analog daily list | TRANSFERS AS PRACTICE | Maps onto priority coord cards + the morning digest. Worth adopting deliberately; nothing to build |
| Notification routing is a design problem | MOSTLY HANDLED | sk-alert, notify.py escalation cards, the autopilot decision DM. The residual gap (one morning surface instead of scattered pings) is exactly what the digest absorbs |
| Work 50% from your phone | PARTIAL | The Telegram bridge + reply-by-number digest + approve/reject commands already make the phone a real seat; the digest strengthens it. No new build beyond skwatchdog itself |
| Build a public reputation by sharing what you learn | TRANSFERS, independent | See recommendations below |

## Independent recommendations from the video (no skwatchdog dependency)

1. **Keep the human walkthrough.** Ryan is explicit that nothing substitutes
   for using your own app. Put a recurring 30-minute weekly slot on the
   calendar to click through skchat/skworld-app as a user. The browser lane
   reduces the need; it does not remove it.
2. **Adopt the pinned-threads discipline.** Each morning, pick 2 or 3 cards
   that define the day, and let everything else ride the board. The digest
   should end with "today's pinned" once Chef starts using it that way; the
   analog paper list Ryan uses is his GTD, and Chef already has a better one.
3. **Check on agents on a cadence, not continuously.** The 25-minute rhythm
   is a sanity guard against thread-thrash. With 5 to 10 parallel sessions
   becoming normal here, the same pacing applies.
4. **Publish the learnings.** Ryan's "write helpful articles" point lands
   directly on the DefCon talk material: the twin gate, the flood incident,
   the sovereign router. That corpus is already written; shipping it
   publicly costs little and compounds.
5. **Phone seat hardening.** Half of Ryan's throughput is phone-shaped
   decisions. The pieces exist (Telegram bridge, decision DM, decide
   commands); a deliberate pass to make approve/reject/release all workable
   from a thumb, with the digest as the entry point, is cheap and
   high-yield. It is adjacent to skwatchdog but does not depend on it.
