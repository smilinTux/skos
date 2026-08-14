# SKWorld Code section + density pass: phased implementation plan

Date: 2026-08-11
Author: Fable (claude-fable-5)
Status: PROPOSED, Phase 0 FILED (epic fd59b412 and cards D-1 500662f6,
D-2 7ee35e9e, D-3 96a0de34 exist on the coord board; no other cards
created, no config touched, no code changed)
Specs: `../specs/2026-08-11-skworld-code-section-architecture.md`,
`../specs/2026-08-11-skworld-density-and-type-scale.md`
Related: skwatchdog plan `2026-08-10-skwatchdog-implementation.md` (the
watchdog builds the digest; this plan builds its native renderer and the
jobs view; neither duplicates the other)

> **Revision 2026-08-11 (rev 2).** Chef rejected project chat as a fifth
> preview tab (chat and artifacts competed for the same pane) and chose
> four columns on wide panes: rail, project chat, transcript, artifact.
> Chat is therefore a PRIMARY surface, not a Phase 3 nicety. Changes:
> C-7 re-scoped (the pane is now the artifact pane, artifacts only, plus
> a host slot for the collapsed Chat tab); C-11 narrowed to session
> grouping only and stays Phase 3; NEW card C-12 (project chat column)
> added in Phase 2. The Phase 0 epic and D-1/D-2/D-3 were already filed
> before this revision (IDs above); their numbering and scope are frozen
> and unchanged.

## 0. Ground rules

- No new stores: the Code pane is a view over hostd's registry, the cron
  ledger, the coord board, and the watchdog digest artifact. Owners stay.
- Auth stays capauth: audience `skcode`, scopes stream/inject/dispatch,
  PDP-decided writes, Bearer headers on HTTP, `?token=` only on WS.
- All model lists and inference via skgateway; never hardcode a model.
- The iframe survives at `/code/legacy` until Phase 2 exit criteria pass,
  then dies in the same release that flips the manifest to Grade A.
- Additive server changes only: every new hostd field/route must leave the
  existing iframe client byte-compatible.
- No em or en dashes in any emitted text or UI string.

## Phase 0: the density pass (ships first, independent)

The low-risk, high-visibility win, and it is deliberately decoupled: it
touches no server, no auth, no protocol, and every step has a one-line
revert. Detail in the density spec.

- D-1 lands the token system + new compact default + PRD amendment +
  accessibility golden tests + the literal-ban ratchet.
- D-2 lands shell chrome spacing + the top-7-file literal burn-down.
- D-3 lands the skcode client tokenization + pytest guard (repo
  skharness; also improves the legacy iframe for as long as it lives).

Exit criteria: Chef runs compact on his phone for two days and does not
flip back; golden tests green at OS scale 1.0/1.3/2.0; ratchet baseline
strictly smaller than 328; zero px font-size literals in the skcode
client.

## Phase 1: the thinnest native slice that beats the iframe

Read-only native Code pane: sessions rail + live transcript with the
render taxonomy + raw rail toggle, consuming ONLY endpoints hostd already
serves (`GET /sessions`, `GET /sessions/{sid}`, WS stream) plus one small
additive server change (event `seq`/`sid`/`source` fields). No composer,
no dispatch, no jobs list yet.

Why this already beats the iframe: real Bearer auth (no token in URL on
HTTP), native scrolling/selection/theme/density, phone-shaped navigation
(list then full-screen session), the taxonomy instead of the raw text
dump, and offline-honest reconnect. The iframe stays one tap away at
`/code/legacy`.

Exit criteria: watch a live autocode run and a live interactive session
from a phone over the funnel; kill the daemon mid-stream and see an
honest reconnect with no duplicate rows (the `(sid, seq, ts)` dedup
proving itself across the seq reset); standalone boot test green
(`shell == null`).

## Phase 2: interactive parity, jobs, project chat, and the Grade A flip

Composer (inject) + needs_input approve/deny (ratify), new-session
dispatch form fed by `/dispatch/targets`, session cancel, the artifact
pane (Diff, Logs, Raw tabs), archive paging, the Jobs list over the cron
ledger, the Digest tab rendering the watchdog artifact with working
`skworld://` deep links, and the project chat column (C-12). Ends with
the manifest grade flip B to A and iframe removal.

Chat moved up from Phase 3 in rev 2 because it is now load-bearing for
Chef's primary scenario (ask agents in chat on the left, watch the
transcript and Diff land on the right); it stays cheap because it mounts
the EXISTING native chat surface, zero new chat infrastructure. C-12
lands after C-5, because the composer disambiguation (chat Send vs
session Inject, spec section 7.1) can only be proven once both composers
exist.

Exit criteria: every operation the iframe client offered works natively;
Chef approves a needs_input from his phone; a watchdog digest line
deep-links into a live session; Chef asks for a change in project chat
and watches the transcript and Diff update without leaving `/code`; the
chat and inject composers are visually unmistakable (spec 7.1 chrome
rules verified at four-column and collapsed widths); iframe files
deleted.

## Phase 3: coordination surfaces and ACP

Session grouping by coord card/epic (C-11, narrowed in rev 2; the chat
work it used to carry moved to C-12 in Phase 2) and the ACP harness
adapter in skharness (verdict below).

## Coord epic + child cards (Phase 0 FILED; C/A cards proposed only)

Epic: `SKWorld Code section Grade A + density pass` (epic fd59b412 on
the board; D-1/D-2/D-3 filed as 500662f6 / 7ee35e9e / 96a0de34, scope
frozen). C-* and A-1 remain proposed for the staged lane. Sizing S under
a day, M one to two days, L several.

| # | Title | Repo tag | Size | Phase |
|---|---|---|---|---|
| D-1 | density tokens: SovereignDensity + spacing tokens + compact default + PRD amendment + OS-scale golden tests + literal-ban ratchet | repo:skworld-app | M | 0 |
| D-2 | shell chrome density + top-7-file fontSize burn-down (121 literals) | repo:skworld-app | M | 0 |
| D-3 | skcode client CSS tokenization (rem tokens, media-query consolidation) + pytest px guard | repo:skharness | S | 0 |
| C-1 | SessionEvent v2: additive seq/sid/source fields + per-session event persistence + `GET /sessions/{sid}/events` archive paging | repo:skharness | M | 1 |
| C-2 | skcode_client package skeleton: SkworldModule impl, standalone runner, shell==null CI boot + import-isolation grep gate | repo:skworld-app | M | 1 |
| C-3 | session store + WS tail client: Bearer HTTP, ?token= WS, re-mint on 401/1008, merge/dedup (sid,seq,ts), 3000-event live window (Buzz port, attributed) | repo:skworld-app | M | 1 |
| C-4 | render taxonomy + classifier + transcript list + raw rail + phone layout (list -> session screen) | repo:skworld-app | L | 1 |
| C-5 | composer + inject + ratify/needs_input approve-deny flow | repo:skworld-app | M | 2 |
| C-6 | dispatch form (targets-fed) + session cancel endpoint (`POST /sessions/{sid}/cancel`, dispatch scope, PDP) | repo:skharness + repo:skworld-app | M | 2 |
| C-7 | artifact pane (re-scoped rev 2): Diff/Logs/Raw tabs, artifacts only + panel-left shadow treatment + bottom-sheet variant on phone + host slot for the collapsed Chat tab at sub-four-column widths (slot only; chat content is C-12) | repo:skworld-app | M | 2 |
| C-8 | jobs view: hostd `GET /api/v1/jobs` (cron ledger tail, read-only) + Jobs rail section + staleness badges | repo:skharness + repo:skworld-app | M | 2 |
| C-9 | Digest tab: render watchdog `latest/` artifact, resolve `skworld://` links through the shell router | repo:skworld-app | S | 2 |
| C-10 | Grade A flip: hostd manifest grade A + entry.flutter_package, registry flip, delete iframe files + /code/legacy | repo:skharness + repo:skworld-app | S | 2 |
| C-11 | session grouping by coord card/epic in the rail (narrowed rev 2; chat moved to C-12) | repo:skworld-app | S | 3 |
| C-12 | NEW rev 2. project chat column: mount existing native chat surface scoped to the meta.project group + four-column tier and chat collapse-to-tab ladder (spec 7) + chat/inject composer disambiguation chrome (spec 7.1) + per-column follow-tail scroll rules + phone chat chip | repo:skworld-app | L | 2 |
| A-1 | ACP harness adapter: `harnesses/acp.py` normalizing session/update to SessionEvent, first target goose or claude-code-acp | repo:skharness | M | 3 |

Ordering: D-1 -> D-2 in sequence (merge-tested CI, keep diffs clean);
D-3 parallel. C-1 and C-2 parallel; C-3/C-4 sequential after them.
Phase 2 cards parallelize except: C-12 after C-5 (the inject composer
must exist before the two-composer disambiguation can be proven) and
after C-7 (it fills the Chat tab slot C-7 builds); C-10 last.

Card ID policy (rev 2): D-1/D-2/D-3 are FILED and frozen; C-1 through
C-10 and A-1 keep their meaning and IDs; C-11 is narrowed but still
means "grouping," so it keeps its ID; C-12 is the only new ID.

## Buzz reuse vs port vs write (Apache-2.0 permits direct reuse with attribution)

| Pattern | Verdict | Reason |
|---|---|---|
| Event merge/dedup (`mergeObserverEventWindows`, `(seq,timestamp)` key, live-wins, scroll anchor id) | PORT to Dart | ~100 lines of TS logic; cross-language so vendoring is impossible, but the algorithm and its comments port one-to-one. Attribution comment to agentSessionPanelLayout.ts |
| Render taxonomy (15 classes, tones, ToolStatus, error override, suppressed valve) | PORT to Dart, adapted | The classes and the two design choices (tone = blast radius, suppressed = noise valve) transfer; the classifier's tool table is rewritten for Claude Code/MCP tool names. SKWorld adds `diff`, generalizes relay-op to mcpOp, and tones everything |
| Raw event rail | PORT pattern | details/pre rows become ExpansionTile + mono SelectableText; trivial |
| `panel-left` two-layer negative-x shadow | REUSE values | CSS values reused verbatim in the (interim) web client; translated to two BoxShadows in Flutter with the comment carried over |
| Sub-xs type ramp values (11/10 px, rem-based) | REUSE values | micro=11 and badge=10 adopted as tokens on both Flutter and web surfaces; 8 px (3xs) rejected on touch-legibility grounds |
| px-text guard (`check-px-text-core.mjs` regexes incl. `(?<!-)` lookbehind, overrides allowlist) | REUSE regexes, WRITE harnesses | Regexes lifted verbatim into a pytest (skharness) and the ratchet test (Dart); running their Node script would add a toolchain for 2 regexes |
| Live window cap 3000 + archived paging split | REUSE constant, WRITE storage | The number and the live/archive split are adopted; SQLite is not (capped JSONL per session fits house style) |
| Projects forge UI (PR panels, commit detail, branch rooms) | WRITE LATER / SKIP for now | SKWorld's forge is GitHub + coord board; duplicating a forge UI is a product decision, not a pane feature. Revisit if self-hosted forge ever lands |
| buzz-acp / buzz-agent (Rust ACP harness) | WRITE own thin adapter | Rust crates do not embed in a Python daemon; what transfers is the protocol (v2) and the session/update normalization idea |
| Nostr relay/identity machinery | SKIP | Section 12 of the architecture spec: distraction now, seam preserved |

## The ACP recommendation (final)

**Adopt ACP narrowly: one harness adapter inside skharness
(`harnesses/acp.py`), Phase 3, size M. Do not make it the pane protocol.
Do not adopt buzz-acp. Do not expose skharness as an ACP agent yet.**

Grounds: skharness already isolates harness differences behind per-harness
parsers (claude_code stream-json today; codex/opencode/pi in autocode).
ACP (JSON-RPC 2.0 over stdio, protocol v2, spoken by Zed, JetBrains,
goose, buzz-agent, and Claude Code via the claude-code-acp shim) turns
"one parser per harness" into "one adapter for every ACP agent," which is
a genuine interop win at exactly the seam SKWorld already owns. It is NOT
a competitor to skgateway (model routing) nor to hostd's WS tail (capauth
scoping, PDP, funnel, audit), which is why it must not replace either.
The reason it is Phase 3 and not Phase 1: the dominant harness already
works through a proven parser, so ACP's value is future coverage, and
nothing in Phases 0 to 2 depends on it. Trade-off accepted: one more
protocol dependency in skharness, and the Claude Code ACP path drags a
Node shim, which is why the existing stream-json parser stays the default
for Claude Code even after A-1 lands.

## Risks and their handles

- **Compact density rejected by Chef's thumb**: one-line revert to
  comfortable; the scale tables make it exact.
- **Seq dedup across daemon restarts**: this is THE known trap (Buzz hit
  it, documented it); C-3's exit test kills the daemon mid-stream on
  purpose.
- **WS through the funnel on cellular**: the WS proxy path is live today
  for the iframe, so the risk is client-side reconnect quality, covered by
  C-3's backoff + re-mint logic.
- **Scope creep toward a forge UI**: explicitly out (Buzz table); the
  coord pane and GitHub remain the forge surfaces.
- **Two job surfaces by accident**: the architecture spec's section 8/9
  rule (view, never store; watchdog narrates, Code section watches) is the
  guard; C-8 and C-9 build renderers only.
- **Two composers, one wrong audience** (new in rev 2): the four-column
  tier puts the chat Send composer and the session Inject composer at the
  bottoms of adjacent columns; typing an agent instruction into the
  humans' channel is embarrassing, typing a chat message into a running
  agent is worse. Handle: the spec 7.1 chrome split (skchat visuals +
  "Send" vs mono/amber/target-chip + "Inject"), no shared focus
  traversal, and an explicit C-12 exit check at both four-column and
  collapsed widths.
- **Four-column tier rarely triggers on laptops** (new in rev 2): pane
  width >= 1500 plus the shell rail means a ~1580+ window; ordinary
  laptop windows get three columns and pay the chat-tab toggle. Accepted
  by Chef; the handle is refusing to shave column minimums, not
  pretending the tier fires everywhere. On phone the ask-left-watch-right
  layout does not exist at all (chat is a pushed screen).
- **Chat and transcript scroll competition** (new in rev 2): two
  auto-tailing columns side by side. Handle: per-column follow-tail with
  scroll-up disengage and a "jump to latest" pill; the scrolls are never
  linked. Built and tested in C-12.
- **CI tests the merge, not the tip** (skworld-app): keep D and C cards as
  small sequential merges; never stack unmerged branches.
