# CapAuth Operator Identity and Token Lifecycle

**Date:** 2026-08-14
**Author:** Fable (claude-fable-5), from Lumina's brief
(`~/clawd/docs/fable-capauth-token-lifecycle-brief.md`)
**Status:** Proposed. Companion plan:
`~/clawd/skos/docs/plans/2026-08-14-capauth-token-lifecycle-implementation.md`
**Scope:** capauth (PDP, tokens), skchat (audience-token mint), skharness
(skcode-hostd PEP), skworld-app (Code section client), skos (watchdog).

---

## REVISION 2026-08-14 (later the same day): facts changed after assessment

Verified against live state on noroc2027 and capauth main `7b5f0a6`. The
design below STANDS; the following facts moved under it. The fleet-wide
view now lives in the companion spec
`2026-08-14-skworld-trust-model-coherence.md` (COH-* cards).

1. **Root rotated mid-session.** The operator root is now
   `BD7EEECA23D90A594400751CFDB582D9CB7272A6`
   (`Chef (SK Sovereign Root) <chef@skworld.io>`); the old
   `D8920EA8...` is recorded as retired in `~/.skcapstone/cluster.json`.
   All references below to "the capauth signing key" mean the new root.
   Note: the secret key is in the noroc2027 keyring while
   `capauth/identity/custody.json` declares it offline; see coherence
   spec section 8 (COH-7).
2. **The durable token store was pruned to 5 tokens** (213 old-root
   tokens archived to `capauth/retired-keys/stale-tokens-20260814/`).
   All 5 are signed by the new root, none revoked. A second,
   audience-token store with 5,000+ files exists at
   `~/.skcapstone/capauth/security/tokens/` and is NOT what the PDP
   reads; the dual-root problem is coherence spec section 5 (COH-1).
3. **`chef@skworld.io` is now an ENROLLED subject** (4 VERIFIED
   devices) AND holds a 19-capability token (`fc9961c9`, no expiry)
   including `skcode.dispatch` and `skcode.inject`, signed by the new
   root. Section 3.3's enrollment ceremony has therefore already
   happened in substance. What remains of it is verification and
   repeatability, not creation (see plan revision: CAP-6 shrinks).
4. **Signing was silently broken before today.** capauth `#38`
   (`7b5f0a6`) removed a forced empty passphrase that meant
   `issue_token` never actually signed. This retroactively explains the
   rot: tokens presumed signed were not.
5. **Phase 0 is DONE, executed by another session.** Interim grant
   `db78de08` to `lumina@chef.skworld.io` (`skcode.dispatch` +
   `skcode.inject`, 168h, new-root-signed) expires
   **2026-08-21T22:38:12Z**. An earlier unsigned attempt (`5527082c`)
   was revoked and is in the revocation list. Chef is unblocked.
6. A separate concurrent fix addresses `decide()` not verifying token
   signatures and `issue_token` storing on signing failure. This spec
   continues to assume signature verification as an invariant; the
   issuer-policy layer ON TOP of it is coherence spec COH-2.

Consequence for section 3.3: read it as historical rationale. The
enrollment exists; the open question moved to how the mint seam reaches
it (section 3.2 / CAP-7, unchanged). Consequence for section 4.2's
grant registry: the chef 19-cap no-expiry token and the three 6-cap
agent tokens are now the canonical examples of orphan grants (live
tokens with no declaration behind them); registry backfill for all 5
survivors is part of CAP-2's acceptance.

---

## 1. Verdict: refactor, bounded to one seam, plus a new lifecycle layer

**Refactor.** The subject model for the Code section is wrong, and it is wrong
at exactly one seam: the audience-token mint in
`skchat/src/skchat/webui.py` (`POST /api/v1/audience-token`) anchors the token
subject to `capauth.resolve_agent_identity()`, the daemon's own identity,
regardless of who authenticated. Everything downstream is correct and stays:
the PDP (`capauth.authz.decide`) is deterministic and fails closed, the PEP
(`skharness/daemon.py`) enforces scope plus PDP floor per request, the
verifier extracts the subject from the verified token and never from the
caller. The refactor is not a rip-out. It is:

1. **Derive the minted subject from the authenticated primary credential**
   instead of a constant. One function changes.
2. **Enroll the human operator as a first-class subject** so that derivation
   has somewhere to land.
3. **Add a token lifecycle layer** (grant registry, renewer, CLI,
   visibility) that does not exist today in any form.

Why not a patch (just re-mint Lumina's expired token and bolt a cron on it):
because `skcode.dispatch` is RCE and every session launches with
`--dangerously-skip-permissions`, so the PDP decision is the entire identity
control surface. Auto-renewing a grant whose subject conflates three
principals automates the incoherence: the audit trail stays unanswerable on
the one question that matters for RCE ("human or agent?"), and revocation
stays non-separable (cutting the UI off cuts the agent off and vice versa).
Chef's own instruction ("give lumina token only if she needs access to the
interface, she would not be using it, only a human would") is a correct
statement of the target model. The patch would contradict it permanently.

Why the refactor is cheap enough to justify: the identity chain already
carries the human's identity to within one function call of the mint. The
primary-auth gate (`request_is_primary_authenticated`) validates an
operator-session JWT or a signed FQID assertion before any token is minted.
The credential that proves WHO is asking is in hand at the moment the subject
is chosen; today it is validated and then discarded. No new protocol, no new
crypto, no PDP change is needed to stop discarding it.

---

## 2. Verified findings, and corrections to the brief

Everything in the brief's section 1 and 2 checked out against the code:

- `_SKCODE_RULES` in `capauth/src/capauth/authz.py` requires
  `EnrollmentMode.VERIFIED` for both `skcode.dispatch` and `skcode.inject`;
  `skcode.stream` is deliberately scope-only.
- `decide()` requires BOTH a satisfying enrollment mode AND an active,
  unrevoked capability token; every uncertainty denies.
- The mint route's subject is server-derived
  (`resolve_agent_identity()`), never caller-supplied, and the
  anti-laundering rule holds: an audience token cannot mint another
  (`request_is_primary_authenticated` refuses the audience-token class).
- `skharness/serve.py::build_capauth_verifier` reads the subject off the
  verified token payload; `daemon.py` passes it to the PDP. So the PDP
  subject IS the mint's subject IS the daemon identity. The attribution
  diagnosis is confirmed end to end.
- There is no `capauth token grant` CLI; the `token` group exposes only
  `mint-audience`. There is no renewal logic anywhere in capauth (grepped).

Refinements where the brief is incomplete or slightly off:

1. **The dead surface is wider than dispatch.** `_enforce_inject_floor`
   gates `inject`, `ratify`, AND `deny` on the verified-tier `skcode.inject`
   capability, which was never granted. So Approve and Deny on the
   needs_input banner are dead too, not just New session. The unblock grant
   must include both capabilities (Chef asked for exactly that).
2. **Short TTL is not the revocation lever. Revocation is.** The PDP checks
   `is_revoked` on every single `decide()`, and since CR-3.4 P2
   `verify_token` also checks revocation, so the hostd bearer path refuses a
   revoked token too. Revoking a grant takes effect on the next request, not
   at the next expiry. What short TTL actually buys is: bounding the life of
   a token whose bytes leaked somewhere the revocation file does not reach,
   and forcing the lineage through the renewer's refusal gate regularly.
   Those are real benefits, but they change the TTL calculus: daily TTL buys
   almost no extra revocation speed over weekly and costs real lockout risk.
   See section 6.
3. **"The PDP gate is the ENTIRE control surface" needs scoping.** It is the
   entire IDENTITY control surface. Downstream of an allow there are still
   real bounds: the emergency brake (pause flag, checked before auth), the
   server-side repo allowlist (`SKCODE_DISPATCH_REPOS`, with skos/skharness
   excluded as self-mod hazards per CR-6.2 C6), the `full`-profile subject
   allowlist (`SKCODE_FULL_PROFILE_SUBJECTS`), spawn input guards
   (`SpawnRejected`), and the cgroup+tailnet envelope. None of that helps
   attribute an action to a principal, which is why the subject model still
   matters, but "no second line of defence" overstates it for blast radius.
4. **The audit obligation drops context.** `_audit_obligation` copies only
   `trust_signal` out of `context`; everything else (e.g. the
   `permission_mode` the daemon passes) is discarded from the PDP audit
   record. The PEP's own audit line records request details, but the
   initiator channel is nowhere. This is the concrete hook for making the
   audit record honest (section 5).

---

## 3. The identity model: three principals, subject from credential

### 3.1 Principals

| Principal | Identity | Holds | Does not hold |
|---|---|---|---|
| Human operator | `chef@skworld.io` (enrolled, VERIFIED devices) | `skcode.dispatch`, `skcode.inject` grants | nothing else new |
| Agent (autonomous) | `lumina@chef.skworld.io` | `skcode.*` ONLY if a real headless consumer exists (today: none; autopilot live_execution is OFF) | UI-originated RCE authority |
| Daemon (infrastructure) | `lumina@chef.skworld.io` acting as the skchat service | `skchat.*` dataplane grants, exactly as today | any `skcode.*` grant |

Per Chef's instruction 4, after the migration Lumina holds no `skcode.*`
capability token at all until something she runs headlessly actually needs
one, at which point it is granted to her deliberately, as her, and the audit
trail shows her. The agent and the daemon share an identity string today;
that is tolerable because the daemon's grants (`skchat.*`) are not RCE.
Splitting daemon-as-itself into a third identity string is NOT proposed now;
it would churn the whole skchat dataplane for no attribution gain on the RCE
surface.

### 3.2 The mint change (the refactor's core)

`POST /api/v1/audience-token` currently does:

    subject := resolve_agent_identity()          # constant: the daemon

It becomes:

    subject := identity proven by the validated PRIMARY credential
      - operator-session JWT  -> the operator identity bound to that
                                 session (and its device_fp)
      - signed FQID assertion -> the asserted, signature-verified fqid

The subject remains 100% server-derived: it comes from a credential the
server validated, never from request JSON. The `Anti-forgery` property is
therefore not weakened, it is strengthened: today every primary-authenticated
caller is handed a token for an identity they did not prove (the daemon's).
After the change a caller can only ever receive a token for the identity
their credential proves. The existing rule "no subject or agent is ever read
from request input" stays literally true.

Rollout is shadowed exactly like CR-3.4 P5: the mint logs the
would-be-derived subject alongside the actual one for a soak window, then
flips behind a flag (`SKCHAT_AUDIENCE_SUBJECT_FROM_CREDENTIAL`, default OFF
until the flip). The issuer-shadow machinery in `dataplane_auth.py` already
resolves per-device subjects, so the derivation logic has prior art in-tree.

### 3.3 What enrolls the human (brief question 1)

`chef@skworld.io` is currently not an enrolled subject ("unknown subject: no
enrolled device"). Enrollment is capauth pairing: a `DeviceRecord` under the
subject at a mode. VERIFIED is defined as "capauth challenge-response /
self-signed FQID assertion" (`pairing/records.py`). The raw material exists:
Chef's Flutter devices already live in skchat's device registry with
device_fp, keys, and approval state (Linked Devices epic), and the linked
device already has capauth pairing records, just parked under the daemon
subject. The work is an enrollment ceremony that creates VERIFIED
`DeviceRecord`s under `chef@skworld.io` for Chef's real devices, driven from
the existing device registry rather than a parallel store. This is a
Chef-in-the-loop ceremony by design (it is the trust anchor for RCE), one
time per device, not per session.

### 3.4 If they stayed one subject (brief question 2, answered but not chosen)

The fallback would be: keep subject = daemon identity, stamp the initiator
(credential class, operator fqid, device_fp) into token metadata at mint, and
thread it through `decide(context=...)` onto the audit obligation. That makes
the audit record honest but leaves the grant conflated: revoking the UI still
revokes the agent. Since Chef explicitly wants Lumina de-granted unless she
needs it, the one-subject model cannot express his stated policy, so it is
rejected as the end state. Its audit half survives as Phase 1 hardening
(section 5) because it is cheap and useful during the migration window.

---

## 4. What can refuse: the renewal design (brief question 3)

### 4.1 Two layers, kept, with clarified roles (brief question 7)

The two-token split is coherent and stays:

- **Capability tokens** are durable authorization FACTS: "subject S is
  granted capability C until T." Days-scale TTL. Consulted by the PDP.
- **Audience tokens** are per-app SESSION credentials: containment of a
  pane/client, hour-scale, re-minted by the client on 401 via the
  `onAuthRejected` seam. Already working; unchanged.

What the capability layer imports from the audience layer is NOT
client-driven re-mint (a client that can re-mint its own RCE grant is
laundering, exactly what `request_is_primary_authenticated` exists to
prevent). It imports: renewal as an ONLINE, REFUSABLE act, plus expiry that
is visible before it bites.

### 4.2 The grant registry (new)

A capability token today is an orphan: nothing records the INTENT behind it,
so when it expires nothing knows whether that was a lapse or a decision.
Introduce a grant registry at `~/.skcapstone/security/grants.d/`, one signed
JSON declaration per grant:

```json
{
  "grant_id": "...",
  "subject": "chef@skworld.io",
  "capabilities": ["skcode.dispatch", "skcode.inject"],
  "token_ttl_hours": 168,
  "renew": true,
  "renew_until": null,
  "class": "rce",
  "approved_by": "chef",
  "reason": "Code section operator surface",
  "created_at": "..."
}
```

The capability token becomes the short-lived MATERIALIZATION of a grant; the
declaration is the standing intent. Deleting or disabling the declaration is
the roll-fast lever Chef asked for: renewal stops at the next cycle, and
`capauth token revoke --grant <id>` kills the live token immediately (PDP
effect on the next request, per section 2 note 2).

### 4.3 The renewer

`capauth-renewd`: a systemd user timer (daily) on the box that holds the
capauth signing key (.158). Not a client-side seam, not a network service.
Each run, for each declaration with `renew: true`:

1. Load the current live token for (subject, capabilities lineage).
2. Evaluate refusal conditions (below). Any hit: refuse, log, emit a
   watchdog event, do NOT mint.
3. If the live token has less than half its TTL remaining, mint the
   successor via `capauth.tokens.issue_token` with the declaration's TTL and
   `metadata.grant_id` linking it to the declaration. The predecessor is left
   to expire naturally (no revoke-on-renew, so an in-flight request never
   races a renewal).
4. Journal the outcome (renewed / not-due / refused+why) to
   `~/.skcapstone/security/renewals.jsonl` for the watchdog to read.

### 4.4 What can say no

An auto-renewer with no refusal condition is a permanent grant. These can
refuse, in evaluation order:

1. **The declaration itself.** Missing, `renew: false`, or past
   `renew_until`: refused. This is the operator's kill switch and the
   default answer to "what stops it": delete one file.
2. **Revocation of the lineage.** If ANY token carrying this grant_id has
   been revoked, renewal is refused permanently until a human re-grants.
   Revoking a token is a statement about the grant, not just the bytes; the
   renewer must never quietly resurrect a revoked authority.
3. **Enrollment facts.** The subject must still hold at least one
   non-revoked device at the capability's floor (VERIFIED for `skcode.*`).
   Unenroll or revoke the devices and renewal stops. This reuses
   `decide()`'s own fact base, so the renewer cannot drift from the PDP.
4. **The emergency brake.** For `class: rce` grants, if dispatch is paused
   (`operator_cli pause-dispatch` flag) the renewer refuses. A frozen fleet
   should not be quietly re-arming RCE grants in the background.
5. **Operator liveness (RCE class, Phase 3).** If no PRIMARY operator
   authentication has been observed within N days (default 14), refuse and
   alert. An operator who has vanished should not have a perpetually fresh
   RCE grant. Evidence source: the operator-session issuance log skchat
   already keeps. This one ships last because it needs a reliable liveness
   feed and a loud alert path first; a silent refusal here would recreate
   today's silent-expiry failure with extra steps.
6. **The signing key.** Unavailable key fails the mint closed. Not a policy,
   but it is the reason the renewer lives on the keyholder box and nowhere
   else.

Honest framing, because the brief demanded it: an auto-renewed RCE grant IS
a standing grant. This design does not pretend otherwise. What it changes is
that the standing grant has (a) a one-file kill switch, (b) instant
revocation independent of TTL, (c) refusal conditions evaluated by something
other than the beneficiary, and (d) daily narration of every renewal and
refusal where the operator actually looks. That is the difference between a
standing grant and a forgotten one, and a forgotten grant is what rotted on
2026-08-04.

---

## 5. What the audit record must be able to say

For every `skcode.dispatch` / `skcode.inject` / `ratify` / `deny` decision:

1. **Which principal.** After Phase 2 this falls out of the subject:
   `chef@skworld.io` is a human, `lumina@chef.skworld.io` is the agent.
2. **Through which channel.** `context.initiator`: credential class
   (operator-session / fqid-assertion), device_fp when known, client surface
   (flutter-shell / cli / headless). Stamped into token metadata at mint,
   passed by the PEP into `decide(context=...)`.
3. **Copied onto the audit obligation.** `_audit_obligation` grows an
   `initiator` advisory block copied from context, exactly like
   `trust_signal`: ADVISORY ONLY, read by no allow/deny branch. The spec 4.2
   hard rule (nothing but cryptographic facts gates allow) is untouched.
4. **Renewal lineage.** Token metadata carries `grant_id`, so an audit line
   is traceable to the declaration and its `approved_by` human.

Items 2 and 3 land in Phase 1 (cheap, useful immediately even with the wrong
subject); item 1 lands with Phase 2.

---

## 6. TTL (brief question 4)

**Capability tokens for `skcode.*`: 7 days, renewed at half-life (daily
renewer, renews when 3.5 days or less remain).** Reasoning:

- Revocation speed does not depend on TTL (section 2 note 2), so daily TTL
  does not make grants meaningfully more rollable than weekly. Chef's
  roll-fast goal is served by the revocation list and the grant registry.
- Renewal-outage runway: with 7d/renew-at-3.5d, a dead renewer leaves at
  least 3.5 days of working tokens, with the watchdog narrating the failure
  daily from day one. With 24h/renew-at-12h, a Friday-night renewer failure
  locks Chef out of his own tooling by Saturday morning, which is this
  incident again with a shorter fuse.
- 7 days also bounds a leaked-bytes scenario acceptably for a token that is
  useless without passing the PEP on the tailnet.

**Audience tokens: 1 hour, unchanged.** Already right.

**In-flight sessions on lapse:** the PDP decides per REQUEST, not per
session. A running harness session keeps running when the grant lapses; what
stops is new dispatch/cancel and new inject/ratify/deny. The live WS stream
rides the hour-scale audience token and read scope, so observation degrades
last. This is the correct failure order for RCE: you lose the ability to
start and steer before you lose the ability to watch. No kill-on-lapse is
proposed; killing a mid-flight coding session because a weekly token ticked
over destroys work without reducing risk (the session was authorized when it
started, and the brake exists for real emergencies).

---

## 7. The grant CLI (brief question 5)

Yes, capauth needs one, and it does not undermine the ceremony, it makes the
ceremony legible. Today "issuing a capability token is a key ceremony with no
ergonomic path" produced the worst of both worlds: the friction did not
prevent standing grants (one existed, it just rotted), it only prevented
anyone from noticing, inspecting, or repeating the act correctly. Security
by awkwardness is not a control; the actual controls are the signing key
(must be on the box), the enrollment floor, and now the declaration's
`approved_by` + `reason` fields, all of which the CLI enforces rather than
bypasses.

```
capauth token grant  --subject S --cap C [--cap C2] --ttl-hours N
                     --reason "..." --approved-by chef [--renew/--no-renew]
capauth token status [--subject S]     # grants, tokens, expiries, lineage
capauth token renew  --grant ID        # one manual renewal cycle, same refusals
capauth token revoke --grant ID | --token ID
```

`grant` writes the declaration, mints the first token, then prints a live
`decide()` verification for each capability so the ceremony ends with proof
instead of hope. It requires the signing key locally and refuses to run
without `--reason` and `--approved-by`.

---

## 8. Expiry visibility (brief question 6)

Watchdog, yes; it is exactly the shaped hole. The watchdog's adapter port
(`skos/src/skos/watchdog/port.py`) is pull-only `collect(window)`, and the
fleet digest is the surface Chef already reads daily. A
`capauth_grants` adapter reports: grants expiring within 7 days, renewals
performed in-window, renewals REFUSED in-window (with the refusal reason),
and any subject holding an RCE-class capability (a standing one-line
inventory of who can execute code, which is worth narrating even when
nothing changes state). Atlas's operator facet can read the same
`renewals.jsonl` later; the watchdog is the floor, not the ceiling, and it
ships first because it is live today and needs one S-sized adapter.

---

## 9. Blast radius (brief question 8)

Keep the token coarse. The enforcement points for narrowing dispatch already
exist server-side and are the right place: the repo allowlist
(`SKCODE_DISPATCH_REPOS`, with the self-modification exclusions), the
full-profile subject allowlist, the spawn guards, the brake, the cgroup and
tailnet envelope. Encoding repos/hosts/hours into the token would create a
second policy store that drifts from the server truth and makes every
renewal a policy decision. Two bounded improvements instead:

1. **Move the full-profile allowlist from env var to grant metadata**
   (`"profiles": ["sandbox"]` vs `["sandbox", "full"]`), evaluated by the
   existing `full_profile_allowed` seam in serve.py. Same enforcement point,
   but the fact travels with the grant, survives unit-file drift, and shows
   up in `token status`. (Phase 3.)
2. **Per-request session-count and rate ceilings belong in the PEP**, not
   the token, if ever needed. Not proposed now; no incident motivates it.

---

## 10. Answers to the brief's eight questions, in one place

1. **Distinct operator subject?** Yes. `chef@skworld.io`, enrolled VERIFIED
   via a device ceremony seeded from the existing linked-devices records.
   Mint derives the subject from the validated primary credential;
   anti-forgery survives because the subject is still server-derived,
   now from a proven credential instead of a constant. (Sections 3.2, 3.3)
2. **If one subject, honest audit?** Possible via initiator metadata plus
   context passthrough, and that half ships in Phase 1, but one subject
   cannot express Chef's stated grant policy, so it is only the interim.
   It is a PEP-supplies / capauth-records concern: hostd passes context,
   `decide()`'s audit obligation copies it advisory-only. (Sections 3.4, 5)
3. **What renews, what refuses?** `capauth-renewd` on the keyholder box,
   daily, driven by signed grant declarations. Refusals: declaration
   removed/disabled/aged out, lineage revoked, enrollment floor lost,
   dispatch brake on, operator liveness stale (Phase 3), signing key
   absent. (Section 4)
4. **TTL?** 7 days, renew at half-life; audience 1h unchanged; lapse stops
   new actuation but never kills a running session; observation degrades
   last. (Section 6)
5. **Grant CLI?** Yes. The ceremony's real controls are key custody,
   enrollment floors, and recorded approval, which the CLI enforces.
   Awkwardness was never a control; it just made the rot invisible.
   (Section 7)
6. **Expiry visibility?** Watchdog `capauth_grants` adapter in the daily
   digest; Atlas reads the same journal later. (Section 8)
7. **Two-token split coherent?** Yes. Grants (facts, days) vs session
   credentials (containment, hours). The capability layer imports refusable
   automation and visibility, not client-side re-mint. (Section 4.1)
8. **Scope the token further?** No. Blast-radius bounds stay server-side
   where they already are; the one move is relocating the full-profile
   allowlist into grant metadata at the same enforcement point.
   (Section 9)

---

## 11. Invariants this design must not break

- The PDP stays deterministic from cryptographic facts; nothing advisory
  ever gates allow (spec 4.2 hard rule).
- The verified-tier floor on `skcode.dispatch` / `skcode.inject` stays.
- The token subject is never caller-supplied. The refactor changes WHICH
  server-held fact it derives from, not who supplies it.
- No client can mint or renew a capability token. Renewal happens only on
  the keyholder box, offline from any request path.
- `request_is_primary_authenticated` keeps refusing the audience-token
  class at every mint gate (no laundering).
- Fail closed everywhere: a broken renewer, a missing declaration, or an
  unreadable registry results in expiry and a loud digest line, never a
  silent extension.
