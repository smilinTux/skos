# SKOS GTD — the Claude-Code operating convention

**Status:** active (2026-07-03) · **Audience:** Claude Code / Opus runtime · **Spine:** [`gtd-ingest-architecture.md`](./gtd-ingest-architecture.md)

`skos gtd-ingest` is Chef's **one unified GTD** and single pane of glass. This doc
tells *this runtime* how to default to it, so work doesn't scatter into ad-hoc
chat lists. The canonical short form lives as a block in `~/.claude/CLAUDE.md`;
this is the full rationale + how-to.

## The rule
Route work through the unified GTD by default. Don't invent side-lists.

| When… | Do |
|---|---|
| An actionable item surfaces in any session (order, fix, follow-up, errand) | **Capture** it to the unified GTD — `skcapstone gtd capture` or `skos.gtd_ingest.capture(GtdCapture(...))` — with a real `source` + stable `source_ref`. Not just chat. |
| Chef says "keep an eye on / track / waiting on X" | Create a **waiting-for** item. If it's an order/delivery, attach a `meta.order` block so the **`order` adapter** drives it statefully (see below). |
| A source needs watching that isn't email/calendar/telegram/itil/cron/order | Write **one adapter** on the gtd-ingest port (`poll()` for pull, `capture()`/`upsert()` for push/stateful). Never a parallel store. |
| Session start | Read open next-actions + waiting-fors (`skcapstone gtd next`, `gtd waiting`); surface anything overdue/stale. |
| Touching GTD | **Process, don't just add** — reconcile: dedupe, age, complete finished items, link ITIL/projects. |
| Any triage / parse / summarize / classify step | Send it through **skgateway** (`http://localhost:18780/v1`, model `sk-default`) and let the **auto-router** land it on **ornith**. Don't hardcode a model. |

## Stateful tracking (orders/deliveries) — the `order` adapter
An order is just a `waiting-for` item carrying a `meta.order` block:

```jsonc
"order": {
  "vendor": "amazon", "order_id": "…", "account": "<gog mailbox>",
  "states": ["ordered","shipped","out_for_delivery","delivered"],
  "state": "ordered", "eta": "YYYY-MM-DD",
  "complete_on": "delivered", "notify_tier": "normal"
}
```

- `skos ingest order` (cron, every 3h) drains it: reads the vendor's status emails
  via `gog`, extracts the furthest-along state (deterministic subject match →
  ornith only on ambiguity), and **`upsert`s** the *same* item forward.
- One Telegram ping **per real state change only** (`unchanged` polls write nothing).
- On the `complete_on` state → the item is marked `done` and moved to `archive.json`.

Seed one by hand or (future) `skos ingest order --add …`.

## Key primitives
- `skos.gtd_ingest.capture(GtdCapture)` — create-or-**skip** (dedupe by `(source, source_ref)`). For one-shot captures.
- `skos.gtd_ingest.upsert(GtdCapture) -> (id, action)` — create-or-**update** for stateful sources; `action ∈ {created, unchanged, updated, completed}`; **no write on `unchanged`**.
- `GtdSourceAdapter` — base for adapters; register in `skos/adapters/__init__.py`.

## Guardrails
- Never delete/trash mail — label + archive only (reversible).
- Digests/reads never mutate; only `capture()`/`upsert()`/label ops write.
- Secrets (gog keyring, tg token) are read from existing stores; adapters never embed them.
- Unknown vendor phrasing ⇒ **no change**, never a guessed transition.
