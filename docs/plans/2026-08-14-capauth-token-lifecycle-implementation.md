# CapAuth Token Lifecycle: Implementation Plan

**Date:** 2026-08-14
**Spec:** `~/clawd/skos/docs/specs/2026-08-14-capauth-operator-identity-and-token-lifecycle.md`
**Author:** Fable (claude-fable-5)
**Card sizing per Chef:** L and M cards for Opus, S cards for Sonnet.
Cards are PROPOSED here, not created on the board. Chef or Lumina promotes.

---

## REVISION 2026-08-14 (later the same day)

Facts verified against live state; see the spec's revision note and the
companion `2026-08-14-skworld-trust-model-coherence.md` (COH-1..8 cards,
same L/M=Opus, S=Sonnet sizing).

- **Phase 0 is DONE** (another session): interim grant `db78de08` to
  `lumina@chef.skworld.io`, expires **2026-08-21T22:38:12Z**. The
  "next hour" section below is historical; do not run it again. The
  GTD waiting-for on the 08-21 expiry still applies.
- **The root rotated** to `BD7EEECA...`; the store was pruned to 5
  new-root tokens. All grant/renewal work signs with the new root.
- **CAP-6 shrinks: (M, Opus) becomes (S, Sonnet).** `chef@skworld.io`
  is already enrolled (4 VERIFIED devices) and already holds a
  19-capability token including `skcode.dispatch`/`skcode.inject`.
  Remaining scope: verify the enrollment records' provenance against
  the linked-devices registry, document the ceremony so it is
  repeatable for the next device, and add the acceptance check
  (`decide("chef@skworld.io", "skcode.dispatch")` should now ALLOW,
  not "no token grants": stronger than originally specified).
- **CAP-8 shrinks** (stays M, Opus): the chef grant step is done.
  Remaining: shadow-soak review, flag flip, verify a real click audits
  as `chef@skworld.io`, revoke Lumina's `db78de08`, update
  `SKCODE_FULL_PROFILE_SUBJECTS`, rollback note. Timing rule: flip (or
  consciously re-grant) BEFORE 2026-08-20, since `db78de08` expires
  08-21 and until the flip the UI still rides the Lumina subject.
- **CAP-2 acceptance grows:** backfill registry declarations for all 5
  surviving tokens (they are orphans by CAP-2's own standard, and the
  chef and agent tokens have NO expiry, which the registry must record
  as an explicit decision or replace with TTL'd renewals). CAP-2,
  CAP-3, and CAP-5 must resolve the token store through the single
  root function from COH-1; do not hardcode either legacy path.
- **CAP-4 note:** the signature-verification fix in flight touches the
  same `authz.py`; coordinate merge order, land CAP-4 after it.

---

## What to do in the next hour (unblock Chef TODAY): DONE, historical

Chef's Code section write surface is dead: dispatch, inject, ratify, and
deny all fail the PDP. The minimum safe action is one manual grant ceremony,
run by Chef (or by Lumina with Chef watching), on .158, the box holding the
capauth signing key. This is deliberately the STATUS QUO subject model
(subject = `lumina@chef.skworld.io`): it restores yesterday's working state
plus the never-granted inject, nothing more. The architectural work below
supersedes it.

```bash
# 1. Issue a 7-day grant for BOTH capabilities (dispatch AND inject;
#    inject also gates ratify/deny, so Approve/Deny need it too).
~/.skenv/bin/python - <<'EOF'
from pathlib import Path
from capauth.tokens import issue_token

home = Path.home() / ".skcapstone"
tok = issue_token(
    home,
    "lumina@chef.skworld.io",
    ["skcode.dispatch", "skcode.inject"],
    ttl_hours=168,
    metadata={
        "granted_by": "chef",
        "reason": "unblock Code section 2026-08-14; interim manual grant "
                  "pending grant-registry work (see 2026-08-14 spec)",
    },
)
print("token:", tok.payload.token_id[:16], "expires:", tok.payload.expires_at)
print("signed:", bool(tok.signature))
EOF

# 2. Verify with the PDP itself, never assume:
~/.skenv/bin/python - <<'EOF'
from capauth.authz import decide
for cap in ("skcode.dispatch", "skcode.inject"):
    d = decide("lumina@chef.skworld.io", cap)
    print(cap, "allow =", d.allow, "|", d.reason)
EOF

# 3. Click New session in the Code section. The Flutter client re-mints its
#    audience token on 401 by itself (onAuthRejected seam); no restart needed.
```

Notes on this interim state, stated plainly:

- If step 1 prints `signed: False`, the GPG signing key was not reachable
  and the token is useless (the verifier requires a signature). Fix the key
  path first; do not proceed unsigned.
- **This token expires 2026-08-21.** Until CAP-2/CAP-3 land, that is a
  manual re-run. Put it in GTD as a waiting-for with a due date the moment
  the grant is made. CAP-1 (watchdog adapter) makes the expiry visible in
  the daily digest and is deliberately first in Phase 1 so the manual
  window is never silent.
- Human-initiated dispatch is still attributed to Lumina until Phase 2.
  Known, accepted, temporary.

---

## Phases

- **Phase 0 (today, Chef's hands):** the manual grant above. No code.
- **Phase 1 (this week): lifecycle + visibility.** Grant registry, renewer,
  CLI, watchdog adapter, initiator audit passthrough. Ends the
  silent-expiry failure class for good. No identity change, no client
  change, nothing user-visible except the digest.
- **Phase 2 (next): operator identity.** Enroll `chef@skworld.io`, derive
  the mint subject from the primary credential (shadow first, then flip),
  move the `skcode.*` grants to Chef, de-grant Lumina. Ends the
  attribution incoherence. Chef-gated flip.
- **Phase 3 (later): hardening.** Operator-liveness refusal, full-profile
  allowlist into grant metadata, client-side expiry surfacing polish.

Phase 1 and Phase 2 are independent enough to parallelize across sessions,
but Phase 2's flip must not land before CAP-1 is narrating renewals (a
subject migration with invisible token state is how this incident happened).

---

## Proposed coord epic

**Epic: `capauth-token-lifecycle` : operator identity + auto-renewing
capability grants for the Code section**

Repos touched: `capauth`, `skchat`, `skharness`, `skos`, `skworld-app`.
All work in worktrees under `~/skworld-worktrees/`; the shared checkouts
under `~/clawd/skcapstone-repos/` are production and stay on main.

### Phase 1 cards

**CAP-1 (S, Sonnet) [skos]: watchdog `capauth_grants` adapter.**
New `skos/src/skos/watchdog/adapters/capauth_grants.py` on the existing
`WatchdogSourceAdapter` port. Collect within window: capability tokens
expiring within 7 days (read via `capauth.tokens.list_tokens`, filter
RCE-class caps first), renewals and refusals from
`~/.skcapstone/security/renewals.jsonl` (tolerate the file not existing
until CAP-2 ships), and a standing inventory line: subjects currently
holding `skcode.*` grants. Fail-safe per port contract (degrade to
SourceUnavailable, never raise). Tests mirror the existing adapter tests.
Ship FIRST: it protects the Phase 0 manual window.

**CAP-2 (L, Opus) [capauth]: grant registry + `capauth-renewd`.**
The core of Phase 1. New `capauth/src/capauth/grants.py`: signed grant
declarations in `~/.skcapstone/security/grants.d/` (schema in spec section
4.2), lineage linkage via `metadata.grant_id` on issued tokens, and the
renewer entry point (`capauth-renewd`, console script + systemd user
timer unit shipped in the repo). Refusal conditions 1-4 and 6 from spec
section 4.4 (declaration state, lineage revocation, enrollment floor via
the same pairing facts `decide()` uses, dispatch brake for `class: rce`,
signing-key fail-closed). Renew at half-life, never revoke-on-renew,
journal every outcome to `renewals.jsonl`. Hermetic tests against
`tmp_path` exactly like the authz tests (injectable base_dir). Must NOT
touch `decide()` semantics.

**CAP-3 (M, Opus) [capauth]: grant CLI.**
`capauth token grant | status | renew | revoke` per spec section 7, over
CAP-2's registry. `grant` refuses without `--reason`/`--approved-by`,
requires the local signing key, mints the first token, and ends by
printing live `decide()` results for each granted capability. `status`
shows grants, live tokens, expiries, lineage, and refusal history.
Depends on CAP-2.

**CAP-4 (S, Sonnet) [capauth + skharness]: initiator advisory audit
passthrough.** In `capauth.authz._audit_obligation`, copy an
`initiator` block from `context` onto the audit record, advisory-only,
exactly parallel to `trust_signal` (no allow/deny branch may read it;
add the test asserting that). In `skharness/daemon.py`, pass
`{"initiator": {...}}` (credential class and device_fp when the
AuthContext carries them, client surface) into the dispatch and
inject-floor `decide()` calls. Small, two repos, no behavior change.

**CAP-5 (S, Sonnet) [skcapstone]: doctor check for RCE grant health.**
`skcapstone doctor` gains `authz:skcode-grants`: for each expected
subject (config-driven), run `decide()` for `skcode.dispatch` and
`skcode.inject` and report allow/deny + days-to-expiry + whether a grant
declaration backs the token. Red when a live token has no declaration
(orphan, the 08-04 failure mode) or expires within 48h with `renew:
false`.

### Phase 2 cards

**CAP-6 (M, Opus) [capauth + skchat]: enroll the operator subject.**
An enrollment ceremony CLI (`capauth pair enroll-operator` or
equivalent): create VERIFIED `DeviceRecord`s for `chef@skworld.io`,
seeded from skchat's device registry rows for Chef's approved devices
(challenge-response or operator-signed FQID assertion per
`pairing/records.py` VERIFIED semantics). Chef-in-the-loop, one time per
device. Acceptance: `decide("chef@skworld.io", "skcode.dispatch")` moves
from "unknown subject" to "no token grants capability" (enrollment done,
grant pending).

**CAP-7 (L, Opus) [skchat]: mint subject derived from the primary
credential.** The refactor's core seam, spec section 3.2. In
`webui.py::audience_token_mint` + `dataplane_auth.py`: resolve the
subject from the validated operator-session JWT / FQID assertion instead
of `resolve_agent_identity()`. Shadow mode first
(`SKCHAT_AUDIENCE_SUBJECT_SHADOW`: log derived vs actual on every mint,
zero behavior change), then the flip flag
(`SKCHAT_AUDIENCE_SUBJECT_FROM_CREDENTIAL`, default OFF). Reuse the
issuer-shadow derivation prior art in `dataplane_auth.py`. Hard
invariants in tests: subject never read from request JSON; audience
tokens still refused as mint credentials; headless agent callers (FQID
assertion as Lumina) still yield the agent subject unchanged.

**CAP-8 (M, Opus) [capauth + fleet]: the grant migration flip.**
Chef-gated, after CAP-6+CAP-7 shadow soak: `capauth token grant` for
`chef@skworld.io` (`skcode.dispatch`, `skcode.inject`, `class: rce`,
renew on), flip `SKCHAT_AUDIENCE_SUBJECT_FROM_CREDENTIAL` on, verify a
real button click dispatches and audits as Chef, then revoke Lumina's
interim Phase 0 token and do NOT re-grant her (per Chef's instruction 4:
she holds `skcode.*` again only when a headless consumer exists).
Includes updating `SKCODE_FULL_PROFILE_SUBJECTS` to Chef's subject and a
rollback note (flip the flag off; Lumina re-grant path documented).

**CAP-9 (S, Sonnet) [skworld-app]: honest auth-failure surfacing.**
In the Code section client, distinguish and display the three failure
states the server already reports distinctly: 401 (stale audience token,
auto-remedied via `onAuthRejected` re-mint), 403 "dispatch not
authorized" (grant missing/expired: show WHICH capability and say "grant
expired or not issued, see capauth token status", never a generic
toast), 503 (dispatch paused). Chef found this incident by clicking a
button into a mute error; the client should have named the problem.

### Phase 3 cards

**CAP-10 (M, Opus) [capauth + skchat]: operator-liveness refusal.**
Refusal condition 5: `capauth-renewd` refuses RCE-class renewal when no
primary operator authentication is observed within N days (default 14).
Needs the liveness feed (operator-session issuance journal from skchat)
and a LOUD refusal path (watchdog event + sk-alert), because a quiet
refusal here recreates the silent-expiry failure.

**CAP-11 (S, Sonnet) [skharness]: full-profile allowlist from grant
metadata.** `full_profile_allowed()` reads the subject's grant
declaration `profiles` field (env var stays as fallback during
transition). Same enforcement point, fact travels with the grant.

---

## Dependencies and ordering

```
Phase 0 (manual grant)          : today, no deps
CAP-1 watchdog adapter          : no deps, ship first
CAP-2 registry + renewd (L)     : no deps
CAP-3 grant CLI (M)             : after CAP-2
CAP-4 initiator audit (S)       : no deps
CAP-5 doctor check (S)          : after CAP-2 (declaration awareness)
CAP-6 enroll operator (M)       : no deps, parallel with Phase 1
CAP-7 mint derivation (L)       : shadow anytime; flip needs CAP-6
CAP-8 migration flip (M)        : after CAP-2, CAP-3, CAP-6, CAP-7 soak; Chef-gated
CAP-9 client surfacing (S)      : no deps
CAP-10 liveness refusal (M)     : after CAP-2, Phase 3
CAP-11 profile metadata (S)     : after CAP-2, Phase 3
```

## What is deliberately NOT in this plan

- No change to `decide()`'s allow/deny logic, the verified floor, or the
  fail-closed posture, in any card.
- No client-side capability-token minting or renewal, ever.
- No third identity string for daemon-as-itself (spec section 3.1: no
  attribution gain on the RCE surface for whole-dataplane churn).
- No repo/host/time-of-day caveats inside tokens (spec section 9: the
  server-side allowlists are the enforcement point and stay so).
- No kill-on-lapse for running sessions (spec section 6).
