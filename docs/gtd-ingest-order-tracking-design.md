# skos gtd-ingest: Stateful Order/Shipment Tracking + the `upsert` primitive

**Status:** design (2026-07-03) · **Owner:** Opus (Claude Code) + Chef · **Sibling:** [`gtd-ingest-architecture.md`](./gtd-ingest-architecture.md), [`gtd-ingest-SOP.md`](./gtd-ingest-SOP.md)
**Scope:** the *one real gap* on top of the existing gtd-ingest framework: a
capture path that **updates an existing GTD item through a lifecycle** (not just
create-or-skip), a **stateful pull adapter** that drives deliveries/orders through
it, and the **Claude-Code operating convention** so this runtime defaults to
routing work through skos. Pilot: Chef's iPhone-13-mini battery order.

> Spec written under the Superpowers brainstorming flow; located in the skos repo
> (project convention: design docs live beside the code they describe) rather than
> `docs/superpowers/specs/`.

---

## 1. Context: what already exists (reuse, don't reinvent)

`skos.gtd_ingest` is a working ports-and-adapters framework:

- **Sink:** `capture(GtdCapture) -> id | None`: normalizes, **dedupes by
  `(source, source_ref)`**, appends to the unified JSON GTD store.
- **Port:** `GtdSourceAdapter`: push adapters call `emit()`; pull adapters
  implement `poll() -> list[GtdCapture]` and are drained by `drain()`.
- **Live adapters:** `email`, `telegram`, `calendar` (pull); `itil`, `cron` (push).
- **Scheduling:** `skingest-maintain.timer` (systemd --user) + a `sk-cron-run`
  cron pipeline with a run-ledger; failures auto-capture a GTD item + sk-alert.
- **Notify:** sk-alert (realtime, failure/urgent) + 07:00 Email Brief / 08:00 Ops
  Report digests → Hermes DM.
- **Inference:** `skgateway` (OpenAI-compatible proxy, `:18780`) with a
  **difficulty auto-router** → `ornith` (local/free, `192.168.0.100:8082`) for
  easy/bulk, escalating hard cases. `consciousness_loop` already uses this seam.
- **Surface:** `skos status …` CLI + a `features/skos` surface in skchat-app.

**The gap:** `capture()` is **create-or-skip**. On a duplicate `source_ref` it
returns `None` and mutates nothing. There is no way to walk *one* GTD item through
a changing state (ordered → shipped → out-for-delivery → delivered) or to
auto-complete it. Delivery/order tracking is the first source whose items are
**stateful**, so it needs an update path the framework does not have yet.

---

## 2. Goals / Non-goals

**Goals**
1. Add an **`upsert` capture path** to `skos.gtd_ingest`: given a stable
   `source_ref`, create the item if new, else **update it in place** (text, status,
   meta, list-file move). This is additive and does not change existing `capture()` callers.
2. Add a **stateful `order` pull adapter** that tracks deliveries and drives their
   GTD items through a lifecycle via `upsert`, auto-completing on delivered.
3. **Derive one Telegram notification per real state change** (quiet by default,
   tier-aware), reusing the existing sk-alert/digest machinery. Never on a poll
   that finds no change.
4. Route any LLM-touch (parse an Amazon status email → normalized state) through
   **skgateway** so the auto-router lands it on **ornith**.
5. Document the **Claude-Code operating convention** (skos-first defaults) and the
   exact CLAUDE.md block, so this runtime captures/tracks through skos by default.
6. **Pilot:** the battery order (`113-5638977-2258657`) rides the whole path
   end-to-end on the existing timer.

**Non-goals**
- No new store, no new scheduler, no new notify channel: all exist.
- No rewrite of `capture()` semantics for existing push/pull adapters.
- No new app: the pane-of-glass is the existing skchat-app `features/skos`.
- Order tracking is not a general "workflow engine": just a linear state list
  with a terminal `complete_on` state. (ITIL owns richer workflows.)

---

## 3. The `upsert` primitive (the one core change)

### 3.1 Where the "watch recipe" lives: inside the GTD item

Per the source-of-truth decision, the tracking state lives **on the waiting-for
item itself**, namespaced under `meta` (matches the existing `email_*`/`itil_*`
convention), so there is no second store to drift:

```jsonc
{
  "id": "…", "text": "WAITING: iPhone 13 mini battery ×2, arriving Sun Jul 5",
  "source": "order", "source_ref": "amazon:113-5638977-2258657",
  "status": "waiting", "context": "@errand", "priority": "low",
  "order": {                                   // the watch recipe, in-item
    "vendor": "amazon",
    "order_id": "113-5638977-2258657",
    "account": "…@gmail.com",                  // which gog mailbox sees the emails
    "states": ["ordered","shipped","out_for_delivery","delivered"],
    "state": "ordered",                        // current, updated by the adapter
    "eta": "2026-07-05",
    "complete_on": "delivered",                // terminal → item goes done+archive
    "notify_tier": "normal"                    // normal=telegram; high=+escalation
  }
}
```

### 3.2 The sink function

```python
def upsert(c: GtdCapture, *, transition: str | None = None) -> tuple[str, str]:
    """Create-or-update by (source, source_ref). Returns (item_id, action) where
    action ∈ {created, updated, unchanged, completed}.

    - No existing item          → behave like capture() (create), action=created.
    - Existing + no field delta  → action=unchanged (NO write, NO notify).
    - Existing + delta           → patch text/status/priority/meta in place; if the
      new status maps to a different list file, MOVE the item between files;
      action=updated.
    - transition == meta['complete_on'] state → set status=done, completed_at, move
      to archive.json; action=completed.
    """
```

Design rules:
- **Additive & isolated.** `capture()` is untouched; `upsert()` is a sibling. The
  base `GtdSourceAdapter.drain()` keeps using `capture()`; a new
  `drain_stateful()` (or `mode = "upsert"` on the adapter) uses `upsert()`. Only
  the `order` adapter opts in.
- **Change detection = `unchanged` short-circuit.** Compare the incoming
  normalized state against `meta.order.state`; equal ⇒ no write, no notify. This
  is what makes daily polling idempotent and quiet.
- **List-file moves reuse existing helpers** (`_find_item_across_lists`,
  `_remove_item_from_list`) already used by ITIL's `_complete_gtd_items`.
- **Notify is derived, not embedded.** `upsert` returns the `action`; the *adapter*
  (not the sink) decides whether to fire a Telegram ping, so the sink stays a pure
  store operation (mirrors "digests are reads; only capture()/labels mutate").

---

## 4. The `order` pull adapter

`skos/adapters/order.py`: subclasses `GtdSourceAdapter`, auto-registers, drained
by `skos ingest order`.

```python
class OrderAdapter(GtdSourceAdapter):
    name = "order"
    def poll(self) -> list[OrderObservation]:
        # 1. Load waiting-for items with meta.order (the tracked orders).
        # 2. For each, find the latest vendor status email via skos.mail/gog
        #    (search the item's account for the order_id).
        # 3. Extract the normalized state via _classify() (skgateway→ornith).
        # 4. Yield an observation carrying (item, new_state, detail, eta).
    def drain(self) -> list[str]:               # override: stateful
        out = []
        for obs in self.poll():
            item_id, action = upsert(obs.capture(), transition=obs.new_state)
            if action in ("updated", "completed"):
                self._notify(obs, action)        # derived Telegram ping
                out.append(item_id)
        return out
```

**State extraction (`_classify`), the only LLM touch:**
- Deterministic first: regex/subject match on Amazon's own phrasing
  (`"has shipped"`, `"Arriving"`, `"out for delivery"`, `"was delivered"`,
  `"delivery attempted"`, `"delayed"`). Covers ~all Amazon mail with zero tokens.
- Fallback to LLM **only** when deterministic parsing is ambiguous: POST to
  `http://localhost:18780/v1/chat/completions` (skgateway), model `sk-default`
  (auto-router → ornith), a strict "return one of `<states>`" prompt. Cheap, fast,
  and it degrades to `unchanged` if the gateway is down (never invents a state).

**Where tracked orders come from:** an order becomes tracked the moment a
waiting-for item carries a `meta.order` block. Seeding one is either a
`skos ingest order --add …` helper (writes the item via `capture()` with the meta)
or a normal `gtd capture` + clarify that attaches the block. (Helper CLI is a small
convenience, specced in §7.)

---

## 5. Notify: derived, tiered, on-change-only

`OrderAdapter._notify(obs, action)`:
- `normal` tier → one line to Chef's Telegram DM via the existing send path
  (`skcapstone telegram send` / sk-alert normal), e.g.
  `📦 iPhone 13 mini battery ×2, out for delivery (ETA today).`
- `completed` → `✅ Delivered: iPhone 13 mini battery ×2. Marked done + archived.`
- `high` tier → same, plus escalation channels per the item's `notify_tier`.
- **Never** fires on `unchanged`/`created`-by-poll noise. State changes only.

Roll-forward context also lands naturally in the **08:00 Ops Report** (it already
reads the store), so even a missed push still surfaces in the daily digest.

---

## 6. Pilot: the battery order

1. Seed a waiting-for item with the `meta.order` block above
   (`source_ref = amazon:113-5638977-2258657`, `account` = the mailbox that got the
   Amazon confirmation, `eta = 2026-07-05`).
2. The existing `skingest-maintain` timer (or a `sk-cron-run`-wrapped
   `skos ingest order` line) drains the adapter on schedule.
3. Expected: `ordered → out_for_delivery → delivered` over Jul 3-5, one Telegram
   ping per transition, auto-complete + archive on delivered.
4. **Acceptance:** the item transitions without duplicates, exactly one notify per
   real change, and it lands in `archive.json` with `status=done` on delivery,
   with the adapter having written nothing on no-change polls (verify via
   run-ledger + a store diff).

---

## 7. Claude-Code operating convention (my lane)

New doc `docs/CLAUDE-CODE-GTD-CONVENTION.md` (skos repo) + a block appended to
`~/.claude/CLAUDE.md` so **this runtime** defaults to skos. Proposed block:

```md
## SKOS GTD: single pane of glass (default for all work)
skos gtd-ingest is Chef's ONE unified GTD. Default to it, don't invent side-lists:
- **Capture:** any actionable item that surfaces in a session → capture to the
  unified GTD (`skcapstone gtd capture` / `skos.gtd_ingest.capture`), tagged with a
  real `source`/`source_ref`. Not just chat.
- **Track:** "keep an eye on / waiting on X" → a `waiting-for` item; if it's an
  order/delivery, attach a `meta.order` block so the `order` adapter drives it.
- **Session start:** read open next-actions + waiting-fors (`skcapstone gtd next`,
  `gtd waiting`); surface anything overdue/stale.
- **Process, don't just add:** when touching GTD, reconcile: dedupe, age, complete
  finished items, link ITIL/projects.
- **Adding a source = one adapter** on the gtd-ingest port. Never a parallel store.
- **Inference** for any triage/parse/summarize step → skgateway `:18780`
  (`sk-default`, auto-router → ornith). Don't hardcode a model.
```

A small helper CLI `skos ingest order --add --order-id … --account … --eta …`
(writes the seeded item) is specced here but optional for the pilot (the item can
be seeded by hand first).

---

## 8. Risks & decisions

| Risk | Mitigation |
|---|---|
| **Unknown-field round-trip**: does the skcapstone GTD manager preserve `meta.order` on rewrite (clarify/move/done)? | The skos sink loads→appends→saves whole dicts, so it round-trips. Verify the skcapstone CLI path preserves unknown keys; if it strips them, keep all order state under `meta` (already dict-passthrough in `capture()`) and never round-trip order items through the lossy CLI path. Add a test. |
| Amazon email phrasing drift | Deterministic matcher first; LLM fallback via ornith; unknown ⇒ `unchanged` (never guess). |
| skgateway/ornith down | `_classify` degrades to deterministic-only; on total ambiguity returns `unchanged`. No crash, no false transition. |
| Secrets in cron env | Reuse the existing pattern: `GOG_KEYRING_PASSWORD` is already exported in the `sk-cron-run` cron lines. |
| Duplicate notifications | Single source of truth for "did state change" is the `unchanged` short-circuit in `upsert`; notify only on `updated`/`completed`. |
| Scope creep into a workflow engine | Linear `states` + terminal `complete_on` only. Anything richer routes to ITIL. |

---

## 9. Test plan

- **Unit:** `upsert()`: created / unchanged (no write) / updated (+list move) /
  completed (→archive). Golden store fixtures; assert byte-level no-write on
  unchanged.
- **Unit:** `_classify` deterministic matcher over a corpus of real Amazon subject
  lines (ship/out-for-delivery/delivered/delayed); LLM path mocked.
- **Integration:** seed a fake order item, feed staged emails, drain, assert the
  single item walks the lifecycle with one notify per change and lands in archive.
- **E2E (pilot):** the real battery order over Jul 3-5 (marker-gated so CI skips).

---

## 10. Out of scope (future, same pattern)

- Other vendors (UPS/USPS/FedEx tracking numbers) = more `_classify` matchers /
  backends behind the same adapter.
- Generic "watch" sources (build status, PR review, reply-awaited) = additional
  stateful adapters using the same `upsert` primitive.
- The skchat-app `features/skos` surface rendering tracked orders. Separate FE work.
