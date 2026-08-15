# SKWorld Trust Model Coherence

**Date:** 2026-08-14
**Author:** Fable (claude-fable-5)
**Status:** Proposed
**Companion:** `2026-08-14-capauth-operator-identity-and-token-lifecycle.md`
(the token lifecycle spec; it stands, this document sits above it)
**Scope:** capauth (PDP, tokens, custody), skharness, skgateway, skchat,
sk-access (the four PEPs), Syncthing as the fact-distribution layer, skos
watchdog.

Everything below was verified against live state on noroc2027 and against
capauth main at `7b5f0a6` on 2026-08-14. No token was minted, granted,
revoked, or modified in producing this assessment.

---

## 1. Verdict

The trust model is sound in shape and incoherent in three specific places.

Sound: one PDP (`capauth.authz.decide`), consulted by every PEP, deciding
from cryptographic facts only, failing closed on every uncertainty, with
post-allow blast radius bounded by independent server-side controls. That
concentration is correct and should stay (section 4).

Incoherent:

1. **Two physical storage roots for one logical trust store**, with home
   resolution that differs by call site. The PDP reads one root; the
   revocation fallback in `verify_token` can read the other. (Section 5)
2. **"Can write a file under the synced tree" is a grant-issuing power.**
   The store is Syncthing-replicated, so that principal set is fleet-wide,
   and until the in-flight signature fix lands, `decide()` never checks
   signatures at all. Even after that fix, no issuer policy exists: a
   signature by ANY key is not the same as a signature by the RIGHT key.
   (Section 6)
3. **The audience-token store has no lifecycle and is flooding right now**:
   5,215 token files at time of writing, growing at over 2,000 per hour
   during the 21:00 and 22:00 UTC hours today, never garbage-collected,
   replicated to every node. (Section 7)

Plus one custody defect that is not architectural but must be fixed before
the next rotation: the custody declaration says the root private key is
offline, and the keyring on this host holds it. (Section 8)

---

## 2. Verified ground truth (2026-08-14)

- **Root identity:** `BD7EEECA23D90A594400751CFDB582D9CB7272A6`, uid
  `Chef (SK Sovereign Root) <chef@skworld.io>`, ultimate trust, secret key
  material PRESENT in the noroc2027 keyring (`sec`, not `sec#`).
  `~/.skcapstone/identity/identity.json` and `~/.skcapstone/cluster.json`
  both point at it; `cluster.json` records the retired
  `D8920EA86742260161A220C30355DE4AA63CCD69`.
- **Durable store** (`~/.skcapstone/security/tokens/`): exactly 5 tokens,
  all signed, all issuer `BD7EEECA`, none revoked:
  - `fc9961c9` `chef@skworld.io`, 19 caps including `skcode.dispatch`,
    `skcode.inject`, no expiry.
  - `db78de08` `lumina@chef.skworld.io`, `skcode.dispatch` +
    `skcode.inject`, expires **2026-08-21T22:38:12Z** (the interim grant).
  - `0e43a258` / `64c5839a` / `00e35c04`: lumina / jarvis / opus
    `@chef.skworld.io`, 6 ops caps each, no expiry.
  The prune archived 213 files to
  `capauth/retired-keys/stale-tokens-20260814/`. The revoked unsigned
  interim attempt (`5527082c...`) is in
  `~/.skcapstone/security/revoked-tokens.json`.
- **Audience store** (`~/.skcapstone/capauth/security/tokens/`): 5,215
  files, all `token_type: capability`, 5,212 signed by
  `02BC0EB3...` (Lumina agent key, `lumina@skworld.io`), 3 unsigned.
  Audience split: 5,006 `skchat`, 229 `skcode`. About 3,758 unexpired at
  scan time. Mint rate: ~10/hour baseline, then 1,423 in the 21:00 hour
  and 2,325 in the 22:00 hour today.
- **Enrollment:** `chef@skworld.io` peer record exists with 4 VERIFIED,
  non-revoked devices. The PDP's earlier "unknown subject" answer is gone.
- **PDP code at `7b5f0a6`:** `decide()` checks enrollment mode, capability
  match, activity window, and revocation. It does NOT verify the token's
  PGP signature (a separate concurrent fix addresses this; this document
  assumes it lands and does not design it). `issue_token` stores the token
  even when signing fails (same concurrent fix).
- **PEPs:** skharness `daemon.py` (`_enforce_inject_floor` on inject,
  ratify, deny; dispatch floor via serve.py wiring), skchat
  `dataplane_auth.py` and `operator_grants.py` (in-process), skgateway
  `policy/authz_gate.mjs` (HTTP, via `capauth-service`
  `POST /v1/authz/decide` on loopback :8420, gated by
  `CAPAUTH_AUTHZ_TOKEN`), sk-access (same decide seam per fleet policy).

---

## 3. Who owns which guarantee

| Guarantee | Owner | Status |
|---|---|---|
| The caller is who they claim (subject authenticity) | Each PEP, before calling `decide()` | Sound in skharness (subject read off the verified bearer token) and skchat; contract implicit elsewhere (section 4) |
| Subject string cannot be forged at mint | skchat mint seam (server-derived subject) | Sound; being improved by CAP-7 (credential-derived) |
| Allow/deny policy | `capauth.authz.decide`, the ONE PDP | Sound logic; signature gap in flight; issuer policy missing (section 6) |
| Grant facts are authentic | Token PGP signatures + issuer policy | Broken until sigfix lands; issuer pinning does not exist (COH-2) |
| Grant facts are the same everywhere | Syncthing replication | Eventual only; revocation merge is lossy under conflict (COH-3) |
| Revocation takes effect | `is_revoked` per decision + replication | Sound locally; fleet-effective only at sync pace; dual-root hazard (section 5) |
| Post-allow blast radius (RCE) | skharness: brake, repo allowlist, full-profile subject allowlist, spawn guards, cgroup + tailnet | Sound, verified in the lifecycle spec |
| Root key custody | Operator ceremony + `custody.json` + doctor | Declaration contradicts reality today (section 8) |
| Expiry and renewal visibility | skos watchdog (CAP-1) + renewd journal (CAP-2) | Not shipped yet; interim token expires 08-21 |

The concentration question answered: one PDP defect hardening or breaking
all four PEPs simultaneously is the DESIGNED behavior, and it is right.
The alternative is four independently drifting policy engines, which is
how the skchat dataplane shadow-mode era found real divergence. A single
PDP turns "did we fix authz" into one question. What was missing is not
distribution, it is a written contract (section 4) and a conformance
suite PEP authors can run (COH-5).

---

## 4. What the PDP guarantees, and what it does NOT

This is the contract four PEP authors may rely on. It should be committed
to capauth as `docs/PDP-CONTRACT.md` and versioned (COH-5).

### decide() GUARANTEES

1. **Deterministic from stored facts.** Same subject, capability, and
   fact-store state gives the same answer. No network, no clock other
   than expiry windows, no advisory input ever gates allow (`trust_signal`
   and `context` are audit-only).
2. **Fail closed on every uncertainty:** unknown capability, unknown
   subject (no enrolled non-revoked device), insufficient enrollment mode
   for the capability's floor, no granting token, and granting tokens all
   expired / not-yet-valid / revoked each produce a deny with a distinct
   reason string. Reason strings are part of the contract surface; PEPs
   may log them but MUST NOT branch on their text.
3. **Enrollment floor:** `skcode.dispatch` and `skcode.inject` require a
   VERIFIED device. No token can substitute for enrollment, and no
   enrollment can substitute for a token. Both are required.
4. **Revocation is consulted on every call.** Revoking a token takes
   effect on the subject's next request through any PEP on a node where
   the revocation file has arrived.
5. **(Once the in-flight sigfix lands) a granting token must carry a
   valid signature.** Issuer policy on top of that is COH-2 and becomes
   part of this contract when it ships.
6. **An audit obligation is returned on every decision**, allow or deny.

### decide() does NOT

1. **Authenticate the caller.** The subject parameter is trusted input.
   A PEP that passes an unverified string has bypassed authorization by
   construction. Every PEP MUST derive the subject from a credential it
   cryptographically verified (skharness: the verified bearer token
   payload; skchat: the validated session; skgateway: the verified
   caller identity it forwards over the authenticated :8420 channel).
2. **Persist the audit record.** The obligation is returned, not written.
   The PEP owns getting it to durable audit storage. A PEP that drops it
   has silent authz with no trail.
3. **Bind the decision to the resource.** `resource` is recorded on the
   audit obligation only; the seeded rules do not evaluate it. Anything
   resource-shaped (which repo, which host, which session) is the PEP's
   own enforcement (e.g. `SKCODE_DISPATCH_REPOS`). Do not assume the PDP
   checked it.
4. **Rate-limit, throttle, or cache.** Each call scans the token
   directory (O(n) files). PEPs on hot paths own their own caching
   posture and must consider staleness against guarantee 4.
5. **Protect the fact store.** `decide()` believes what it reads under
   its storage root. File permissions are NOT a defense on a
   Syncthing-replicated tree (section 6). Fact authenticity comes from
   signatures + issuer policy, nothing else.
6. **Guarantee fleet-wide simultaneity.** Facts converge at Syncthing
   pace. A grant or revocation is effective on a node only once
   replicated there. Design flows accordingly (a revocation chased by an
   attacker's request to a lagging node can still be allowed there).
7. **Notify anyone of anything.** Expiring grants, refused renewals, and
   store anomalies are the watchdog's job (CAP-1, COH-8), not the PDP's.
8. **Resolve one storage root today.** Until COH-1 lands, which physical
   store a given call reads depends on the call site (section 5). PEPs
   MUST pass no `base_dir` and rely on the library default, and MUST NOT
   set `CAPAUTH_HOME` divergently per process.

---

## 5. Incoherence 1: two storage roots, split revocation

There is one logical trust store and two physical roots:

- `~/.skcapstone/security/` : what `decide()` reads, because its default
  home is `pairing.store.default_base_dir()` = `~/.skcapstone`. The
  pruned, root-signed, 5-token durable store. Its revocation file holds
  the revoked unsigned interim token.
- `~/.skcapstone/capauth/security/` : what `resolve_capauth_home()`
  returns (the `~/.skcapstone/capauth` dir exists, so branch 3 wins).
  `verify_token(home=None)`'s revocation fallback resolves THIS root, as
  does anything else that resolves "the capauth home". The audience mints
  land here because the minting services pass this home.

Consequences, concretely:

- A token revoked via the PDP root is still unrevoked as far as a bare
  `verify_token(home=None)` bearer check is concerned, and vice versa.
  The CR-3.4 P2 fix ("a revoked token is rejected everywhere") is
  silently split-brained by pathing.
- The 194-to-5 prune "cleaned the token store" only in the PDP's root.
  5,215 tokens sat untouched next door, invisible to the person doing the
  cleanup. Nobody can currently answer "what tokens exist" without
  knowing this footnote.
- The retirement note in `tokens.py` says the portable envelope key stays
  `skcapstone_token` for compatibility; the same discipline was never
  applied to the storage root.

**Fix (COH-1):** one resolution function, used by `decide()`,
`issue_token`, `mint_audience_token`, `list_tokens`, `is_revoked`,
`revoke_token`, and `verify_token`, with an explicit migration: pick
`~/.skcapstone/capauth/security/` as canonical (it is the capauth-owned
subtree), move the 5 durable tokens and the revocation entries there
under a mint freeze, leave a doctor check that goes red if the legacy
root ever contains a token or revocation file again. Until then the
operational rule is: durable grants and their revocations go through the
PDP root only.

This does NOT weaken any anti-forgery property; it removes an ambiguity
about where the facts live.

---

## 6. Incoherence 2: file write = grant issuance, fleet-wide

The trust model behaves as if the token directory were integrity-
protected local state. It is neither:

- **Not integrity-protected:** at `7b5f0a6`, `decide()` never verifies
  signatures. A hand-written JSON file with `subject`, `capabilities`,
  and a plausible window IS a grant. Three unsigned tokens exist in the
  audience store today and would pass the PDP's token gate if their
  subject and caps matched a request. The concurrent sigfix closes this;
  assumed, not re-designed here.
- **Not local:** `~/.skcapstone` is Syncthing-replicated. The principal
  set "can write a file into the token dir" is every process on every
  node that can write to the synced folder, plus anything that can
  inject into Syncthing itself. The July 23 outbox flood already proved
  this tree is a fleet-wide write surface in practice.

Signature verification alone is necessary but not sufficient, because
the keyring contains many keys and a valid signature by SOME key must
not equal authorization. Today's stores demonstrate the live question:
durable RCE grants are signed by the root (`BD7EEECA`); audience session
tokens are signed by the Lumina agent key (`02BC0EB3`), and agent keys
necessarily live on the nodes that mint. If any-signer-verifies, then
any agent key on any node can sign itself a `skcode.dispatch` capability
token, which collapses the root ceremony to the weakest agent keyring.

**Fix (COH-2), the issuer policy:**

- Each `CapabilityRule` gains an issuer class: `root` or `service`.
- RCE and admin class capabilities (`skcode.*` write surface,
  `agentrun.execute`, `change.deploy`, `skgateway.admin`) accept ONLY
  tokens whose verified signer fingerprint equals the operator root
  recorded in `cluster.json` (`operator_pubkey_fingerprint`), and the
  retired fingerprint list is explicitly excluded.
- Audience/session tokens may be signed by the minting service's
  enrolled agent key; the acceptable set is the enrolled, non-revoked
  agent identities, not "anything in the keyring".
- Unknown or retired issuer: deny, with its own reason string.

This strengthens the anti-forgery property. It also makes the rotation
story coherent: retiring a root fingerprint in `cluster.json` instantly
invalidates every token it signed, fleet-wide, at sync pace, with no
per-token revocation needed. That is what a rotation SHOULD mean, and it
is why the 194-token prune must never need to happen by hand again.

**Fix (COH-3), revocation that survives replication:** the single
`revoked-tokens.json` map is a lossy merge under Syncthing conflict
(last-writer-wins can resurrect a revoked token). Move to additive
tombstones: `security/revoked.d/<token_id>.json`, one file per
revocation, exactly like token files; readers union the legacy map and
the tombstone dir. Deletion-resurrection remains possible for an
attacker who can delete synced files, which is why COH-2 (issuer
pinning) and short TTLs stay the primary controls and tombstones are
the consistency fix, not the security fix.

---

## 7. Incoherence 3: the audience store has no lifecycle

5,006 skchat audience tokens exist for a surface whose sessions last an
hour. Baseline mint rate is ~10/hour; today it spiked to over 2,000/hour
for at least two consecutive hours and was still climbing at scan time.
Nothing garbage-collects expired tokens; nothing rate-limits mints per
subject+audience; every file replicates to every node; and every
`decide()` call scans the directory linearly, so PDP latency degrades
with the flood. The three unsigned files in this store are pre-#38
signing failures that were stored anyway.

This is not a security hole by itself (audience tokens are hour-scale
and, post-sigfix, must verify), but it is the same failure class as the
July 23 outbox flood, and it invalidates the mental model "the token
store is small and inspectable" that the prune tried to restore.

**Fix (COH-4):** find the re-mint loop (the hourly cadence says a client
is minting on a timer or on every 401 without backoff; the 21:00 spike
says something regressed today), add per-subject+audience mint
coalescing (return the existing live token when one has more than half
its TTL left), GC expired audience tokens on a timer, and separate
audience tokens from the durable store by construction
(`security/audience-tokens/`, never scanned by `decide()`, since the
PDP never needs them: they are presented as bearers, not looked up).
Watchdog line for store growth rate (COH-8) so the next flood is a
digest line, not an archaeology find.

---

## 8. Key custody and the rotation runbook

What the live state says: `capauth/identity/custody.json` declares
`"private_key": "offline"` ("deliberately NOT on this host"), dated
today. The gpg keyring on this host holds the full secret key
(`sec`, not `sec#`). Both cannot be true. Either the key goes to
offline custody with a shipped-in signing procedure, or the declaration
must say the truth so `doctor` and the next operator reason from
reality. A custody declaration that flatters is worse than none
(COH-7 makes doctor compare declaration to keyring and go red on
mismatch).

Today's rotation also broke a mint mid-flight: a token was issued in the
window where the old root was retired and signing was not yet proven
(compounded by the #38 empty-passphrase bug), and `issue_token` stored
the unsigned result, which then had to be found and revoked by hand.

**The rotation runbook (amend `ROOT_ROTATION_CEREMONY.md`, COH-6) must
say, in order:**

1. **Exclusive mint window.** Before touching keys: pause the renewer,
   engage the dispatch brake for RCE class, and set an explicit mint
   freeze flag that `issue_token` and `mint_audience_token` check. A
   rotation owns the store; concurrent sessions do not mint during it.
2. **Decide custody BEFORE import.** Record in `custody.json` where the
   secret will live, then make reality match, then verify with doctor.
3. **Canary before commit.** In a throwaway home: issue, sign, and
   verify a canary token with the new key. `signed: False` aborts the
   ceremony. Only then update `identity.json` and `cluster.json`
   (atomically, with the retired fingerprint recorded).
4. **Reissue, then invalidate.** Reissue each surviving durable grant
   under the new root (from the grant registry once CAP-2 exists; by
   inventory until then), archive old-root tokens, and rely on issuer
   pinning (COH-2) to make retired-root tokens dead everywhere without
   per-token revocation.
5. **Propagate before unfreeze.** Verify each fleet node can verify a
   new-root signature (public key arrived via sync) BEFORE lifting the
   mint freeze. A node that cannot verify the new root yet will
   fail closed post-COH-2; that is correct but should be brief.
6. **Postcheck.** No token in the canonical store carries a retired
   issuer; revocation entries survived; `decide()` canaries pass for
   each critical (subject, capability) pair; watchdog digest line
   records the ceremony.

---

## 9. The lumina@ skcode grant

Recommendation: **keep `db78de08` until the mint-seam flip, revoke it at
the flip, and do not renew it.**

Reasoning: today the audience mint still stamps the daemon identity
(`lumina@chef.skworld.io`) as subject on every Code-section token, so
revoking Lumina's capability grant now would re-deadlock Chef's UI even
though `chef@skworld.io` is enrolled and granted; chef's grant is
unreachable until the seam derives the subject from his credential
(CAP-7). Once the flip is verified with a real button click auditing as
`chef@skworld.io`, Lumina's grant has no consumer: Chef's policy is that
she holds `skcode.*` only as an actual headless user of the interface,
autopilot `live_execution` is OFF, and no headless consumer exists.
Revoke rather than let it lapse, so the audit trail records a decision
instead of an accident.

Deadline coupling: the interim token expires **2026-08-21T22:38Z**. If
the flip will not be verified by 2026-08-20, either consciously re-grant
short-TTL with a registry declaration (once CAP-2/CAP-3 exist) or accept
the lapse with the client now naming the failure (CAP-9). Do not let it
expire silently; that is the 08-04 incident again.

---

## 10. Proposed cards (not created on the board)

Sizing per Chef: L and M for Opus, S for Sonnet.

- **COH-1 (M, Opus) [capauth]: one storage root.** Single resolution
  function for every token/revocation read and write; migrate the 5
  durable tokens and revocation entries to the canonical root under a
  mint freeze; doctor check red on any token/revocation appearing in
  the legacy root. Hermetic tests with injected base_dir.
- **COH-2 (L, Opus) [capauth]: issuer policy in the PDP.** Issuer class
  per CapabilityRule; root-pinned verification for RCE/admin class
  against `cluster.json`'s operator fingerprint with retired
  fingerprints excluded; enrolled-service signers for audience class;
  unknown issuer denies with its own reason. Builds ON the concurrent
  signature-verification fix, does not reimplement it. Contract doc
  updated in the same PR.
- **COH-3 (S, Sonnet) [capauth]: tombstone revocations.**
  `security/revoked.d/<token_id>.json` additive tombstones; readers
  union legacy map + tombstones; revoke writes both during transition.
- **COH-4 (M, Opus) [skchat + capauth]: audience-store lifecycle.**
  Root-cause today's 2,000+/hour mint flood; mint coalescing per
  subject+audience; GC of expired audience tokens; audience tokens
  stored apart from the durable store and excluded from `decide()`'s
  scan. Includes cleaning the 3 stored unsigned tokens.
- **COH-5 (S, Sonnet) [capauth]: PDP contract + conformance suite.**
  Commit section 4 as `docs/PDP-CONTRACT.md` (versioned); pytest
  conformance suite exercising every guarantee and every documented
  non-guarantee, importable by PEP repos' CI.
- **COH-6 (S, Sonnet) [capauth]: rotation runbook amendment.** Section 8
  steps 1 to 6 into `ROOT_ROTATION_CEREMONY.md`, plus the mint-freeze
  flag honored by `issue_token` / `mint_audience_token` (flag check is
  S-sized; it is a guard clause plus tests).
- **COH-7 (S, Sonnet) [capauth]: custody truth check.** `doctor`
  compares `custody.json`'s declaration against the keyring
  (`sec` vs `sec#` vs absent) and goes red on mismatch, both directions.
- **COH-8 (S, Sonnet) [skos]: watchdog store-health line.** Token-store
  file count and growth rate per store, revocation-file conflict
  artifacts (`.sync-conflict` files under `security/`), and unsigned
  tokens present. Digest line, SourceUnavailable degradation per port.

Ordering: COH-6's mint-freeze flag and COH-7 first (they protect the
next rotation, and one just happened); COH-1 before COH-2 (issuer
policy should land on one store, not two); COH-4 urgent independently
(the flood is live); COH-3, COH-5, COH-8 anytime.

---

## 11. Invariants (unchanged from the lifecycle spec, restated)

- The PDP stays deterministic from cryptographic facts; nothing
  advisory ever gates allow.
- The verified-tier floor on `skcode.dispatch` / `skcode.inject` stays.
- The token subject is never caller-supplied; the mint seam derives it
  from a server-validated credential.
- No client mints or renews its own capability token.
- Fail closed everywhere: unknown issuer, unreachable store, frozen
  mint, and unverifiable signature are all denies, never warnings.
- Nothing in this spec weakens the PDP; COH-2 strictly strengthens it.
