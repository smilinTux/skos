# SKWorld density and type scale: the pass across all three surfaces

Date: 2026-08-11
Author: Fable (claude-fable-5)
Status: PROPOSED
Brief: `~/clawd/docs/fable-skworld-code-section-brief.md`
Companions: `2026-08-11-skworld-code-section-architecture.md`,
`../plans/2026-08-11-skworld-code-section-implementation.md`

## 1. Summary and the actual problem

Chef's complaint is "fonts are too big." The audit says the theme scale is
only half the problem. Verified state of skworld-app:

- The theme scale (`sovereign_typography.dart`) runs 12 to 28 with generous
  line heights, and every Material sub-theme inherits it, so headers, nav,
  dialogs, and chips all sit at the roomy end.
- **328 hardcoded `fontSize:` literals live outside the theme files**, and
  their distribution (12 appears 87 times, 11 x57, 13 x47, 10 x31, plus
  off-ladder 9, 10.5, 12.5, 7) shows developers have been hand-rolling a
  denser micro scale the theme refuses to provide. The theme is too big AND
  it has no small roles, so the small sizes went feral.
- Spacing is a 4-px-ish grid with heavy off-beats (10, 14, 20), ListTiles
  run at Material's default 56 to 72 dp heights (only 2 `dense: true` in
  the whole app), `ThemeData` sets no `visualDensity`, and `GlassCard`
  defaults to 16 padding everywhere.
- **Zero `TextScaler`/`textScaleFactor` usage** anywhere, and no
  `MaterialApp.builder`, so OS text scaling works today only by Flutter's
  default pass-through, with nothing protecting layouts at large scales.

So the pass is three things: (1) a smaller default scale WITH sanctioned
micro roles so the feral literals have somewhere legal to go, (2) a density
token that moves spacing and component metrics together with type, and
(3) guards so it cannot regress. Buzz's lesson transfers as discipline, not
mechanics: their rem-based ramp exists so browser zoom keeps working, and
their lint guard exists because a hardcoded-px regression actually shipped
(their PR #891). Flutter has no rem; the equivalent invariant here is that
OS accessibility scaling keeps working, and the equivalent guard is a
literal ban with a ratchet.

## 2. Findings the brief needs corrected or sharpened

1. **This pass amends a documented contract.** PRD.md v1.0.0 (2026-02-24)
   specifies the type table the theme docstring claims to "match exactly"
   (display 28, heading 20, body 15, caption 12, mono 13). The density pass
   changes those numbers, so the PRD table, the docstring, and the theme
   move in the same PR. Not silent divergence: a PRD amendment section is
   part of card D-1.
2. **The pass also IMPLEMENTS two PRD promises that were never built**:
   "All text scales with system font size preference" (currently only true
   by accident of Flutter defaults, untested) and the settings mock's
   "Font Size: Medium" row (never implemented). The density setting is that
   row, delivered.
3. **Surface 3 is tiny.** The skcode web client is ONE file,
   `skharness/src/skharness/client/index.html` (675 lines, no framework, no
   build step), with every font size a hardcoded px literal duplicated once
   in a mobile media query. It is already visually dense (10 to 15 px); its
   pass is tokenization and zoom correctness, not shrinking. The brief's
   fear of an unlocatable CSS codebase is unfounded.
4. **Buzz detail corrections** (so we copy the real thing): their fontSize
   extension has five entries not three; transcript timestamps use
   `text-xs` not `text-2xs`; the guard's negative lookbehind is `(?<!-)`
   protecting `--font-size:` custom properties; the allowlist is exact
   `"path:literal"` strings, four decorative entries total.

## 3. Surface 1: the Flutter type scale

### 3.1 Density axis

Three densities. `compact` is the new default (this is the "fonts are too
big" fix); `comfortable` preserves today's numbers for anyone who wants
them (and is the accessibility-friendly floor); `dense` is an opt-in for
desktop/rail layouts. Chef flips between them in Settings (the PRD's Font
Size row) and the choice persists in Hive like the daemon URL override.

### 3.2 Before/after type scale table

Sizes in logical px (Flutter sp equivalent). Line heights change only where
noted; body text keeps 1.5 for chat readability.

| Role | Today (comfortable) | compact (NEW DEFAULT) | dense | Height today -> compact |
|---|---|---|---|---|
| displayLarge | 28 w700 | 24 | 22 | 1.2 -> 1.15 |
| titleLarge | 20 w600 | 18 | 16 | 1.3 -> 1.25 |
| titleMedium | 17 w600 | 15 | 14 | 1.3 -> 1.3 |
| titleSmall | 15 w500 | 14 | 13 | 1.4 -> 1.35 |
| bodyLarge | 15 w400 | 14 | 13 | 1.5 (keep) |
| bodyMedium | 14 w400 | 13 | 12 | 1.5 (keep) |
| bodySmall | 13 w400 | 12 | 11 | 1.5 -> 1.45 |
| labelLarge | 14 w600 | 13 | 12 | 1.4 -> 1.35 |
| labelMedium | 13 w500 | 12 | 11 | 1.4 -> 1.3 |
| labelSmall | 12 w400 | 11 | 10 | 1.4 -> 1.3 |
| mono (default) | 13 | 12.5 | 12 | 1.5 -> 1.45 |
| micro (NEW) | none | 11 w400 1.3 | 10 | meta workhorse: timestamps, badges rows, event meta (Buzz `2xs` = 11px, adopted) |
| badge (NEW) | none | 10 w500 1.2 | 10 | count/status badges (Buzz `badge` = 10px, adopted) |

Explicitly NOT adopted: Buzz's `3xs` (8 px). Below 10 px fails WCAG
practical legibility on a phone held at arm's length, and every SKWorld
surface is touch-first. 10 is the floor, enforced by the guard.

Design rule that makes the burn-down possible: `micro` and `badge` are real
named styles on `SovereignTypography` (density-resolved like every other
role), so the 328 literals have sanctioned targets: 10/11 literals map to
badge/micro, 12/13 to labelSmall/bodySmall, and the off-ladder values (7,
9, 10.5, 12.5) die.

### 3.3 Token design: how density is implemented

Answering the brief's (a)/(b)/(c) directly: **(b), a `SovereignDensity`
token that the theme builder resolves through, with OS scaling left to the
system TextScaler on top. Not (a)**: a global `TextScaler` multiplier would
also scale user chat content and fight the OS scaler multiplicatively, and
it cannot move spacing. Concretely:

- `SovereignDensity` enum `{comfortable, compact, dense}` in
  `packages/skchat_ui/lib/src/theme/sovereign_density.dart`, with
  per-role lookup tables (explicit numbers per the table above, NOT a float
  multiplier: multipliers produce 12.42 px garbage and make the guard
  unenforceable).
- `SovereignTypography.buildTextTheme({dark, density})` and
  `SovereignTheme.dark({density})/light({density})` thread it through; all
  existing sub-themes (appBar, navigationBar, chip, dialog, snackbar,
  input) inherit automatically since they already reference the text theme.
- `ThemeData.visualDensity`: comfortable `VisualDensity.standard`, compact
  `VisualDensity(horizontal: -1, vertical: -1)`, dense `(-2, -2)`. This
  moves Material component internals (buttons, list tiles, checkboxes) in
  step for free.
- A `densityProvider` (Riverpod, Hive-persisted) feeds `MaterialApp.router`
  in `lib/main.dart`; changing it rebuilds the theme. Widgets never read
  density directly; they read theme roles and spacing tokens.

## 4. The spacing scale (density is not only type)

Canonical ladder, 4-pt grid: `2, 4, 8, 12, 16, 20, 24, 32` as
`SovereignSpacing.s2 ... s32`. The audit shows the codebase already lives
on this grid with off-beats (6, 10, 14); off-beats round to a neighbor
during burn-down.

Density-resolved semantic tokens (the ones that create felt density):

| Token | comfortable | compact | dense | Applies to |
|---|---|---|---|---|
| rowVPad | 8 | 6 | 4 | list rows, transcript rows |
| cardPad | 16 | 12 | 10 | GlassCard default padding |
| gutter | 16 | 12 | 12 | screen edge padding |
| sectionGap | 24 | 16 | 12 | between sections |
| listTile contentPadding vertical | 4 | 2 | 0 | listTileTheme |
| listTile minTileHeight | Material default | 44 | 40 | listTileTheme (new) |
| navCellVPad | 10 | 8 | 8 | bottom nav cells |
| avatarList | 40 | 36 | 32 | list row avatars |
| iconNav | 24 | 24 | 22 | nav icons (held at 24 on touch) |

**Touch target floor: 48x48 dp, held at every density**, per the PRD's
accessibility contract. Density shrinks the VISUAL row, never the tap
area: `MaterialTapTargetSize.padded` stays the default, and any custom
InkWell/GestureDetector row that drops below 48 visual height must carry a
`minimum interactive size` wrapper (this is a review rule, cheap to hold
because rows rarely go below 40 + padding).

## 5. OS accessibility scaling (the non-negotiable)

A user who enlarged system text still gets it, at every density:

- Density sets BASE sizes; the OS `MediaQuery.textScaler` multiplies on
  top, which is Flutter's default behavior for every `Text` that does not
  override it. The invariant is therefore: **never pass
  `textScaler: TextScaler.noScaling` and never read
  `textScaleFactor` to "correct" sizes.** Today's codebase has zero such
  overrides (verified); the guard keeps it that way (section 7).
- One `MaterialApp.router` `builder:` is added (none exists today) wrapping
  the app in `MediaQuery.withClampedTextScaling(maxScaleFactor: 2.0)`.
  No minimum clamp: small-text OS preferences are also respected. The max
  clamp protects layouts from the 3x pathological end while honoring the
  full accessibility range Android/iOS actually ship in settings.
- Compact base + 2.0 OS scale = bodyMedium at 26 logical px; golden tests
  render key screens (chats list, transcript row, coord board row) at OS
  scale 1.0, 1.3, and 2.0 in compact density and assert no overflow
  exceptions. This is the test the PRD's promise never had.
- The density setting and OS scaling compose: a low-vision user on compact
  still gets big text; a sharp-eyed user on comfortable still gets roomy
  text. They are independent axes and the code never conflates them.

## 6. Surface 2: shell chrome (spacing made real)

File-level moves, using the tokens above:

- `app_shell_scaffold.dart`: nav cell vertical padding 10 -> navCellVPad;
  the two hardcoded `fontSize: 12` rail label styles -> theme labelSmall;
  offline banner `fontSize: 12` -> micro; grip and blur untouched.
- `app_shell.dart`: badge text -> badge token.
- `app_drawer_sheet.dart`: grid tile height 52 -> 48; labels -> labelSmall.
- `external_module_pane.dart` + `skcode_pane.dart` header chrome: subtitle
  `fontSize: 12` -> micro; header padding 16/10 -> gutter/rowVPad.
- `glass_widgets.dart`: `GlassCard` default padding 16 -> cardPad;
  `SoulAvatar` badge `fontSize: 7` -> badge (10) with the avatar sized up
  a step, or the badge dropped to a dot; 7 px text is unreadable and dies.
- `sovereign_theme.dart`: `listTileTheme` gains density-resolved
  contentPadding + minTileHeight; `visualDensity` wired (section 3.3).
- Burn-down wave 1, the top offenders (121 of 328 literals, 37%):
  `spaces/space_room_screen.dart` (26), `coord/coord_board_screen.dart`
  (18), `skos/skos_files_screen.dart` (17), `conf/conf_screen.dart` (17),
  `calls/livekit_call_screen.dart` (17), `profile/profile_screen.dart`
  (14), `identity/identity_card_screen.dart` (11). Each literal maps to
  the nearest role/micro/badge; ladder deviations round.
- Remaining literals burn down opportunistically under the ratchet
  (section 7): touched file = cleaned file.

## 7. The lint guards (a density change without a guard regresses)

### 7.1 Flutter: literal ban with a ratchet

A repo test (`test/font_literal_guard_test.dart`, pure Dart, no plugin):

- Scans `lib/` and `packages/` for `fontSize:` followed by a numeric
  literal, and for `textScaler: TextScaler.noScaling` /
  `textScaleFactor` (the accessibility invariant, section 5).
- Allowed zones: `packages/skchat_ui/lib/src/theme/` (the tokens
  themselves) plus an explicit allowlist file
  (`tool/font_literal_allowlist.txt`) of `path:line-content` entries for
  genuinely decorative cases (mirrors Buzz's four-entry decorative
  allowlist).
- **Ratchet, not big bang**: a checked-in baseline
  (`tool/font_literal_baseline.txt`) lists today's 328 entries as
  `path:literal` pairs. The test fails on any entry NOT in baseline or
  allowlist (new literal = red), and fails if the baseline contains stale
  entries (forcing baseline shrink as burn-down lands, so it only moves
  down). Same mechanism as Buzz's `check-px-text-core.mjs` overrides,
  adapted to a codebase with 328 preexisting hits instead of 4.

### 7.2 skcode client: full conversion plus a pytest gate

The client is one file, so no ratchet: convert completely, then ban.

- All font sizes become rem tokens on `:root` (base inherits the browser's
  16 px, which is exactly what keeps browser zoom working, Buzz's PR #891
  lesson):

| Token | Value | px equiv | Replaces |
|---|---|---|---|
| `--fs-badge` | 0.625rem | 10 | 10px literals |
| `--fs-micro` | 0.6875rem | 11 | 11px (Buzz 2xs value) |
| `--fs-caption` | 0.75rem | 12 | 12px |
| `--fs-body` | 0.8125rem | 13 | 13px + body default |
| `--fs-emph` | 0.875rem | 14 | 14px |
| `--fs-title` | 0.9375rem | 15 | 15px |

- The `@media (max-width: 640px)` block stops re-declaring ~15 individual
  sizes and instead swaps the token VALUES once. Sizes are roughly
  preserved (the client is already dense); this surface's pass is hygiene
  and zoom correctness, not shrinking.
- Guard: `skharness/tests/test_client_type_guard.py` reads
  `client/index.html` and asserts zero matches of
  `(?<!-)font-size:\s*\d+(\.\d+)?px` and zero `text-\[\d` style arbitrary
  values (regexes lifted from Buzz's `check-px-text-core.mjs`, including
  the `--font-size:` negative lookbehind, with attribution in the
  docstring). Runs in the existing pytest suite, no Node dependency.

### 7.3 What is deliberately not linted

Spacing literals. 4-pt grid adherence is a review rule and a token
convention; a spacing lint over a codebase with hundreds of legitimate
EdgeInsets would be noise. Type is where the regression risk concentrates
(that is also Buzz's scoping: their guard checks text sizes only).

## 8. Per-surface file list

Surface 1 (type scale + token), repo skworld-app:
- `packages/skchat_ui/lib/src/theme/sovereign_typography.dart` (rework)
- `packages/skchat_ui/lib/src/theme/sovereign_density.dart` (new)
- `packages/skchat_ui/lib/src/theme/sovereign_spacing.dart` (new)
- `packages/skchat_ui/lib/src/theme/sovereign_theme.dart` (density thread,
  visualDensity, listTileTheme)
- `packages/skchat_ui/lib/src/theme/glass_widgets.dart` (cardPad, badge)
- `packages/skchat_ui/lib/src/theme/theme.dart` (exports)
- `lib/main.dart` (builder clamp, density-fed theme)
- `lib/features/profile/profile_screen.dart` (the density Settings row)
- `PRD.md` (typography table amendment + density section, same PR)
- `test/font_literal_guard_test.dart`, `tool/font_literal_baseline.txt`,
  `tool/font_literal_allowlist.txt` (new)
- golden tests for OS-scale 1.0/1.3/2.0

Surface 2 (shell chrome + burn-down wave 1), repo skworld-app:
- `lib/features/shell/app_shell_scaffold.dart`, `app_shell.dart`,
  `app_drawer_sheet.dart`, `external_module_pane.dart`,
  `toolbar_module_actions.dart`
- `lib/features/skcode/skcode_pane.dart` (header chrome only)
- top offenders: `lib/features/spaces/space_room_screen.dart`,
  `lib/features/coord/coord_board_screen.dart`,
  `lib/features/skos/skos_files_screen.dart`,
  `lib/features/conf/conf_screen.dart`,
  `lib/features/calls/livekit_call_screen.dart`,
  `lib/features/profile/profile_screen.dart`,
  `lib/features/identity/identity_card_screen.dart`

Surface 3 (skcode client), repo skharness:
- `src/skharness/client/index.html` (tokenize + rem + media-query
  consolidation)
- `tests/test_client_type_guard.py` (new)

## 9. Rollout and risk

- Ships FIRST, before the Code section work (plan doc Phase 0): zero
  server-side changes, one repo per surface, instantly visible, and the
  compact default is a one-line revert (`density: comfortable`) if Chef
  hates it on a real phone.
- The `comfortable` table is byte-identical to today's scale, so the
  escape hatch is exact.
- Risk concentrates in OS-scale overflow on compact; the golden tests at
  1.3/2.0 are the mitigation and are part of D-1, not a follow-up.
- CI note from the fleet standard: skworld-app CI tests the MERGE, not the
  tip; land D-1 (tokens) before D-2 (chrome) to keep each diff reviewable.
