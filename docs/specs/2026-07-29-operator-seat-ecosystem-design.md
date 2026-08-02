# The Operator Seat: Stack-Wide AI Operations Design Spec

Date: 2026-07-29
Author: Fable (architect pass)
Status: PROPOSED (epic spec + phased roadmap)
Scope: the whole SKWorld stack (skcapstone fleet, skchat, skos, skcomms,
skgateway, skmemory, and every app that plugs in later)
Parent: `2026-07-27-skworld-fleet-control-plane-design.md` (section 8 and
Phase 8 are the fleet-scoped seed of this design)

## 1. Summary

The fleet control plane (Phases 1 to 7, merged) gave the fleet a spine: six
declarative kinds, read-time controllers, a scheduler, signed writes, a freeze
flag, an event log, and a self-describing explain surface. Its stated
north-star payoff is the cognitive layer: an AI holding the OPERATIONS seat.
This spec generalizes that seat from "fleet operator" to "stack operator":
one AI operator that runs day-to-day operations across every SKWorld app,
with the human (Chef) retaining exactly one power that always wins, the
freeze card.

The partnership model, stated plainly:

- The AI operator holds the ops pager. It observes, diagnoses, and acts
  autonomously. Posture: auto-fix and report after, escalate with 2 to 3
  options only when it genuinely needs guidance.
- The human holds the freeze kill-switch, decides MAJOR (CAB) escalations,
  and is the only authority that can change the operator's own guardrails.
- Everything else is machinery already built: the fleet substrate is the OPS
  reference implementation, the ITIL system is the governance spine, the
  autocode harness is the CODE channel, capauth is identity, sk-alert plus
  the Telegram bridge is the feedback loop, and explain surfaces are how the
  operator learns what it can do.

This is an AGENT design, not a rule table. The intelligence is an agent
session (hybrid brain: local ornith for cheap quiet passes, Claude for real
decisions) reading normalized state and deciding. The substrate's job is to
make every one of that agent's actions classified, governed, signed,
reversible where possible, reported always, and physically incapable of
touching its own guardrails without a human.

New code is deliberately small: one substrate package
(`skcapstone/src/skcapstone/operator_seat/`), one pure detector module in the
harness (`skharness/src/skharness/autocode/protected.py`), one small amendment
to the ITIL fold, and thin per-app adapters. Everything else is reuse.

## 2. Grounding inventory: what exists and is reused

Bias is hard toward reuse, same as the fleet epic. This spec proposes no new
datastore, no new transport, no new identity system, no new merge machinery.

| Asset | Where | Role in this design |
|---|---|---|
| Fleet substrate (store, events, freeze, signing, explain, conditions) | `skcapstone/src/skcapstone/fleet/` | The OPS reference implementation and the plane-file store (`objects/_freeze.json`, and the new `objects/_protected.json`) |
| `store.is_frozen` + `objects/_freeze.json` | `fleet/store.py:252`, `fleet/paths.py:72` | THE stack freeze primitive, promoted from fleet scope to stack scope |
| Signed writes (off/permissive/enforce) | `fleet/signing.py` (`SKFLEET_SIGNING`, capauth keys, `canonical_bytes`, `verify_payload`) | The signing pattern every operator action reuses |
| Explain KINDS registry | `fleet/explain.py` | The discovery surface pattern every adapter must expose |
| Pure-plan / thin-apply split | `fleet/agent_convergence.py`, `fleet/gateway_sync.py` | The card decomposition discipline for every phase below |
| ITIL engine (event-sourced, append-only, CRDT-fold) | `skcapstone/src/skcapstone/itil.py` (1699 lines), records under `~/.skcapstone/coordination/itil/` | The governance spine: changes, CAB, incidents, problems, KEDB |
| ITIL CAB fold rule | `itil.py:861-885` (`_fold_change`) | Constitutional already: any rejection blocks; only `agent == "human"` approval unblocks a non-standard change; standard changes auto-approve at fold |
| ITIL MCP + CLI surface | `mcp_tools/itil_tools.py`, `cli/itil.py` | The operator writes ITIL records through the same tools agents use today |
| Autocode harness | `skharness/src/skharness/autocode/` | The CODE channel: card, sandbox build, twin gate, auto-merge |
| Twin gate predicate | `engineering.py:58` `twin_gate_passed`: score == 5 AND promise COMPLETE AND CI green AND diff coverage >= `RepoSpec.min_diff_coverage` | The self-gating property: broken code physically cannot merge |
| Auto-merge choke point | `engineering.py:405-465` `EngineeringExecutor.finalize`, the `automerge` boolean at L427, escalation via `digest.queue_decision` | The single seam where the carve-out detector is enforced |
| Tool firewall + worktree path guard | `autocode/claude_code.py` (`is_forbidden`, `assert_within_worktree`) | Existing deny-by-default precedent the detector copies |
| capauth | capauth repo, `resolve_agent_identity` | Per-seat identity; the operator signs as its own agent |
| sk-alert | `~/.skenv/bin/sk-alert` (outbound Telegram DM, dedupe keys, priority levels) | Report-after channel |
| skchat Telegram bridge | skchat repo | Inbound approve/reject/choose from the phone |
| skos ports and adapters | `skos/src/skos/adapter.py`, `capability.py`, `conformance.py`, `adapters/` | The adapter contract style (capability catalog + registered adapters + conformance suite) |
| skgateway | `http://localhost:18780/v1`, model `sk-default` | The quiet-pass brain (local ornith), never hardcoding a model |
| Coord board + GTD digest | `~/.skcapstone/coordination/tasks/`, `autocode/digest.py` | Card authoring for the CODE channel; decision queue plumbing |

What does NOT exist yet (and is therefore the new work): the operator loop
itself, the action-classification policy, the ITIL auto-normal fold
amendment, the protected-paths manifest and detector, the approval CLI and
Telegram inbound commands, and the per-app adapter contract. The fleet
approval gate (fleet spec Card 8.1) is also unbuilt; this spec absorbs it
(section 3.8) so it is built once, stack-wide, instead of fleet-only.

## 3. The operator substrate

Package: `skcapstone/src/skcapstone/operator_seat/` (not `operator`, which
shadows a stdlib module). CLI: `skoperator`. It lives in skcapstone because
ITIL, coord, fleet, and signing already live there.

### 3.1 The loop

One operator run, end to end. Level-triggered and idempotent, like every
fleet controller: a run on stale state is corrected next run, never
compounded. Single-flight: a run takes a coord-style claim
(`operator@<node>`) so two ticks never overlap.

1. WAKE: a fleet CronJob tick (default every 15 min), or an alert-triggered
   wake (sk-alert crit events also touch a wake file the CronJob checks).
2. FREEZE CHECK: `store.is_frozen(paths)` first, always. Frozen means the
   run writes a heartbeat digest line and exits. No observation-triggered
   cleverness while frozen.
3. OBSERVE: for every registered app adapter (section 4), pull
   `explain --json` (cached per adapter generation) and `observe --json`
   (objects, conditions, events), plus fleet merged objects, the fleet event
   log, open ITIL incidents, and pending escalations. Normalize into one
   OPERATOR BRIEF (a pure composition, section 6 card O4.1).
4. QUIET PASS (ornith via skgateway `sk-default`): triage the brief.
   Output is a strict verdict: `nothing` | `known` (exact KEDB match with a
   bound standard action) | `decision` (anything else actionable). The quiet
   pass is read-only by default; it never authors cards, never proposes
   MAJOR changes, never acts on a novel diagnosis.
5. DECISION PASS (Claude agent session), entered on `decision` (and on
   `known` while the auto-known knob is off): the agent diagnoses with the
   full brief, the explain surfaces, KEDB search, and the event history,
   then selects a channel and one or more actions.
6. ACT, per action: classify (3.4) -> open the ITIL change record ->
   guard checks (freeze re-check, signing, plane-file ownership, protected
   paths for CODE) -> execute through the adapter or the harness -> VERIFY
   (re-observe; confirm the target condition transitioned) -> advance the
   change record (implementing -> deployed -> verified) or roll back via the
   change's `rollback_plan` and open an incident.
7. REPORT AFTER, per action: sk-alert Telegram message (change id, app,
   action, result, rollback state), fleet event emit, and a line in the
   morning 7:15 brief. Escalations park (3.8) and notify with options.
8. HYGIENE: the operator also runs the ITIL process itself under the same
   autonomy: `check_sla_breaches()`, `auto_close_resolved()`, incident to
   problem linkage for recurrences, KEDB entries from resolved problems.
   These are record writes (append-only, reversible), classified STANDARD.
   The carve-out (3.6) is what it may never do: change the ITIL CODE or
   fold rules.

### 3.2 Hybrid brain routing

- Quiet pass: local ornith through skgateway (`http://localhost:18780/v1`,
  model `sk-default`, auto-router). Cheap, frequent, read-only triage and
  dedupe ("is anything actionable, and is it a known error?"). Runs every
  tick.
- Decision pass: a Claude session (the same adapter machinery the autocode
  harness uses, `adapters/claude_code.py` pattern, with the operator's own
  tool allowlist). Expensive, on demand, does all diagnosis and all novel
  actuation decisions.
- Routing rule, hard: ornith may never actuate, author cards, or classify
  its own actions. The only ornith-initiated actuation is the `known` path
  (exact KEDB match bound to a catalog standard action), and only after the
  `auto_known` knob is deliberately turned on late in the ramp (Phase O8).
  Default off.
- Budget: the operator gets its own spend caps modeled on autocode `Caps`
  (`max decision passes per day`, `max_usd_per_day`), separate from the
  build budget. Exhausted budget degrades to observe-and-alert, never to
  silent skipping.

### 3.3 Three actuation channels

CHANNEL 1, OPS: signed diffs to app objects and app-level actions. Fast and
reversible: restart a service, rerun a cron, re-place a workload, pause an
outbox, resync a catalog, cordon a node. Executed through the app's adapter
`act` verb (section 4) or `skfleet apply` for fleet objects. Every OPS
action carries its ITIL change id and the operator's capauth signature
(fleet signing pattern; adapters verify when enforce mode is on).

CHANNEL 2, CODE: the operator authors a coord card (title, description,
acceptance criteria, `repo:<name>` tag, quality gated) and the autocode
harness does the rest: sandbox build, up to 4 Ralph rounds, twin gate
(score == 5 AND promise COMPLETE AND CI green AND diff coverage), then
`finalize` with its second gate (repo in `automerge_repos`, repo.automerge,
real GitHub checks green) before `gh pr merge`. The channel is self-gating:
broken code physically cannot land. The operator's card is linked to its
ITIL change record (card id in the change timeline, change id in the card
description), so git history and ITIL history cross-reference. The
carve-out detector (3.6) rides this channel.

CHANNEL 3, ARCHITECTURE: for anything structural (new component, schema
change, replacing a mechanism, cross-app contracts), the operator does not
act. It writes a proposal: 2 to 3 concrete options with tradeoffs, costs,
and a recommendation, parked as a MAJOR escalation (3.8). The human picks.
The pick IS the approval: the chosen option auto-flows into the CODE
channel (the operator authors the implementation cards referencing the
approved change id), because the twin gate and the carve-out detector still
govern every resulting merge. A per-escalation flag `hold_cards: true` lets
the human demand a second look at the authored cards before builds start;
default is auto-flow. (This confirms the parent spec's default, with the
opt-out refinement.)

### 3.4 Governance: the ITIL mapping

Every operator action is an ITIL change record, written through the existing
`ITILManager` surface (`propose_change`, `update_change`, `submit_cab_vote`),
under the operator's own agent name. Git is the code audit; ITIL is the
prod-change audit. The mapping to the existing model
(`ChangeType`: STANDARD | NORMAL | EMERGENCY, `_fold_change` semantics):

| Operator action class | ITIL record | Approval mechanics | Posture |
|---|---|---|---|
| STANDARD: an action in an app's pre-approved standard-changes catalog (adapter-declared, human-ratified at adapter registration) | `ChangeType.STANDARD` | Auto-approves at fold time (existing `itil.py` behavior, `cab_required=False`) | Auto-apply, report after |
| NORMAL: not in the catalog, but reversible and low/medium risk with a stated `rollback_plan` | `ChangeType.NORMAL` + tag `auto-normal` | NEW small fold amendment (below): auto-approves at fold unless any rejection vote exists (standing human veto preserved) | Auto-apply, report after |
| MAJOR: irreversible, high blast radius, architecture, or anything touching the carve-out | `ChangeType.NORMAL`, no `auto-normal` tag | Existing fold rule verbatim: requires a CAB vote with `agent == "human"` to approve; ANY rejection blocks; the operator's own vote can never self-authorize (this is already enforced at `itil.py:876`) | Escalate with 2 to 3 options, park, notify |
| EMERGENCY / freeze | `ChangeType.EMERGENCY` record documenting the freeze event | Human-owned. The operator never uses the EMERGENCY type itself in v1 | All actuation halts (3.7) |

The auto-normal fold amendment (the only ITIL code change in this epic, and
it is inside the protected manifest so the operator can never amend it
later): in `_fold_change`, a NORMAL change auto-approves when ALL of:
`"auto-normal" in tags`, `risk != HIGH`, `rollback_plan` non-empty, the
record's creating writer is the operator seat (verified via the signed
event writer identity once ITIL event signing lands; until then, via the
`created_by` field plus the fact that only the operator's classifier sets
the tag), AND no rejection vote file exists. Any human (or agent) rejection
vote still hard-blocks, exactly as today. This keeps "AI approval never
self-authorizes" fully intact: auto-normal is not the operator approving
itself, it is a human-ratified standing policy (this spec, approved once)
with a standing per-change veto.

Classification is code, not vibes: a pure policy module
(`operator_seat/policy.py`) classifies every intended action from
machine-readable facts the adapter declares per action (`standard: bool` via
catalog membership, `reversible: bool`, `blast_radius: low|high`, protected
manifest hits, freeze state). Irreversible ops (object delete, node drain
hosting an always-on service, fleet-wide restart, destructive data ops) are
MAJOR by construction: `reversible: false` or `blast_radius: high` can never
classify below MAJOR, whatever the agent argues. The classifier's output is
recorded in the change record's timeline, so every audit answers "why was
this allowed to auto-apply".

### 3.5 Guardrails (all mandatory, none optional)

1. FREEZE ALWAYS WINS (3.7). Checked at wake, re-checked immediately before
   every individual actuation, honored by every adapter's `act`, by sknoded,
   by the scheduler, and by the CODE channel (no card authoring, no merges
   while frozen).
2. IRREVERSIBLE ESCALATES. Enforced in the pure classifier as above; not a
   prompt instruction, a code path.
3. ARCHITECTURE ESCALATES. Channel 3 exists precisely so the operator has a
   good path for structural change that is not "sneak it through channel 2".
   The classifier treats multi-repo cards, schema-touching cards, and cards
   whose diff would exceed a size threshold as architecture-channel
   candidates and forces the proposal path.
4. SIGNED WRITES. Every OPS write and every ITIL record carries the
   operator's capauth identity; the operator runs as its own agent identity
   (recommended: a dedicated `operator` agent under
   `~/.skcapstone/agents/operator/`, own key, own memory, own journal), so
   the audit trail distinguishes seats: Chef's writes, Lumina's writes, and
   the operator's writes are cryptographically distinct. Rollout is
   permissive-then-enforce per the fleet signing pattern (`SKFLEET_SIGNING`
   and an analogous `SKOPERATOR_SIGNING`).
5. THE CONSTITUTIONAL CARVE-OUT (3.6). The operator may modify anything in
   the stack EXCEPT the machinery that constrains the operator. Those
   changes always escalate to the human, never auto-merge, even at score 5
   with green CI.

### 3.6 The constitutional carve-out: concrete enforcement

What is protected (the guardrail set): freeze logic, the twin gate and the
merge path, signing and identity, the escalation and CAB policy (including
the ITIL fold), the protected-paths mechanism itself, and the operator's
own policy/classifier code. The enforcement is layered so no single bug or
clever prompt defeats it:

LAYER 1, the manifest. A PROTECTED-PATHS MANIFEST, human-owned,
single-writer (Chef), signed under Chef's capauth identity, living as a
plane file in the fleet tree: `~/.skcapstone/fleet/objects/_protected.json`
(underscore prefix is already reserved for plane files, same as
`_freeze.json`). Shape:

```json
{
  "version": 3,
  "repos": {
    "skcapstone": [
      "src/skcapstone/itil.py",
      "src/skcapstone/fleet/store.py",
      "src/skcapstone/fleet/signing.py",
      "src/skcapstone/fleet/paths.py",
      "src/skcapstone/operator_seat/policy.py",
      "src/skcapstone/operator_seat/governance.py",
      "src/skcapstone/operator_seat/freeze.py",
      "tests/operator_seat/test_guardrails.py"
    ],
    "skharness": [
      "src/skharness/autocode/engineering.py",
      "src/skharness/autocode/ratify.py",
      "src/skharness/autocode/ci.py",
      "src/skharness/autocode/config.py",
      "src/skharness/autocode/claude_code.py",
      "src/skharness/autocode/protected.py",
      "tests/autocode/test_protected.py"
    ],
    "capauth": ["**"]
  },
  "writer": {"role": "human", "identity": "capauth:chef", "signature": "..."}
}
```

Granularity is file-level in v1 (globs allowed). Yes, that means a benign
refactor of `itil.py` escalates; that friction is the design, not a defect
(open decision D2 offers Chef the tradeoff knob).

LAYER 2, the detector at the merge choke point. A new PURE module
`skharness/src/skharness/autocode/protected.py`:
`touches_protected(diff_paths: list[str], repo_name: str, manifest: dict)
-> list[str]` (the matching globs, empty when clean), plus manifest loading
with signature verification (reusing `fleet.signing.verify_payload`) that
FAILS CLOSED: an unreadable, unsigned (in enforce mode), or missing
manifest means every diff in a manifest-listed repo is treated as
protected. Wired into `EngineeringExecutor.finalize`
(`engineering.py:405-465`) at two points: (a) at the top of `finalize`,
before commit/push, the diff's paths are checked; a hit forces the
escalation branch (`digest.queue_decision` plus a MAJOR ITIL change with
the diff summary attached, PR opened for human review, task left claimed,
NEVER merged); (b) as a belt-and-braces clause on the `automerge` boolean
at L427 (`... and not protected_hits`). `RepoSpec` gains a
`protected_manifest: bool` field (registered in `_REPO_KEYS` in
`config.py:17`) that opts a repo into manifest checking; skcapstone,
skharness, and capauth ship opted in. `ratify.py` reports protected hits in
its `GateResult` notes so the read-only grade path shows the same verdict.

LAYER 3, the CI backstop. A `protected-paths` CI check (a small script
runnable under both GitHub Actions and `local:` CI) that fails when the PR
diff touches manifest globs. It registers in the SECURITY class of checks
(`_AUTOMERGE_SECURITY` in `engineering.py`), and the existing rule
"security checks are NEVER advisory" plus `_github_checks_verdict`
returning `blocked` on a failed security check means that even a build path
that somehow skipped Layer 2 cannot auto-merge: the GitHub checks verdict
gate at L435 refuses. Two independent implementations of the same predicate
must both be defeated for a protected diff to land unreviewed.

LAYER 4, behavioral enforcement (the anti-bypass net). Path-matching alone
can be gamed by editing an unprotected module that a protected one imports.
So the guardrail TEST SUITES (`test_guardrails.py`, `test_protected.py`)
are (a) themselves protected paths, and (b) part of the core CI check set
for their repos: they assert BEHAVIOR (freeze halts actuation, a veto
blocks an auto-normal fold, a protected diff cannot produce
`automerge=True`, an unsigned manifest fails closed, the manifest
self-includes the detector and the fold and the freeze). Any change
anywhere that breaks a guardrail behavior goes CI-red and cannot pass the
twin gate, regardless of which file it hid in.

LAYER 5, the OPS-side carve-out. The fleet store already restricts writes by
role; it gains one rule: plane files (`objects/_*.json`) are writable only
by a writer whose verified identity is the human's. The operator seat
therefore cannot write `_freeze.json` or `_protected.json` through any code
path, enforced at `store.py` (itself a protected path) and re-verified by
sknoded before honoring plane-file state in enforce mode.

Who unlocks a protected change: the human, and only the human. The
escalation carries the PR link, the diff, the grade, and the CI verdict.
The human approves via a CAB vote (`skoperator decide` or `/decide` on
Telegram, both recording `agent="human"`) and merges the PR themself in v1
(open decision D3 covers whether an approved record may trigger the merge
mechanically later).

### 3.7 Freeze semantics, stack-wide

`~/.skcapstone/fleet/objects/_freeze.json` is promoted to THE stack freeze.
One file, human-owned, already implemented (`store.is_frozen`,
`skfleet freeze/unfreeze`). Generalization is by contract, not new
machinery: `operator_seat/freeze.py` wraps `is_frozen` for non-fleet
callers, and the adapter conformance suite (4.2) requires every adapter's
`act` to check it and refuse while frozen. Frozen means: no OPS actuation
anywhere, no card authoring, no auto-merges (the harness checks it in
`finalize`), no quiet-pass known-fixes, scheduler places nothing, sknoded
halts actuation. What continues: observation, self-report, heartbeats,
alerting, and reading. Unfreeze is a human act. Every freeze/unfreeze pair
is recorded as an EMERGENCY change for the audit trail.

### 3.8 Escalation, approval, feedback

Escalation record: a MAJOR ITIL change whose description carries the
diagnosis, 2 to 3 options with tradeoffs, a recommendation, and an expiry
(default 72h; expiry closes the change `rejected -> closed` and the
condition re-raises if still true). This one mechanism serves fleet
approval-gate needs (parent spec Card 8.1: drain, delete, fleet-wide
restart), protected-path code changes, and architecture proposals: they are
all the same shape, "parked decision with options".

Channels, both thin skins over the same records:

- CLI: `skoperator pending` (list parked escalations with options),
  `skoperator decide <chg-id> --option N [--note ...]`,
  `skoperator reject <chg-id>`, `skoperator explain <chg-id>` (full brief).
  `decide` records a CAB vote `agent="human"`, `decision=approved`,
  `conditions="option-N"`. `reject` records the rejection vote (blocks
  permanently under the existing fold rule).
- Telegram: outbound via sk-alert (crit level, dedupe key = change id) with
  the options numbered; inbound via the existing skchat Telegram bridge,
  which gains two operator commands, `/ops` (pending list) and
  `/decide <chg-id> <n>`, routed to the same CLI path. Identity binding:
  the bridge only accepts these from Chef's verified chat id (the sk-alert
  home channel), and the resulting vote is written under the human
  identity.

Report-after (non-escalation actions): one Telegram line per action
(`[ops] chg-ab12cd34 skchat restart-telegram-bridge: verified, rollback
n/a`), batched per run when more than 3 actions fire, plus the fleet event
log and a 7:15 brief section (`operator: N actions, M escalations
pending`).

Approved architecture options auto-flow: the operator authors the
implementation cards (each carrying the change id), the change moves to
`implementing`, and the change reaches `deployed`/`verified` as the cards
merge and their acceptance verifies. The pick is the approval; the twin
gate still governs every merge.

### 3.9 Discovery

The operator hardcodes nothing about any app. At wake it reads each
registered adapter's `explain --json` (the fleet's `skfleet explain --json`
KINDS registry is the schema archetype): kinds, spec/status fields,
condition types with meanings, and the ACTION CATALOG with per-action
metadata (args schema, `standard`, `reversible`, `blast_radius`, runbook
text or KEDB ref). Adding an app to the operator's world is: ship an
adapter, register it (4.3). No operator code change. The parent spec's
Card 8.2 acceptance test generalizes: a fresh operator given only the
explain outputs constructs valid actions for every registered app
(scripted probe test in the conformance suite).

### 3.10 Identity

The operator is a first-class SKWorld agent: `operator` under
`~/.skcapstone/agents/operator/` with its own capauth key, soul config,
journal, and memory. Rationale: (a) signatures distinguish seats in every
audit trail; (b) the ITIL `agent` field in events, claims, and CAB votes is
honest; (c) the operator can accrue its own operational memory (skmemory)
without polluting Lumina's; (d) revoking the seat is revoking one key.
(Open decision D4 if Chef prefers the Lumina seat instead.)

## 4. The per-app adapter contract

### 4.1 The port

Style follows skos ports-and-adapters exactly: a capability (`operator`) in
the catalog, adapters registered against it, a conformance suite that every
adapter must pass (mirroring `skos/adapter.py` AdapterRegistry +
`conformance.assert_conforms`). Because SKWorld apps are separate repos and
processes, the canonical contract is CLI-FIRST: an app conforms by exposing
three subcommands, and the substrate ships ONE generic `CliShimAdapter`
that wraps any conforming CLI (injected runner, so the shim itself is
testable hands-off). In-process Python adapters are permitted for
skcapstone-resident surfaces (the fleet adapter is one) but must present
the identical surface.

### 4.2 Contract surface (what an app MUST expose)

1. `<app> operator explain --json`
   Self-description: object kinds, spec/status field docs, condition types
   with meanings, and the action catalog. Each action:
   `{name, description, args: {jsonschema}, standard: bool,
   reversible: bool, blast_radius: "low"|"high", runbook: str,
   kedb_refs: [ke-ids]}`. Schema versioned (`contractVersion`).
2. `<app> operator observe --json`
   Read-only snapshot, STRICTLY side-effect free: objects with their
   conditions in the fleet convention
   (`{type, status: True|False|Unknown, reason, message, lastTransition}`),
   plus recent app events (bounded, newest first). `Unknown` is mandatory
   for stale data, same as fleet. Cheap enough to call every tick.
3. `<app> operator act <action> --args <json> --change-id <chg-id>
   [--signature <sig>]`
   Executes exactly one cataloged action. MUST: be idempotent (acting on an
   already-converged target is a no-op success); check the stack freeze and
   refuse with a distinct exit code while frozen; verify the caller
   signature when `SKOPERATOR_SIGNING=enforce`; emit its own audit line
   including the change id; never prompt interactively.
4. STANDARD-CHANGES CATALOG: the subset of actions with `standard: true`.
   Declaring an action standard is a proposal; it becomes pre-approved only
   when the human ratifies the adapter registration (4.3). Standard actions
   must be reversible, low blast radius, and runbook-documented, and the
   conformance suite rejects a catalog violating that.
5. KEDB SEEDS: the adapter ships known-error entries (symptoms, root cause,
   workaround bound to a cataloged action) registered into the ITIL KEDB at
   registration time. This is how tribal knowledge (the telegram-bridge
   wedge, the outbox flood runbook) becomes operator knowledge.
6. CODE TARGETS: the repo name(s) in the autocode `repo_map` that implement
   the app, plus any protected-path contributions the app wants added to
   the manifest (subject to human ratification, since the manifest is
   human-owned).
7. CONFORMANCE: the generic suite asserts: explain validates against the
   contract schema; observe is read-only (strace-free proxy: run twice,
   assert no state change and no writes outside logs); every action
   declares the required metadata; irreversible actions are not marked
   standard; act refuses when frozen; act is idempotent on a converged
   fixture; unknown action names fail closed.

### 4.3 Registration and discovery

Registration is a fleet object (spec-writer: human or operator, but the
STANDARD catalog ratification bit is human-only):
`~/.skcapstone/fleet/objects/operatorapp/<app>.json`:

```json
{
  "kind": "Operatorapp",
  "name": "skchat",
  "spec": {
    "cli": "skchat",
    "contractVersion": 1,
    "repos": ["skchat"],
    "ratifiedStandardActions": ["restart-daemon", "restart-telegram-bridge"],
    "enabled": true
  }
}
```

`skoperator apps` lists registrations with conformance status. The operator
discovers its world by listing this kind, then calling each app's explain.
An app absent from the registry does not exist to the operator: enrolling
is explicit, like fleet node admission.

### 4.4 Worked example: the skchat adapter

skchat is the most operationally active app (daemon, Telegram bridge,
LiveKit calling stack, the outbox that once melted .41), so it makes the
contract concrete.

`skchat operator explain --json` (abridged):

- Kinds: `daemon`, `bridge`, `callingstack`, `outbox`.
- Conditions: `DaemonReady` (API answering on the service port),
  `BridgeAlive` (telegram poll age below threshold; the silent-wedge
  detector from the known incident), `CallingReady` (LiveKit + coturn
  healthy), `OutboxBounded` (file count and churn under caps; the flood
  detector), `AuthEnforced` (SKCHAT_DATAPLANE_AUTH flag observed on).
- Actions:
  - `restart-daemon` {standard: true, reversible: true, blast: low,
    runbook: "systemctl --user restart skchat-daemon; verify DaemonReady"}
  - `restart-telegram-bridge` {standard: true, reversible: true, blast:
    low, kedb_refs: [ke-telegram-wedge]}
  - `pause-outbox` {standard: false, reversible: true, blast: low,
    runbook: "flip outbox pause flag; drains stop; messages queue"}
  - `purge-outbox` {standard: false, reversible: FALSE, blast: high,
    runbook: "destructive; enumerate counts first"} (classifier makes this
    MAJOR by construction; the operator can only propose it with options,
    e.g. purge vs pause-and-drain vs re-home)
  - `resync-peers` {standard: false, reversible: true, blast: low}
- KEDB seeds: `ke-telegram-wedge` (symptoms: poll age > 10m with daemon up;
  root cause: ConnectTimeout hang; workaround: restart-telegram-bridge),
  `ke-outbox-flood` (symptoms: OutboxBounded False, Syncthing churn alarm;
  workaround: pause-outbox then escalate with drain options).
- Code targets: repo `skchat` (already CI-gated in the harness world).

A day-one trace: tick fires, observe shows `BridgeAlive=False` on .158,
quiet pass matches `ke-telegram-wedge` (verdict `known`; knob off, so it
still goes to the decision pass), Claude confirms, classifier says catalog
standard, ITIL STANDARD change `chg-xxxx` opens (auto-approved at fold),
`skchat operator act restart-telegram-bridge --change-id chg-xxxx` runs,
re-observe shows `BridgeAlive=True`, change advances to verified, Telegram
gets one report-after line. Total human involvement: zero.

## 5. The fleet operator: reference implementation

The fleet (skcapstone) adapter is the substrate's reference implementation
and the first registered app, and it is mostly already built:

- explain: `skfleet explain --json` (the KINDS registry, live since
  Phase 1, filled through Phase 7). Gap to close: per-action metadata
  (standard/reversible/blast) on the registry's action lists.
- observe: `skfleet get`/`describe` merged views, node conditions, the
  per-node event log.
- act: `skfleet apply` (signed spec writes), `cordon`/`uncordon`,
  `reconcile`, actuation opt-in, with drain and delete classified MAJOR
  (this absorbs the parent spec's Card 8.1 approval-gate list).
- Standard catalog (proposed for ratification): restart a CrashLooping
  pilot service (bump via spec touch), rerun a missed CronJob, uncordon,
  re-place a `failover: auto` service. NORMAL tier: cordon, pause/unpause,
  new placements. MAJOR: drain of a node hosting an always-on service,
  object delete (tombstone), fleet-wide restart, any `_*.json` plane file
  (impossible anyway per Layer 5).
- KEDB seeds from the scars: broadcast-flood symptoms, sync-conflict files
  (ownership bug: incident, never merge), crash-loop backoff exhaustion.

Parent-spec Phase 8 is thereby superseded, not duplicated: Card 8.1's
approval gate becomes the substrate escalation mechanism (3.8), Card 8.2's
explain capstone becomes the fleet adapter's metadata gap-close, and
Card 8.3's operator loop becomes this epic's Phases O4/O8 with the fleet
adapter as first tenant. The fleet epic closes by pointing here.

## 6. Phased card breakdown

Discipline, proven across fleet Phases 1 to 7: every capability splits into
a PURE planning module (no I/O, table-driven tests, builds hands-off through
the autocode harness) and a THIN APPLY card (real writes, network, wiring:
human-reviewed). HANDS-OFF below means the card is expected to flow
card -> build -> twin gate -> auto-merge with no human touch. REVIEW means
a human reviews the PR (several of these touch protected paths, where
review is mandatory by construction). Ordering: O1 -> O2 are the
constitution and must land before any actuation; O3 -> O5 build the working
loop in report-only; O6 -> O7 widen it; O8 turns autonomy on stepwise.

### Phase O1: Governance core (policy + ITIL mapping + freeze)

- O1.1 HANDS-OFF (pure): `operator_seat/policy.py`. Action classifier:
  (action metadata, catalog, manifest hits, freeze) -> STANDARD |
  AUTO-NORMAL | MAJOR | REFUSED-FROZEN. Irreversible/high-blast can never
  classify below MAJOR. Table-driven tests including adversarial rows.
- O1.2 HANDS-OFF (pure): `operator_seat/governance.py`. Pure builders
  producing ITIL intents per class (propose_change payloads, lifecycle
  advance plans, CAB-vote payloads, escalation records with options and
  expiry). No ITILManager calls; returns intents an apply layer executes.
- O1.3 REVIEW (protected: `itil.py`): the auto-normal fold amendment in
  `_fold_change` (auto-approve iff tag + risk != HIGH + rollback_plan +
  operator writer + zero rejection votes). Plus guardrail tests asserting a
  single rejection vote blocks forever.
- O1.4 HANDS-OFF (pure): `operator_seat/freeze.py`. Stack-freeze wrapper
  over `store.is_frozen`, refusal helper with distinct exit code, plus the
  plane-file ownership predicate (human-identity-only for `objects/_*`).
- O1.5 REVIEW (protected: `fleet/store.py`): enforce the plane-file rule at
  the store write path; `skoperator`/`skfleet` freeze UX unchanged.

### Phase O2: Carve-out enforcement (must precede any CODE-channel use)

- O2.1 HANDS-OFF (pure): `skharness/autocode/protected.py`. Manifest
  schema + signature verification (fail closed) + `touches_protected` glob
  matcher + the SELF-INCLUSION test (the manifest fixture must cover the
  detector, engineering.py, ratify.py, ci.py, config.py, claude_code.py,
  itil.py, store.py, signing.py, policy.py, governance.py, freeze.py, and
  both guardrail test files; test fails otherwise).
- O2.2 REVIEW (protected: `engineering.py`, `config.py`): wire the detector
  into `finalize` (pre-commit check routing to the escalation branch; the
  `automerge` boolean clause at L427), add `protected_manifest` to
  `RepoSpec` + `_REPO_KEYS`, surface hits in `ratify` GateResult notes,
  and check freeze in `finalize`.
- O2.3 REVIEW: the CI backstop: `protected-paths` check script (GH Actions
  + `local:` runnable), registered security-class; rolled out to
  skcapstone and skharness repos first. Behavioral guardrail suites wired
  into core CI (Layer 4).
- O2.4 REVIEW (human ceremony): author and sign the initial
  `objects/_protected.json` (Chef's key), plus `skoperator protected show`
  / `verify` CLI. Includes the signing key ceremony doc.

### Phase O3: Adapter contract + fleet reference adapter

- O3.1 HANDS-OFF (pure): `operator_seat/adapter.py` + `conformance.py`:
  contract types, explain/observe schema validation, `CliShimAdapter` with
  injected runner, the generic conformance suite of 4.2.7.
- O3.2 HANDS-OFF (mostly pure): the fleet adapter: observe normalization
  over `skfleet get --json` outputs, act mapping to skfleet verbs, action
  metadata added to the explain KINDS registry (gap-close of section 5).
  Injected runner keeps it hands-off.
- O3.3 REVIEW: `Operatorapp` kind (registration objects, `skoperator apps`,
  ratification flow where only the human identity may set
  `ratifiedStandardActions`), registered into the fleet explain registry.

### Phase O4: The loop + hybrid brain (report-only first)

- O4.1 HANDS-OFF (pure): brief composer: merge N adapter observations +
  fleet events + open incidents + pending escalations into the operator
  brief; staleness marking; bounded size with overflow summarization rules.
- O4.2 HANDS-OFF (pure): quiet-pass contract: prompt template, strict
  verdict parser (`nothing|known|decision`; a malformed verdict folds to
  `nothing` plus a malformed-verdict alert, so a confused ornith can never
  actuate or escalate spend), KEDB-match binding rules. Model call
  injected.
- O4.3 REVIEW: quiet-pass runner wired to skgateway `sk-default`, scheduled
  as a fleet CronJob (15 min tick), single-flight claim, wake-file support,
  REPORT-ONLY mode (verdicts and would-do actions logged and alerted, no
  actuation). Runs this way for a burn-in week minimum.
- O4.4 REVIEW: decision-pass runner: Claude session bootstrap with the
  brief + explain surfaces + operator tool allowlist (its own firewall
  list, modeled on `claude_code.is_forbidden`, denying secrets/kms/send
  tools), acting only through governance + adapters. Budget caps. Ships in
  report-only.
- O4.5 REVIEW: report-after plumbing: sk-alert lines (dedupe key = change
  id), batching, the 7:15 brief section, fleet event emits, post-action
  verify + rollback-then-incident path.

### Phase O5: Approval and feedback surfaces

- O5.1 HANDS-OFF (pure): escalation formatting: options rendering for
  Telegram and CLI, expiry computation, decision-record parsing.
- O5.2 REVIEW: `skoperator pending / decide / reject / explain` writing CAB
  votes as `agent="human"`; expiry sweeper closing stale escalations.
- O5.3 REVIEW (skchat repo): Telegram inbound `/ops` and `/decide` in the
  bridge, chat-id-bound to Chef, routed to the O5.2 path.

### Phase O6: Architecture channel auto-flow

- O6.1 HANDS-OFF (pure): option-to-cards planner: (approved MAJOR change,
  picked option) -> coord card payloads (repo tags, acceptance criteria
  embedding the change id, `hold_cards` honor).
- O6.2 REVIEW: apply: card creation + change-timeline linkage; end-to-end
  drill proving pick -> cards -> twin gate -> merge -> change verified,
  with the protected detector still escalating a planted guardrail edit.

### Phase O7: App adapters, wave 1 (each = one pure + one review card)

- O7.1 skchat adapter (the 4.4 design): pure observe/normalize + KEDB seeds
  HANDS-OFF; act shim + registration REVIEW.
- O7.2 skgateway adapter: upstream/route objects, `Serving` conditions
  (feeding on ModelServer health it already receives), resync/restart
  actions. Same split.
- O7.3 skcomms adapter: channel paths, outbox health, bridge restarts.
- O7.4 skmemory adapter: daemon/embed-failover conditions (the per-node
  topology), reindex and reconcile actions (destructive compactions MAJOR).
- O7.5 skos adapter: gtd-ingest port health, adapter-run staleness, drain
  reruns.

### Phase O8: Autonomy ramp + closeout

- O8.1 REVIEW: flip STANDARD-catalog auto-apply ON for the fleet + skchat
  adapters. Gameday: injected bridge wedge and CrashLoop must self-heal
  with correct ITIL records and Telegram reports.
- O8.2 REVIEW: flip AUTO-NORMAL ON. Drill: a human rejection vote mid-flight
  blocks the fold and the operator backs off cleanly.
- O8.3 REVIEW: optionally enable the quiet-pass `auto_known` knob. Final
  drills: (a) freeze flipped mid-run halts everything within one guard
  check; (b) the operator is TASKED (by us) to modify `itil.py` via the
  CODE channel and the diff escalates instead of merging, at score 5 with
  green CI; (c) seat-revocation drill (revoke the operator key, verify
  enforce mode refuses its writes). Closeout doc + parent-spec Phase 8
  cross-reference update.

Dependency spine: O1 -> O2 -> (O3, O4 in parallel) -> O5 -> O6 -> O7
(any order per app) -> O8. Report-only operation starts as early as O4.3
and runs throughout O5 to O7, accumulating a track record before O8 grants
autonomy.

## 7. Risks

- R1 Guardrail bypass via indirection (edit an unprotected caller of
  protected code). Mitigated by Layer 4 behavioral tests in core CI, the
  detector's fail-closed manifest handling, and MAJOR-by-construction
  classification for multi-repo or oversized diffs. Residual risk accepted
  and revisited after the O8.3 drill.
- R2 Governance spam: hundreds of STANDARD change records degrade the audit
  trail's usefulness. Mitigate: dedupe by (app, action, target, day
  bucket) reusing the ITIL deterministic-id pattern
  (`_auto_incident_id` style), report batching, and problem-management
  rollups for recurrences (a repeated standard fix MUST spawn a problem
  record: healing forever is not fixing).
- R3 Ornith misclassification. Bounded: quiet pass is read-only, parse
  failures fold to `nothing` + alert, `auto_known` is off by default and
  last to enable.
- R4 Runaway or thrash (the parent spec's R7, stack-wide now). Bounded by:
  freeze wins everywhere, single-flight run claim, spend caps, idempotent
  act verbs, post-action verify with rollback, and crash-loop-style backoff
  on repeated failed fixes of the same target (three failed heals of one
  target = stop, incident, escalate).
- R5 Approval fatigue pushing Chef to rubber-stamp. Mitigate: the
  standard-catalog ratification loop is the pressure valve (any escalation
  the human approves identically three times is a candidate catalog entry,
  surfaced in the weekly review), and expiry keeps the pending list honest.
- R6 Cross-app blast radius (an OPS fix in one app degrading another).
  Mitigate: the brief is stack-wide by construction, verify re-observes ALL
  registered apps' conditions (not just the target's), and a post-action
  regression in any app rolls back and escalates.
- R7 Identity gaps before enforce mode: during permissive rollout,
  signatures are logged but not required, so attribution is convention.
  Same accepted posture as fleet Card 3.5, with the same
  permissive-then-enforce flip and key ceremony.
- R8 The ITIL fold amendment weakens governance if the `auto-normal` tag
  leaks to other writers. Mitigate: the fold checks the creating writer is
  the operator seat, the amendment lives in a protected file, and the
  guardrail suite asserts a non-operator-authored auto-normal change does
  NOT auto-approve.

## 8. Open decisions for Chef

- D1 The AUTO-NORMAL tier itself: accept "auto-approve unless vetoed" for
  reversible non-catalog actions (recommended: it is what makes the
  operator useful on novel-but-safe fixes), or force everything auto-applied
  into the ratified STANDARD catalog (stricter, more curation overhead,
  slower first-time fixes)?
- D2 Protected-manifest granularity: file-level v1 means benign refactors of
  `itil.py` or `engineering.py` always escalate. Accept the friction
  (recommended), or fund symbol-level diff analysis later?
- D3 Protected-path merges: human merges the PR by hand (v1, recommended),
  or may a human-signed approval record trigger the merge mechanically?
- D4 Seat identity: dedicated `operator` agent with its own capauth key
  (recommended), or Lumina holds the seat?
- D5 Freeze-time emergencies: may the operator EVER act while frozen (sev1,
  human unreachable)? v1 says never; confirm.
- D6 Cadence and budget: 15 min quiet tick, decision-pass daily USD cap
  (proposed: its own cap, initial $10/day separate from the autocode $25),
  escalation expiry 72h. Tune?
- D7 Architecture auto-flow default: pick = approval with `hold_cards`
  opt-out per escalation (recommended, confirms the parent design). Agree?
- D8 Wave-1 adapter order: fleet + skchat first (recommended: highest
  operational churn), then skgateway, skcomms, skmemory, skos. Reorder?

## 9. Closing position

The stack already contains every hard part of this design: a constitutional
CAB rule that refuses AI self-authorization, a merge gate broken code cannot
pass, a freeze primitive every actuator was born checking, signed writes,
an event log built for causal reasoning, and a discovery surface built for a
cold-start operator. The operator seat is not a new system; it is the
keystone that makes the existing systems one system. The human stops being
the pager-holder and becomes the constitution: rarely invoked, impossible to
overrule.

---

## Decisions RESOLVED (Chef, 2026-07-29)

- D1 Autonomy tier: **AUTO-NORMAL** (auto-apply standard catalog AND normal changes when
  risk != HIGH + rollback_plan present + operator-authored + zero rejection votes; standing human veto preserved).
- D2 Manifest granularity: **file-level** protected paths (precise; minor friction accepted).
- D3 Approved protected PRs: **mechanical merge** once the human has approved (approval is the gate).
- D4 Identity: **dedicated `operator` capauth identity** (own key; audit separates operator/lumina/chef).
- D5 Act while frozen: **NEVER** in v1 (freeze is absolute).
- D6 Cadence + budget: **15-min tick + alert-wake, $10/day** cap.
- D7 Architecture channel: **auto-flow** after the human picks an option (twin-gate governs the merge);
  per-escalation `hold_cards` opt-out remains.
- D8 Wave-1 adapter: **skchat first** (then skgateway, skcomms, skmemory, skos per O7).

Build order: O1 governance core -> O2 carve-out -> O3 adapter contract + fleet adapter ->
O4 loop (report-only) -> O5 approval surfaces -> O6 arch auto-flow -> O7 wave-1 adapters (skchat) ->
O8 autonomy ramp + drills (incl. the itil.py self-modification-escalates drill).
SAFETY RULE for the build itself: the carve-out WIRING (finalize hook, itil fold amendment, manifest
ceremony, plane-file rules) is human-reviewed, NEVER auto-merged, because it IS the guardrail machinery
and the carve-out that would protect it does not exist yet (bootstrap). Only PURE testable modules
(policy classifier, ITIL intent builders, protected-paths detector) build hands-off.
