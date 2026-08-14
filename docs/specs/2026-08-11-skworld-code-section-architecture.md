# SKWorld Code section: Grade A native pane architecture

Date: 2026-08-11
Author: Fable (claude-fable-5)
Status: PROPOSED
Brief: `~/clawd/docs/fable-skworld-code-section-brief.md`
Companions: `2026-08-11-skworld-density-and-type-scale.md` (density pass),
`../plans/2026-08-11-skworld-code-section-implementation.md` (phases, cards,
Buzz reuse table, ACP recommendation)
Related: `2026-08-10-skwatchdog-architecture.md` (reconciled in section 9)

> **Revision 2026-08-11 (rev 2).** Chef reviewed the layout and rejected
> project chat as a fifth preview tab: chat and artifacts competed for the
> same pane, so in his primary scenario (ask agents for a change, watch
> the result land) he would toggle between the tab where he asked and the
> tab showing what came back. Decision: FOUR columns on wide panes (rail,
> project chat, transcript, artifact), with chat collapsing back to a tab
> at narrower widths. Section 7 reworked (new breakpoint ladder, collapse
> order, two-composer disambiguation, stated consequences), section 10
> updated (chat is a column, not a preview tab; everything else in it
> stands), section 14 lines 1 and 6 updated. The pane formerly called the
> preview pane is now the artifact pane and holds only artifacts
> (Diff, Digest, Logs, Raw). No other section changed.

## 1. One-paragraph summary

The Code section stops being an iframe and becomes what its own docstring
already promised: a Grade A native Flutter module (`packages/skcode_client/`
in skworld-app, implementing `SkworldModule` from `skworld_module_api`)
consuming skcode-hostd's existing WS tail over the 443 funnel with real
capauth Bearer headers. skcode-hostd needs no new architecture, because it
turns out to BE skharness: the daemon is `python -m skharness`, live today on
noroc2027 at 100.108.59.57:9394, and its API (sessions, WS stream, inject,
ratify, dispatch) is already the right backend for a native client. The pane
ports three specific Buzz patterns with attribution (the event merge/dedup
discipline, the activity render taxonomy with read/write/admin tones, and the
raw event rail) and becomes the single view over jobs and harness runs: live
hostd sessions, autocode runs (which already execute inside skharness), cron
ledger runs, and the skwatchdog digest, without creating any new job store.
ACP is adopted narrowly as one future harness adapter inside skharness, not
as the pane protocol. Nostr interop is declared a distraction.

## 2. Corrections to the brief (verified against code)

The brief asked for verification. Findings, bluntly:

1. **skcode-hostd is not a missing repo. It is skharness.** There is no
   `skcode-hostd` repository anywhere; the daemon is the `skharness` Python
   package at `/home/cbrd21/clawd/skcapstone-repos/skharness` (GitHub
   `smilinTux/skharness`, private), run as
   `~/.skenv/bin/python -m skharness --host 100.108.59.57 --port 9394`, via
   the user unit `skcode-hostd.service`, editable-installed so the repo IS
   the running daemon. Nothing about that part of the design is blocked.
2. **The hostd web client is one file.** The entire skcode web client is
   `skharness/src/skharness/client/index.html`, 675 lines, no framework, no
   build step, one style block, all font sizes hardcoded px. The density
   pass on surface 3 is therefore a small tokenization job, not a CSS
   codebase project (details in the density spec).
3. **Buzz's session panel has no composer.** The brief's approved layout
   puts a human+agent composer under the transcript and implies Buzz does
   the same. It does not: `ManagedAgentSessionPanel` and
   `AgentSessionTranscriptList` are read-only, and input lives in the
   channel view. The composer is still RIGHT for SKWorld, because hostd
   already exposes `POST /sessions/{sid}/inject` and `ratify` (Buzz's agent
   input rides its relay instead). Cite hostd's B2 interactive design as
   the precedent, not Buzz.
4. **Buzz's ACP vision doc overstates itself.** `VISION_AGENT.md` says "up
   to 8 concurrent sessions"; the code says
   `BUZZ_AGENT_MAX_SESSIONS` defaults to `usize::MAX` (unlimited), and the
   8 nearby is `max_parallel_tools`. The self-summarization (handoff)
   machinery is real. ACP protocol version in their code is 2.
5. **Minor Buzz corrections.** `MAX_OBSERVER_EVENTS = 3000` lives in
   `observerRelayStore.ts` (module-private), not `agentSessionPanelLayout.ts`
   which only cites it. The `permission` render class is emitted by
   `agentSessionTranscript.ts`, not the tool classifier. The Tailwind
   `fontSize` extension has five entries, not three (`title` and `nsec-key`
   beyond `2xs`/`3xs`/`badge`). Transcript timestamps render `text-xs`, not
   `text-2xs`. None of this changes what is worth taking.
6. **The brief's hostd auth description checks out**, verified in
   `skchat/src/skchat/webui.py` (same-origin `/skcode/*` GET/POST proxy and
   a WS proxy for the session tail, token forwarded verbatim, host closes
   1008 on a bad token) and `skchat/src/skchat/shell_modules.py` (manifest
   aggregation, operator-facet strip).
7. **The module registry detail the brief missed:** the Dart
   `ModuleManifest` has no `audience` or `deeplinkPrefix` field; those live
   in the JSON manifest and `ModuleNav` (`skworld_module_api`). The Grade A
   flip is a JSON manifest edit plus a Flutter package, exactly as the
   standard says ("never a contract change").

## 3. What exists (the verified substrate)

### 3.1 skcode-hostd (skharness) API

Declared in `skharness/src/skharness/daemon.py` with a coverage test that
asserts every route is classified:

- Public: `GET /.well-known/skworld-module.json`, `GET /`, `GET /app`.
- Scope `skcode.stream` (read): `GET /api/v1/hosts/self`,
  `GET /api/v1/sessions`, `GET /api/v1/sessions/{sid}`,
  `WS /api/v1/sessions/{sid}/stream`.
- Scope `skcode.inject` (write, PDP-decided): `POST .../ratify`,
  `POST .../inject` (body `{"text": ...}`; audit stores sha256 + length,
  never raw keystrokes).
- Scope `skcode.dispatch` (PDP-decided): `GET /api/v1/dispatch/targets`,
  `POST /api/v1/dispatch` (body `{repo, branch, profile, permission_mode,
  mode, prompt, harness, model}`).

WS tail event shape (`events.py::SessionEvent.to_dict()`): every frame is
`{"type": ..., "text": ..., "ts": float, "data": {}}` with
`type in {status, assistant_text, tool_call, tool_result, diff, needs_input}`,
produced by `harnesses/claude_code.py` from Claude Code stream-json.
`tool_call.data = {id, name, input}`, `tool_result.data = {tool_use_id,
is_error}`, `status.data.subtype in {init, result, attached, ...}`.

Auth: `Authorization: Bearer <wire>` on HTTP; `?token=` on the WS query
(browsers cannot set WS headers); deny-all verifier ON in prod
(`SKCODE_REAL_VERIFIER=1`); dispatch repos allowlisted to
`skchat, skworld-app, skcapstone` (skos/skharness excluded, self-mod hazard).

### 3.2 The shell side

- `/code` is a `ShellRoute` child rendering `SkcodePane` (the iframe).
- `kBuiltinModules` registers `skcode` (title "Code", nav, order 15,
  grade B comment).
- The Grade A mount pattern already exists once:
  `module_host_screen.dart` builds the skchat module and calls
  `module.build(context, AppShellContext(...))` with theme, bus, and an
  `AuthContext` whose `token()` mints audience tokens and never throws.
- `audienceTokenForAudienceProvider('skcode')` already mints and caches the
  skcode wire token with a 30 s expiry margin, returning null on failure so
  callers degrade tokenless.
- There is no native WS consumer in the app today except FaceTime signaling;
  the standing pattern for live data is Timer-poll into a StreamProvider,
  and one hand-rolled SSE consumer (cluster). The Code pane will be the
  first real native WS data stream, using `web_socket_channel` (already a
  dependency) + `daemonWsUrlProvider`.

## 4. The Grade A design

### 4.1 Where the code lives

`packages/skcode_client/` in skworld-app (workspace member, like
`skchat_ui`), implementing `SkworldModule` (`id: 'skcode'`). Rules from the
module contract standard, held:

- Imports only `skworld_module_api` (grep gate in CI), plus its own deps.
- Boots with `shell == null` (standalone runner with its own capauth login)
  in CI; when mounted, uses the shell's theme, bus, and `AuthContext`.
- The hostd-emitted `skworld.module.json` flips `grade: "A"` and gains
  `entry.flutter_package: "skcode_client"`; `deeplinkPrefix
  skworld://skcode/`, audience and scopes unchanged. Registry flip in
  `module_registry.dart`: `skcode` grade `'A'`, route `/code` now mounts the
  native module through the same pattern as `module_host_screen.dart`.
- The iframe pane stays reachable at `/code/legacy` behind a visible "open
  classic" affordance until Phase 2 parity, then dies with its
  `skcode_web_embed*.dart` files.

### 4.2 Auth: what the audience-token dance becomes

The native client keeps the same-origin funnel path (`<origin>/skcode/...`)
so nothing new is exposed and phones off-tailnet keep working, but the
`?token=` hack disappears from HTTP:

- HTTP: `Authorization: Bearer <wire>` set directly by the Dart client
  (Dio/http), token from `AuthContext.token()` / the audience token service.
  No token in any URL, no CORS dance, no opaque-origin iframe containment
  needed at all.
- WS: stays `?token=` on the query string. Flutter web's WebSocket cannot
  set headers, and hostd + the skchat WS proxy already speak this shape;
  keeping one WS auth path for native and web targets beats a
  per-platform fork. The 1008 close maps to a "pair this device / token
  expired" state that triggers a re-mint and reconnect (single retry, then
  surfaced).
- Token refresh: on HTTP 401 or WS 1008, invalidate the cached audience
  token, re-mint once, retry; then fail visibly. The tokenless degrade
  (mint off) renders the same gated empty state the iframe showed, natively.

### 4.3 Transport and session store

One `SkcodeSessionStore` per open session (Riverpod):

- `GET /sessions` list (poll at 15 s while the rail is visible; cheap).
- `WS /sessions/{sid}/stream` for the focused session; reconnect with
  jittered backoff; on reconnect, re-fetch the archive window and merge.
- Live window capped at 3000 events (port Buzz's `MAX_OBSERVER_EVENTS`
  choice); archive paging extends beyond it (5.3).

## 5. The event model (question 2)

### 5.1 SessionEvent v2, one shape for everything that streams

SKWorld's ObserverEvent equivalent is skharness's `SessionEvent`, extended
additively (old clients ignore unknown keys, the iframe client is
untouched):

```json
{
  "type": "tool_call",
  "text": "Edit",
  "ts": 1765430000.123,
  "data": {"id": "...", "name": "Edit", "input": {}},
  "seq": 412,
  "sid": "s-1a2b",
  "source": "interactive"
}
```

- `seq`: per-session monotonic, assigned at append by the session buffer.
  It resets when the daemon restarts. This is exactly the trap Buzz
  documented (`seq` is "a monotonic counter local to one agent process...
  it resets to 1 after every process restart while timestamp keeps
  climbing"), so the dedup key is `(sid, seq, ts)`, never `seq` alone, and
  the stable row id (scroll anchor shared by transcript and raw rail) is
  `"$sid:$seq:$ts"`.
- `sid`, `source`: in-band identity so one merged multi-session view is
  possible later without re-parsing URLs. `source` values: `interactive`
  (dispatched or attached operator sessions), `autocode` (engine runs),
  `attach`.
- Emitters: hostd's harness parsers emit for interactive sessions today.
  Autocode runs already execute inside skharness; the orchestrator's runs
  register as sessions with `source: "autocode"`, which makes them appear
  in the same rail for free. That is the answer to "one event shape that
  serves all three": hostd sessions and autocode runs are literally the
  same stream. Atlas does NOT emit SessionEvents; Atlas is condition-shaped
  and stays out of this stream (its brief and parked decisions surface via
  the watchdog digest, section 9).
- Scheduler/cron jobs are not sessions and are not force-fitted into this
  shape. They surface as `JobRun` records read from the cron ledger
  (section 8), with links into logs.

### 5.2 Where dedup lives

Client-side, in `SkcodeSessionStore`, a direct port of
`mergeObserverEventWindows`: live and archived windows merge, dedup on
`(sid, seq, ts)`, live copy wins (it may carry incremental transcript
mutations), sort ascending `(ts, seq)`. Roughly 100 lines of Dart plus
tests; Apache-2.0 attribution comment pointing at
`buzz/desktop/src/features/agents/ui/agentSessionPanelLayout.ts`.

### 5.3 Where the archive lives

hostd persists a bounded per-session event file
(`~/.skcapstone/skcode/sessions/<sid>/events.jsonl`, size-capped) and
serves `GET /api/v1/sessions/{sid}/events?before_seq=N&limit=M` under
`skcode.stream`. Note: whether the current in-memory buffer already
persists must be verified at implementation time; the endpoint contract
above is the target either way. Buzz uses SQLite here; a capped JSONL per
session is enough at SKWorld's volumes and matches house style (flat files
as truth).

## 6. The render taxonomy (question 3)

Ported to Dart as two enums plus a classifier function, attribution to
`agentSessionTypes.ts` / `agentSessionToolClassifier.ts`:

- `ActivityRenderClass`: `message, fileEdit, fileRead, skillRead, shell,
  mcpOp, status, thought, plan, permission, diff, image, error, generic,
  raw, suppressed` (Buzz's `relay-op` generalizes to `mcpOp`; `diff` added
  because hostd emits a first-class diff event Buzz lacks).
- `ActivityTone`: `read, write, admin, neutral`. Unlike Buzz (which leaves
  harness tools toneless), every class gets a tone, because the whole point
  is scanning blast radius.
- `ToolStatus`: `executing, completed, failed, pending` (tool_call opens
  executing; the matching tool_result closes it, `is_error` flips failed,
  mirroring Buzz's error override).

Mapping (hostd event type x tool name):

| Input | Render class | Tone |
|---|---|---|
| `assistant_text` | message | neutral |
| `status` (init/attached/result ok) | status | neutral |
| `status` result `is_error` | error | neutral |
| `needs_input` | permission | admin |
| `diff` | diff | write |
| `tool_call` Read, Glob, Grep, WebFetch, WebSearch | fileRead | read |
| `tool_call` Edit, Write, NotebookEdit | fileEdit | write |
| `tool_call` Bash | shell | write |
| `tool_call` Skill | skillRead | read |
| `tool_call` Task/Agent | generic ("Launched agent") | write |
| `tool_call` TodoWrite | plan | neutral |
| `tool_call` `mcp__<server>__<tool>` | mcpOp | verb heuristic: get/list/search/status/show read; kms/fortress/trustee/rotate admin; else write |
| `tool_result` with `is_error` | error override on the open call | keeps tone |
| unknown tool | generic ("Ran tool") | neutral |
| harness heartbeat noise | suppressed | neutral |

`suppressed` is kept as the explicit noise valve (Buzz uses it for their
stop hook); everything suppressed still appears in the raw rail. The raw
rail itself is Buzz's `RawEventRail` shape: expandable rows, `#seq` +
one-line description + timestamp, pretty-printed JSON payload, mono type.

## 7. Layout and responsive behavior (question 1)

Revised per rev 2. Chef's driving scenario is: ask agents for a change in
project chat, watch the result land in the transcript and artifact pane
without switching tabs. So on wide panes project chat is a first-class
COLUMN between the rail and the transcript, and the artifact pane (the
pane rev 1 called the preview pane) holds only artifacts: Diff, Digest,
Logs, Raw. Ask on the left, watch it land on the right.

```
WIDE (pane >= 1500)
+------+---------------+--------------+------------+
| RAIL | PROJECT CHAT  | TRANSCRIPT   | ARTIFACT   |
| sess | chef: fix the | [read] x.py  | Diff       |
| jobs |   density bug | [edit] x.py  | +61 -38    |
|      | lumina: on it | [shell] test | x.py       |
|      | atlas: filed  | ok 892 pass  | y.dart     |
|      |   card D-1    |              |            |
|      | ___ send      | ___ inject   |            |
+------+---------------+--------------+------------+
```

**Breakpoints are measured with a LayoutBuilder on the pane's OWN width,
never screen width**, so the numbers hold when the shell rail steals
~80 px. The full ladder, ultrawide to phone:

- **Four-column (pane width >= 1500)**: rail ~280 | project chat ~320 |
  transcript flex (never below ~540) | artifact ~360. Rail: Sessions
  (live first, grouped interactive/autocode) and Jobs (section 8). Chat
  column: the mounted skchat surface for the project group (section 10),
  chat composer at its foot. Transcript: rendered through the taxonomy,
  inject composer beneath it (visible when the token carries
  `skcode.inject` and the session is interactive; a `needs_input` event
  pins a permission banner with Approve/Deny wired to ratify/inject
  directly above the composer). Artifact pane tabs: Diff (latest `diff`
  events, per-file), Digest (section 9), Logs, Raw (the rail). The
  artifact pane's left edge gets the two-layer negative-x shadow
  treatment (hairline + soft lift) ported from Buzz's `panel-left` token,
  translated to `BoxShadow` in Dart and kept as CSS in the legacy client.
- **Three-column (1200 to 1499)**: chat collapses FIRST, becoming the
  first tab of the artifact pane ("Chat | Diff | Digest | Logs | Raw"),
  with an unread badge. Chef accepted this trade: at these widths the
  screen cannot afford four honest columns, and chat degrades cheapest
  because the same thread is always one tap away in the normal Chats tab.
  The transcript is never the casualty; it is the primary work surface.
- **Two-column (900 to 1199)**: rail + transcript. The artifact pane
  (still carrying the Chat tab) becomes a toggled overlay docked right,
  same shadow; this is exactly the case that token exists for.
- **Phone (< 900, half of Chef's ops happen here)**: the rail IS the
  `/code` landing screen (sessions + jobs list, badge on needs_input).
  Tapping pushes `/code/s/:sid` as a full screen: transcript + inject
  composer, artifact tabs via a swipe-up bottom sheet (the app's existing
  drawer-sheet gesture), raw rail as an exclusive-mode toggle replacing
  the transcript (Buzz's `RawRailLayout` hidden/side/exclusive, where
  phone maps side to exclusive). Project chat on phone is a header chip
  on the landing and session screens that pushes the group's native
  skchat thread screen. Said plainly: the ask-left-watch-right layout
  does not exist on a phone; it becomes ask, then switch back. That is a
  real limitation of the decision, not something this spec can style away.

**Collapse order and why**: chat first (survives as a tab, always
reachable from Chats), artifact pane second (bursty content, works as an
overlay), transcript last (the primary surface). The rail never collapses
above phone width; it is the navigation spine.

### 7.1 Two composers, one hard rule: they must be unmistakable

The four-column tier puts two live composers at the bottoms of ADJACENT
columns. One talks to people (chat send, delivered to the skchat group);
one talks to a running agent process (session inject, delivered to
`POST /sessions/{sid}/inject`). Typing into the wrong one is the worst
cheap mistake this layout can produce, so the distinction is enforced in
chrome, not left to position:

- **Chat composer**: standard skchat visuals exactly as the Chats tab
  renders them (rounded field, chat accent), placeholder
  `Message #<repo>`, button verb **Send**.
- **Inject composer**: terminal-styled: mono font, flat field, write-tone
  (amber) left border, a persistent non-dismissable target chip reading
  `INJECT -> <sid>` inside the field, button verb **Inject**. Rendered
  only when the token carries `skcode.inject` and the focused session is
  interactive.
- The two never share focus traversal (Tab does not move between them),
  and they never share styling tokens. When chat is collapsed to a tab,
  at most one of the two composers is visible in the artifact pane at a
  time, which removes the adjacency ambiguity at narrower widths.

### 7.2 Consequences Chef should know (stated, not papered over)

- **Horizontal budget**: pane >= 1500 plus the shell rail's ~80 px means
  the WINDOW needs roughly 1580+. A 1512-wide laptop window lands in
  three-column and pays the chat-tab toggle; four-column is effectively
  an external-monitor and ultrawide tier. This is deliberate: do NOT
  shave column minimums to force four columns onto laptop widths, because
  a 260 px chat column and a 480 px transcript are both useless.
- **Two auto-following scrollables side by side**: chat and transcript
  both tail live content. Each column owns its scroll independently;
  follow-tail disengages on user scroll-up and offers a per-column
  "jump to latest" pill. The two scrolls are never linked.

Deep links per the contract: `skworld://skcode/session/<sid>` maps to
`/code/s/<sid>`; `skworld://skcode/digest` opens the Digest tab. This is
what makes watchdog digest lines clickable end to end. Unaffected by
rev 2.

## 8. Jobs and harnesses as first-class objects (question 4)

One rule: **the Code section is a view, never a store.** Owners stay where
they are.

| Object | Owner (truth) | How it appears | Actions from the pane | Gate |
|---|---|---|---|---|
| Interactive session | hostd registry | Sessions rail, live | watch; inject text; ratify/approve needs_input; cancel | stream / inject (PDP) / dispatch for cancel (PDP) |
| Autocode run | skharness engine (sessions with `source: autocode`) | Sessions rail, autocode group | watch; cancel; open PR link | stream / dispatch (PDP) |
| New run | hostd dispatch | "New session" form fed by `GET /dispatch/targets` (repo, branch, profile, harness, model; model list via skgateway-backed targets, never hardcoded) | dispatch | dispatch (PDP, repo allowlist enforced server-side) |
| Cron/scheduler run | cron ledger `~/.skcapstone/logs/cron-ledger.jsonl` + jobs.yaml | Jobs list (read-only Phase 2): last run, status, staleness | none in v1 (view + link to logs); run-now deliberately deferred | stream |
| Watchdog digest run | scheduler (it is a cron job) + digest artifact | Jobs list row + Digest tab content | open digest; open linked objects | stream |
| Staged coord card (Proposed lane) | coord board | linked from autocode session rows and digest lines | deep-link to the existing `/coord` pane to release; NO release button in the Code pane | coord's own gates |

The Jobs list is served by a small read-only hostd endpoint
(`GET /api/v1/jobs`, scope `skcode.stream`) that tails the local cron
ledger, because hostd already runs on the node that owns the ledger and
already has the auth plumbing; no new daemon, no new store. Cancel is the
one genuinely new hostd action (`POST /sessions/{sid}/cancel`), and it
rides the dispatch scope through the same PDP decision path as dispatch and
inject. Releasing staged cards stays in the coord pane on purpose: one
surface per authority, and the coord pane already exists at `/coord`.

## 9. skwatchdog reconciliation (no second job surface)

Chef's phrase was "have all these jobs n harnesses run under the code
section." Reconciliation with the 2026-08-10 skwatchdog design, explicitly:

- **The watchdog stays exactly as specced**: an skos capability, daily
  digest artifact under `~/.skcapstone/watchdog/digests/` published beside
  the Atlas brief, DM delivery, findings flowing to GTD/coord. Nothing
  moves into the Code section's backend.
- **The Code section becomes the digest's native renderer.** The artifact
  pane's Digest tab fetches the published digest (the `latest/` artifact
  over https, the load-bearing link form the watchdog spec already
  mandates) and renders it with every line's `skworld://` uri now actually
  resolvable, because the shell router is the one place those links were
  always meant to land. This closes the loop the watchdog spec left open
  ("nothing outside the Flutter shell resolves it").
- **Watchdog runs appear in the Jobs list** like any other scheduled job,
  via the cron ledger row for `watchdog-digest`, with staleness surfaced
  (the same signal Atlas's Phase 4 `WatchdogDigestFresh` condition watches).
- **Division of labor**: watchdog = collect + narrate + file (daily, DM +
  artifact); Code section = watch + steer + read (live, interactive). The
  digest links INTO Code section sessions (`skworld://skcode/session/...`
  for autocode runs it narrates); the Code section links OUT to the digest.
  Neither owns the other's data. There is exactly one jobs surface (the
  Code pane view) over exactly one set of job owners (scheduler ledger,
  hostd registry), and exactly one narrative surface (the digest).

## 10. Multi-agent and the project chat (questions 5 and 6)

- **Session model: one agent per session, coordination in chat.** A hostd
  session is one harness process; that stays. SKWorld's version of Buzz's
  several-agents-per-channel is several SESSIONS grouped by the coord card
  or epic they serve (the rail groups by card tag when present), with the
  humans-and-agents conversation happening where SKWorld conversations
  already happen: skchat, where agents are already first-class peers.
  Atlas coordinates work by filing and routing cards, not by joining
  sessions; that boundary (Atlas actuates through the board, never through
  a live session) is a safety property and stays.
- **Project chat: it is a skchat group, full stop.** A group carrying
  `meta.project = repo:<name>` binds a channel to a repo/project. Rev 2:
  the Code pane mounts it as a first-class COLUMN between the rail and
  the transcript on wide panes (a tab in the artifact pane at narrower
  widths, a pushed thread screen on phone; ladder in section 7), not as a
  preview tab. What it mounts is unchanged: the EXISTING native chat
  thread surface (the `buildLiveSkchatModule()` / live_chats_surface
  machinery already built for the chats tab), scoped to that group. Zero
  new chat infrastructure, PQ crypto and history for free, and the same
  thread is reachable from the normal Chats tab. A Code-section-native
  thread store would be a second chat system and is rejected. The
  section 8 rule extends here unchanged: the chat column is a VIEW over
  skchat's store; the Code section owns none of the messages.

## 11. ACP evaluation (question 7)

**Verdict: adopt narrowly, later. ACP becomes one harness adapter inside
skharness (Phase 3). It does not become the pane protocol, and buzz-acp's
relay machinery is not adopted.**

The evidence:

- ACP is JSON-RPC 2.0 over stdio between a client and an agent; Buzz's
  agent implements protocol version 2; the ecosystem (Zed, JetBrains,
  buzz-agent, goose, the claude-code-acp adapter) is real and growing.
- SKWorld already has the exact seam ACP slots into: skharness's harness
  adapters. `harnesses/claude_code.py` is a hand-written parser for Claude
  Code's proprietary stream-json; the autocode engine carries additional
  adapters (codex, opencode, pi). Every new harness today costs a new
  parser. One `harnesses/acp.py` that speaks ACP and normalizes
  `session/update` notifications into `SessionEvent` buys every current and
  future ACP-speaking agent (goose, Codex via adapter, Claude Code via the
  claude-code-acp shim, buzz-agent itself) for one adapter's cost.
- ACP is NOT a second protocol for skgateway's problem. skgateway routes
  model inference; ACP frames agent sessions and tool traffic. Orthogonal
  layers: an ACP-driven agent still points its model calls at skgateway.
- The pane protocol stays hostd's WS tail, because that is where capauth
  audience scoping, the PDP decision path, the funnel proxy, and the audit
  trail already live. Exposing raw ACP to the shell would mean rebuilding
  all four for a second wire.
- Why later and not now: the dominant harness (Claude Code) already works
  through a proven parser, and the Claude Code ACP path itself needs a
  Node shim. The win is future coverage, not present capability. Size M,
  Phase 3, behind the existing harness port so nothing upstream changes.
- Not recommended: exposing skharness AS an ACP agent (so Zed could drive
  it). No consumer demand today; revisit if a desktop-editor workflow
  appears.

## 12. Nostr/Buzz interop (question 9)

**Distraction, for now.** The architectural echo is real (both systems:
sovereign per-participant crypto identity, append-only signed logs,
self-hosted), but interop means mapping capauth PGP/DID identities onto
Schnorr keypairs and adopting NIP-34 forge semantics, for zero current
users on the other end. skchat's :7447 relay keeps doing its current job.
The one thing worth keeping warm: if SKWorld ever wants to federate with
external Buzz communities, the relay is the natural seam, and nothing in
this design forecloses it. No card.

## 13. Module contract impact

- hostd manifest: `grade` B to A, add `entry.flutter_package`. Everything
  else (id, audience, scopes, deeplinkPrefix, health, operator facet with
  conditions `HostdReady, SessionsHealthy, RegistryConsistent,
  AuthEnforced`) is untouched. Per the standard this is "a manifest edit
  plus a package, never a contract change."
- `skworld_module_api` v0 stays frozen; `skcode_client` consumes it as-is.
- New hostd routes (`events` archive, `jobs`, `cancel`) extend the daemon
  route table and its coverage test; `cancel` joins the PDP-decided set.
- The operator facet's `conditions` list is unchanged, so no Atlas adapter
  drift.

## 14. Answers to the brief's questions in one line each

1. Layout: rail + project chat + transcript/composer + artifact pane,
   four/three/two/one columns by pane width (chat collapses first, to a
   tab), phone gets list-then-screen with a bottom-sheet artifact view
   and a chat chip (section 7).
2. Event model: SessionEvent v2 (`seq, sid, source` added), hostd emits for
   interactive AND autocode (same stream), dedup `(sid, seq, ts)` client
   side, archive as capped JSONL paged from hostd (section 5).
3. Taxonomy: 16 render classes + 4 tones ported from Buzz with a
   hostd-specific mapping table and an every-class-gets-a-tone rule
   (section 6).
4. Jobs: the Code pane is a view over hostd sessions + the cron ledger;
   actions are watch/inject/ratify/cancel/dispatch, all capauth-scoped and
   PDP-decided; card release stays in coord (section 8).
5. Multi-agent: one agent per session, sessions grouped by card, the
   conversation lives in skchat (section 10).
6. Project chat: an skchat group tagged with the repo, mounted as a
   first-class column on wide panes (tab when narrow) via the existing
   native chat surface (section 10).
7. ACP: adopt as one skharness harness adapter in Phase 3; never the pane
   protocol (section 11).
8. Density: companion spec.
9. Nostr: distraction now, seam preserved (section 12).
10. Phasing: density ships first; thinnest native slice is read-only
    sessions + transcript; plan doc.
11. Reuse/port/write: table in the plan doc.
