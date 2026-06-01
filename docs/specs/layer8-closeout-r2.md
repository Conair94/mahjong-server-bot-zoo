# Spec 22 — Layer 8 close-out, round 2

Seven items raised during browser-verify of [layer8-closeout.md](layer8-closeout.md) §1/§2/§4 on 2026-05-27. Three are pure UI polish (§§ 22.2, 22.3, 22.4), two are bug fixes (§§ 22.1, 22.5), one is a feature request that opens up a wider design question about claim resolution (§ 22.6), and one is a small renderer correctness fix (§ 22.7).

Sectioned per item. Each is independently implementable. Suggested order at the bottom.

This spec also closes out the **§3 pinwheel-`?`-flicker** investigation from [layer8-closeout.md](layer8-closeout.md) — § 22.1 below subsumes it with a sharper diagnosis (the `?` was an information leak, not just a flicker).

## Context: what was learned in browser-verify

The 2026-05-27 implementation pass landed §1 / §2 / §4 cleanly under tests but live play surfaced edge cases that pure-jsdom Playwright fixtures missed:

- Item 1 (claim-window arrow): the `?` glyph during CLAIM_WINDOW *signals to all four seats* that someone is deciding whether to claim. That's information you only have at a physical table if you watch the other players' body language — it shouldn't leak into the wire-derived UI.
- Item 3 (selection highlight under unicode): the `.tile-mod.selected` rule applies `text-decoration: underline` but the unicode mahjong glyphs render in a way that doesn't reliably show the underline under some browser default fonts. ASCII mode shows the underline fine.
- Item 5 (BUGANG stall): live-play discovery, no test fixture exists for "BUGANG from hand → expect replacement draw." The engine has a path for opportunity-from-draw BUGANG but the hand-source variant looks unfinished.

The pattern: pinning behaviour with jsdom + unit tests is necessary but not sufficient. Live two-tab play remains the gate for declaring UI work done.

---

## § 22.1 Claim-window arrow leak (supersedes [layer8-closeout.md](layer8-closeout.md) §3)

### Goal

The pinwheel arrow currently returns `?` when `view.phase === "CLAIM_WINDOW"`. This was meant as a "someone is deciding" cue but it *broadcasts* the fact that a claim is being considered — information a non-claiming player should not have. Replace with: arrow always points at `last_discard.seat` regardless of phase.

### Why

`last_discard` is already public (the tile is shown on the table). Pointing at the discarder during CLAIM_WINDOW reveals nothing new and removes the leaky cue. The CLAIM_WINDOW is itself information that all four seats receive in the wire stream (per [wire-protocol.md](wire-protocol.md) — every seat sees its own claim opportunity if any), but seeing a generic `?` on the pinwheel without knowing who is claiming or what they're claiming is the leak — it tells player B that some other player is *thinking about* a claim, which is what makes the arrow drift into "tell" territory.

### Interface

[render.js](../../mahjong/web/static/render.js) `_pinwheelArrow`:

```js
function _pinwheelArrow(view, ownSeat) {
  if (view.phase === "TERMINAL") return "·";
  const ld = view.last_discard;
  if (!ld || ld.seat == null) return "·";
  const relative = (((ld.seat - ownSeat) % 4) + 4) % 4;
  return PINWHEEL_ARROW[relative] ?? "·";
}
```

Remove the `phase === "CLAIM_WINDOW"` branch. The arrow now follows `last_discard.seat` in every phase that has a last-discard, falls back to `·` otherwise.

### Verification fixtures

Update [tests/web/test_cardinal_render.py](../../tests/web/test_cardinal_render.py):

- **Fixture 3 (existing)** — currently asserts `phase=CLAIM_WINDOW` → arrow `?`. Replace with: `phase=CLAIM_WINDOW` + `last_discard.seat=1`, `ownSeat=0` → arrow `→` (the discarder's edge). Same as a DISCARD-phase render.
- **New fixture** — `phase=CLAIM_WINDOW` with `last_discard=null` (claim window for a non-discard origin? — currently impossible per spec, but assert `·` defensively).

The active-badge `.active` class is already tied to `last_discard.seat`, so that doesn't need to change.

### Alternatives considered

- **Show the arrow with a different glyph during CLAIM_WINDOW (e.g. `⇣` vs `↓`).** Same leak in a different costume; rejected.
- **Hide the pinwheel entirely during CLAIM_WINDOW.** Loses the "last discarder" cue that's actually useful. Rejected.

---

## § 22.2 Claimable-action alerts (Tier 3 — UI)

### Goal

When the local player is offered a claim (CLAIM prompt with non-empty `legal_actions` for PENG / CHI / GANG / HU), the existing prompt bar is too subtle — easy to miss in the middle of watching bots play. Add a more attention-grabbing visual cue, ideally one that survives the player looking briefly at another tab.

Sound is noted as a future enhancement; not v1.

### Non-goals

- **Sound effects.** Parked. Add an `<audio>` element with a short ping per claim type later; for now visual only.
- **Browser-level notifications (`Notification.requestPermission`).** Requires user consent prompts; out of scope.
- **Per-claim-type animation.** One alert style for all CLAIM prompts.

### Interface

Two layered cues, both in `<game-pane>`:

1. **Pulsing border on the prompt bar.** Existing `.prompt-bar` gets `animation: claim-pulse 1s ease-in-out infinite alternate` while the current prompt is a CLAIM. CSS keyframe alternates `border-color` between `var(--accent)` and `var(--accent-red)`. Stops on prompt clear.
2. **Pane title chip.** The Game-pane header gains a temporary chip "[ CLAIM AVAILABLE ]" in `var(--accent-red)` while a CLAIM prompt is active. The chip is anchored to the pane header (top of viewport), so it's visible even with the scrollbar at the top.

The detection: `currentPrompt?.kind === "CLAIM" && (currentPrompt.legal_actions ?? []).some(a => a.action !== "PASS")`. PASS-only CLAIM prompts (where you have no actual option) don't trigger the alert.

### Sound (future)

When the user is ready: a single short pings via Web Audio API or a 30-byte WAV in `static/`. Trigger on the same predicate. Honour a user-toggleable mute (localStorage key `mahjong:claim-sound`).

### Verification fixtures

[tests/web/test_prompt.py](../../tests/web/test_prompt.py) extension:

- **claim_prompt_triggers_alert** — render `<game-pane>` with a CLAIM prompt containing `PENG`, assert `.prompt-bar` has class `claim-active` and the pane-header chip exists.
- **discard_prompt_no_alert** — DISCARD prompt → no `claim-active` class, no chip.
- **pass_only_claim_no_alert** — CLAIM prompt with `legal_actions=[{action:"PASS"}]` → no alert (no actual choice).
- **alert_clears_on_prompt_change** — alert visible, then `setPrompt(null)` → both cues gone.

---

## § 22.3 Selection highlight under Unicode tile style (Tier 3 — CSS) — ✅ RESOLVED 2026-06-01

**Fix landed:** [app.js](../../mahjong/web/static/app.js) `.tile-mod.selected` now uses `background-color: color-mix(in srgb, var(--accent) 22%, transparent)` + `border-radius`/`padding` instead of the unreliable `text-decoration: underline`. `color-mix` keeps the tint derived from `--accent` (follows theme swaps, no parallel `--accent-rgb` to maintain). Pinned by `test_fixture_8_selection_background_visible_in_both_styles` (asserts a non-transparent computed background under both ascii and unicode, mounting the real `<game-pane>` so the shadow CSS applies). The ASCII-bracket fallback in the original interface was not needed — the tint reads in both styles.

### Goal

`.tile-mod.selected` applies `text-decoration: underline` + `font-weight: 600`. Under ASCII mode this is clearly visible. Under Unicode mode (the user's preferred style), the underline under the mahjong glyphs doesn't render reliably and the bold weight may not apply to the glyph in some fonts.

Replace with a style that survives the unicode glyph rendering path: a coloured background box, or a bracketing notation, or both.

### Interface

Two layered selection cues:

1. **Background tint.** `.tile-mod.selected { background-color: rgba(var(--accent-rgb), 0.18); border-radius: 0.15em; padding: 0 0.1em; }`. The semi-transparent accent colour reads under both ASCII and Unicode renderings without depending on text-decoration support.
2. **Bracketing in ASCII mode only.** Optional fallback if the tint isn't visible enough: prepend `[` / append `]` around the selected tile in ASCII mode. Skip the brackets in Unicode mode (the tint is already visible).

Verify on light theme + dark theme + both tile styles. The tint should remain visible on all four combinations.

### Verification fixtures

- **selected_has_background_class** — already covered by [test_hand_display.py § Fixture 1](../../tests/web/test_hand_display.py); add an assertion that the computed `background-color` is non-transparent under both `tileStyle="ascii"` and `tileStyle="unicode"`.
- **selection_visible_in_both_styles** — render the same hand twice (ascii / unicode) with `selectedTile=1`, take a screenshot of each, assert the selected tile's bounding box differs from its neighbours by visible pixels in both. (This is heavier than the existing fixtures; use Playwright's `expect(locator).toHaveScreenshot()` or fall back to per-pixel diff on the selected element.)

### Alternatives considered

- **Box-shadow inset.** Looks fine in dark theme, ugly in light. Tint is theme-agnostic.
- **Move the selected tile up by 0.2em.** Breaks the line height for the row; rejected.

---

## § 22.4 Discard tile font sizing (Tier 3 — CSS) — ✅ RESOLVED 2026-06-01

**Fix landed:** [render.js](../../mahjong/web/static/render.js) `renderDiscards` now wraps its output in `<span class="discard-row">`; [app.js](../../mahjong/web/static/app.js) adds `.discard-row .tile { font-size: 1.2em }` (dragons/face-down 1.45em). Hand tiles stay at 1.8em. Pinned by `test_fixture_9_discards_wrapped_in_discard_row` (structure) + `test_fixture_9b_discard_tile_smaller_than_hand_tile` (computed px on the mounted `<game-pane>`).

### Goal

Concealed-hand tiles and discard tiles render at the same `.tile { font-size: 1.8em }` rule today. The discard pile is high-frequency, low-importance background information; the hand tiles are the player's primary attention object. Re-balance: hand tiles stay at 1.8em, discard tiles shrink to ~1.2–1.3em.

### Non-goals

- Different sizes per discard age (newest larger, older smaller). Out of scope.
- Per-suit sizes. Out of scope.

### Interface

[app.js](../../mahjong/web/static/app.js) GamePane styles — add a `.discard-row` wrapper class (or a more specific selector) that overrides `.tile` font-size inside it:

```css
.discard-row .tile {
  font-size: 1.2em;
}
.discard-row .tile.dragon,
.discard-row .tile.face-down {
  font-size: 1.45em;  /* keep the dragon / face-down slightly larger ratio */
}
```

Renderer change: [render.js](../../mahjong/web/static/render.js) `renderDiscards` wraps its output in a span/div with class `.discard-row` (currently it returns a bare list of tile spans joined by spaces).

The `last_discard` glyph in the pinwheel center is unaffected — it's a separate visual element with its own size (already large per [cardinal-ui.md § Large unicode tile](cardinal-ui.md)).

### Verification fixtures

- **discard_row_has_class** — render a SeatView with a non-empty discards list; assert the discard area is wrapped in `.discard-row`.
- **discard_smaller_than_hand** — compute `font-size` on a `.tile` inside `.discard-row` vs a `.tile` inside the concealed area; assert the discard one is smaller.

---

## § 22.5 BUGANG from hand stalls the hand loop (Tier 1 — engine bug) — ✅ RESOLVED 2026-06-01

**Fix landed:** [diff.py](../../mahjong/records/diff.py) `GANG` branch now appends a `DRAW` event when the post-transition `drawn_count` increased — surfacing the gangshanghua replacement for all three gang variants. Pinned by `test_diff_{added,concealed,exposed}_gang_emits_replacement_draw` in [tests/records/test_diff.py](../../tests/records/test_diff.py). The client reducer ([apply_event.js](../../mahjong/web/static/apply_event.js)) already handled both `CLAIM_DECISION(GANG)` (meld upgrade) and `DRAW` (hand restore) correctly, so no client change was needed. **Still owed:** live two-tab browser verify of a real BUGANG (the working-agreement gate for UI-facing fixes).

### Goal

When a player declares BUGANG (added kong) by promoting a melded PENG with a tile from their concealed hand, MCR rules require the player to draw a replacement tile from the wall (gangshanghua). The current implementation does not issue this draw and the hand stalls — no next prompt, no event, the table sits idle until decide-timeout.

This is a rules-engine bug, not a UI issue. It is the highest priority of the seven items in this spec.

### Background

Per [Botzone MCR wiki](https://wiki.botzone.org.cn/index.php?title=Chinese-Standard-Mahjong/en) and [engine-api.md](engine-api.md):

- After a closed gang or added kong (BUGANG): the player draws a replacement tile (gangshanghua).
- The drawn replacement is itself eligible to be a winning tile (杠上花 / gang-on-flower).
- Before the replacement draw, any opponent holding the winning tile may claim qiang-gang-hu (robbing the kong); BUGANG is interruptible by HU, regular GANG is not.

The flow:

```text
PLAYER declares BUGANG (legal_actions includes it during their DISCARD-eligible turn)
  → engine applies the meld promotion (PENG → GANG with `is_added_kong=true`)
  → engine opens CLAIM_WINDOW for qiang-gang-hu (HU-only, no PENG/CHI)
  → resolve:
      if HU claimed → terminal (qiang-gang-hu fan)
      if all PASS → engine emits DRAW (is_replacement=true) from the dead-wall tail
        → if the drawn tile is a flower → emit FLOWER, then another replacement DRAW
        → engine emits PROMPT (DISCARD) to the same player
```

### Root cause — CONFIRMED 2026-06-01 (supersedes the speculation below)

The bug is **not** in `gang.py` and **not** in `manager.py`. Both were verified correct against `seed=42`:

- [gang.py:67-81 `_gang_added`](../../mahjong/engine/transition/gang.py#L67-L81) promotes the PENG → GANG_ADDED, removes the tile from concealed, and calls `internal_draw(new, seat)`. After the transition the state is back in `DISCARD` with the same `current_actor`, `last_drawn` updated to the freshly-drawn tile, and `concealed` count preserved (14 → 14). The replacement draw **does** happen at the engine level.
- The manager's main loop ([manager.py:254-278](../../mahjong/table/manager.py#L254-L278)) re-enters `_step_discard` for the same actor on the next iteration, so the manager *would* issue the follow-up DISCARD prompt.

The actual defect is in **[mahjong/records/diff.py:55-56](../../mahjong/records/diff.py#L55-L56)**. The `GANG` branch emits a single `_gang_event` (a `CLAIM_DECISION`-shaped event) and **never appends the replacement `DRAW` event** — unlike the `PLAY` branch, which calls `_maybe_append_window_or_draw` to surface the engine's auto-advance draw. Because the DRAW is dropped from `diff_to_events`, the wire stream and the on-disk record never report the replacement tile. The web client's local hand is left one tile short, so it cannot render the follow-up DISCARD prompt and the table *appears* stuck even though the server-side engine state is fine.

**Scope:** this affects **all three gang variants** (EXPOSED, CONCEALED, ADDED) — every one calls `internal_draw` and every one loses its DRAW event in `diff_to_events`. The user reported it via BUGANG-from-hand because that was the variant they hit first; the closed/exposed kongs have the same missing event (likely unnoticed because they occur less often in casual play). Verified via repro: `diff_to_events` for both CONCEALED and ADDED gangs returns only `[CLAIM_DECISION]`.

This is a **record-format / wire-contract** bug, not a rules-engine bug. It still belongs to Tier 1 because the record is the replay + training source of truth (per [record-format.md](record-format.md)): a record that omits a wall draw is non-replayable and would silently corrupt the training corpus.

### Interface (confirmed)

In [diff.py](../../mahjong/records/diff.py) `diff_to_events`, the `GANG` branch must append a `DRAW` event when the post-transition `wall.drawn_count` increased — mirroring `_maybe_append_window_or_draw`. Sketch:

```python
elif t == "GANG":
    events.append(_gang_event(state_before, state_after, seat, action, ts))
    # All three gang variants draw a replacement tile (gangshanghua) via
    # internal_draw. Surface it so the wire/record stay replayable.
    if state_after["wall"]["drawn_count"] > state_before["wall"]["drawn_count"]:
        events.append(_draw_event(state_after, ts))
```

The `_draw_event` constructor already reads `state_after["last_drawn"]` (the authoritative slot — see [[feedback_prefer_authoritative_state_over_derivation]]) and emits the standard DRAW shape (`seat`, `tile`, `flower_replacements`). Reuse it as-is — **do not** invent an `is_replacement` field: the record schema has no such field, and the web renderer already offsets the just-drawn tile from `last_drawn`, not from a per-event flag. The gangshanghua draw is structurally identical to a normal wall-front draw for replay purposes (same `drawn_count` advance, same tile recorded).

**Flower-on-replacement:** `internal_draw` ([transition/__init__.py:125](../../mahjong/engine/transition/__init__.py#L125)) already loops past flowers — it routes flower tiles into the seat's `flowers` list and continues to the next non-flower. The existing `_draw_event` hardcodes `flower_replacements: []` (a pre-existing gap on *all* DRAW events, not gang-specific), so the gang DRAW inherits the same behaviour. Leaving that consistent is in scope; populating `flower_replacements` from the diff is a separate, pre-existing record-completeness item and is **not** part of this fix.

### Out of scope for this fix (tracked separately)

- **Qiang-gang-hu (robbing the kong).** MCR allows an opponent holding the winning tile to HU off a BUGANG before the replacement draw. The current engine does **not** open a claim window for this — `_gang_added` draws immediately. This is a *rules-completeness gap*, not the stall, and is deferred to its own spec item (see Open questions). The stall fix above does not depend on it.

### Verification fixtures

[tests/records/test_diff.py](../../tests/records/test_diff.py) extension (the bug lives in the diff layer, so the pinning test goes there):

1. **added_gang_emits_replacement_draw** — seat has PENG of W1 melded + W1 in concealed; apply `GANG/ADDED`; assert `diff_to_events` returns `[CLAIM_DECISION(GANG, ADDED), DRAW(seat=actor, is_replacement=true)]` and the DRAW tile equals `state_after["last_drawn"]["tile"]`.
2. **concealed_gang_emits_replacement_draw** — 4-of-a-kind concealed; apply `GANG/CONCEALED`; assert the same DRAW is emitted.
3. **exposed_gang_emits_replacement_draw** — claim a discard into an exposed kong; assert the DRAW is emitted (and that the discarder's discard-pop event ordering is unchanged).
4. **gang_replacement_flower** — if the gangshanghua is a flower, assert the `FLOWER` event precedes the final `DRAW` (only if `internal_draw` surfaces flowers; otherwise document that flowers are consumed silently and drop this fixture).

Plus one **manager-level e2e** in [tests/table/](../../tests/table/) (the contract that actually broke for the user): drive a hand where a seat declares BUGANG, assert the record contains a `DRAW` event for the actor immediately after the `CLAIM_DECISION(GANG)` and that the next `PROMPT` is a DISCARD to the same seat — i.e. the table does not stall.

The seeded-rollout determinism fixture in [tests/determinism/](../../tests/determinism/) should be extended to cover at least one hand that involves a GANG so a future regression doesn't silently change the wall-consumption order or re-drop the DRAW event.

### Why Tier 1

Rule-engine correctness — the spec contract (BUGANG → replacement draw) is violated. Per [CLAUDE.md § Verification is the product](../../CLAUDE.md): "no learning claim without a verification artifact" applies to rules behaviour too. A BUGANG that stalls the table is worse than a BUGANG that silently mis-scores: the table becomes unplayable. Fix and test-pin before anything else in this spec.

---

## § 22.6 Table-creation options + claim-resolution window (Tier 2)

Two related but distinct asks: per-table bot pacing + decide-timeout knobs at creation time, AND a more principled multi-player claim resolution model.

### Part A — Table creation options

#### Goal

When a player creates a table via the lobby, they can choose:

- **Bot pacing speed.** Pre-canned ranges: "fast" (0.5–1.5s), "normal" (5–10s default), "slow" (15–30s), or custom min/max.
- **Hand decide-timeout.** Per-prompt deadline for human seats — currently fixed at 60s DISCARD / 20s CLAIM. Allow per-table override.
- **Hand time limit toggle.** Boolean: "play with timeouts on/off". When off, prompts have no deadline (the table will wait indefinitely for a human to act). Useful for casual play.

#### Interface

`CREATE_TABLE` wire message gains an optional `options` object:

```json
{
  "kind": "CREATE_TABLE",
  "ruleset": "mcr-2006",
  "seats": [{"kind": "human"}, ...],
  "options": {
    "bot_pacing": "normal",
    "decide_timeout_seconds": 60,
    "timeouts_enabled": true
  }
}
```

`bot_pacing` accepts the four pre-canned ranges or an object `{"min_s": 5.0, "max_s": 10.0}` for custom. `decide_timeout_seconds` is a single value applied uniformly to human DISCARD prompts; human CLAIM and bot timeouts stay at their server defaults (the per-prompt-kind matrix from Spec 19 stays, but the user override only sets one knob). `timeouts_enabled=false` disables the decide-timeout for human seats entirely — they have no deadline, and the existing strike/AutoPass takeover system does not fire on them.

Server-side: [TableHandle](../../mahjong/server/registry.py) gains a `decide_timeouts_override` arg that wins over the global server config. Validation: bot_pacing ranges must be `0 ≤ min ≤ max ≤ 60`; decide_timeout must be `5 ≤ n ≤ 600`; reject with `ERROR { code: "framing", message: ... }` otherwise.

#### Lobby UI

Below the existing "Humans: 1/2/3/4" picker, add a collapsed "Options" section with three controls:

```text
[ Options (advanced) ]   <- click to expand
  Bot pacing:  ( ) fast  (•) normal  ( ) slow  ( ) custom: [5.0]–[10.0] s
  Decide time: [60] seconds per discard prompt
  [×] Use time limits  (uncheck to disable timeouts on this table)
```

Selecting "custom" reveals the min/max inputs.

#### Non-goals

- Per-seat option overrides (e.g., "bob gets longer to decide than alice"). One config per table.
- Live mid-hand option changes. Options are set at creation and frozen.

#### Verification fixtures

- **create_table_with_pacing** — `CREATE_TABLE` with `options.bot_pacing="slow"`, assert the created table's TableHandle reports min=15.0, max=30.0.
- **create_table_with_custom_pacing** — `{"min_s": 1.0, "max_s": 2.0}`, assert override applied.
- **create_table_with_timeouts_disabled** — `timeouts_enabled=false`, run a hand with a human who never responds, assert hand sits indefinitely (no decide-timeout fires within a 30s test budget).
- **invalid_options_rejected** — `{"min_s": -1.0}` → framing error.
- **options_default_to_server_config** — `CREATE_TABLE` with no options → server defaults from [layer8-closeout.md § §2](layer8-closeout.md).

### Part B — Claim resolution window

#### Goal

Today's claim resolution is sequential: a DISCARD opens a CLAIM_WINDOW, and the first seat to send a winning claim wins. Under network jitter or human reaction time variance, this rewards the player with the best ping rather than the player with the best mahjong instincts. Two seats both holding a PENG-able tile both clicking PENG within 100ms of each other — the FCFS path picks one essentially arbitrarily.

The fix: introduce a **minimum claim window** during which the server **collects** claims from all seats, then resolves them in MCR priority order at the end of the window.

#### Background — MCR claim priority

Per [Botzone MCR wiki § Claim resolution](https://wiki.botzone.org.cn/) and the existing [layer 2 claim-priority memory](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer2_claim_priority_deferred.md):

1. **HU** (any seat, any direction relative to discarder) — wins over any other claim.
2. **PENG** / **GANG** (any seat) — wins over CHI.
3. **CHI** (next seat only) — lowest priority.
4. Among same-priority claims: seat order (counter-clockwise from discarder).

The current `_resolve_claim_priority` already enforces this for *simultaneously-arrived* claims. The problem is that today the window closes on the first claim received; a slightly-slower HU loses to a faster PENG.

#### Interface

[mahjong/table/manager.py](../../mahjong/table/manager.py) CLAIM_WINDOW handling:

- After a DISCARD, open a CLAIM_WINDOW of duration `claim_window_min_s` (default 1.5 s; per-table overridable).
- During the window: every seat may submit a claim (or PASS). The manager buffers them.
- The window closes when either:
  - All four seats have submitted (early close), OR
  - The `claim_window_min_s` elapses.
- After closure: `_resolve_claim_priority` picks the winner from buffered claims, fires the chosen claim's transition, all other claims are ignored.

This is **not** the current sequential model. It introduces a deliberate 1.5 s pause between DISCARD and the next player's draw — the cost of fairness.

#### Per-table override

The decide-timeout config gains `claim_window_min_s`. Defaults: server default 1.5 s, per-table override via `CREATE_TABLE.options.claim_window_min_s` (0 = sequential FCFS, restoring today's behaviour for tight CI tests).

#### Edge cases

- **Solo human + 3 bots.** Bots PASS instantly. The 1.5s wait per discard adds 1.5s × ~20 discards/hand ≈ 30s/hand of bot-vs-human idle time. Per § 22.5 bot-pacing, the bot DISCARD path is already paced 5–10s, so an additional 1.5s claim window is small relative. Acceptable.
- **All 4 humans.** This is the case the spec serves. 1.5s is short enough that humans don't feel the pause; long enough that simultaneous claims resolve fairly.
- **A seat doesn't respond within the window.** Their absence is interpreted as PASS. No strike (the strike system is for prompt timeouts; this is implicit-PASS, not a deadline miss).

#### Verification fixtures

- **simultaneous_peng_and_hu_hu_wins** — two seats submit during the window: HU at +0.5s, PENG at +0.3s. Resolution picks HU.
- **window_closes_early_on_all_pass** — all four seats PASS within +0.1s; resolution fires at +0.1s, no need to wait the full 1.5s.
- **late_submission_ignored** — a claim arriving at +1.6s (after window closes) is ignored; manager has already moved on.
- **per_table_zero_disables_window** — `claim_window_min_s=0` restores the existing FCFS path (smoke test for the override).

#### Why Tier 2

Touches the table manager's central claim path. Spec the change carefully; pin the resolution order with a deterministic fixture before changing the production path. Per [CLAUDE.md § TDD: hard for core](../../CLAUDE.md): table manager + claim resolution is in the *core* bucket — test-first is mandatory.

---

## § 22.7 Re-sort concealed hand after every mutation (Tier 3 — client reducer) — ✅ RESOLVED 2026-06-01

### Corrected diagnosis (the original premise below was wrong)

The original spec assumed the **engine** stops sorting after `initial_state`. That is false: `internal_draw` sorts `concealed` after **every** draw ([transition/__init__.py:141](../../mahjong/engine/transition/__init__.py#L141)), and the removal paths (DISCARD/CHI/PENG/GANG) use `.remove()`/value-removal, which preserve a sorted list. The engine hand is always canonical.

The drift the user saw came from the **client reducer**: [apply_event.js](../../mahjong/web/static/apply_event.js) deliberately appended drawn tiles to the tail and never re-sorted (an explicit scope decision in its header comment). After a few draw-then-keep turns, the tail of the *client's local* hand was a jumble, which is what broke the suit-break logic.

### Why the fix moved to the reducer, not the renderer

The original interface (sort inside `renderOwnConcealedTiles`) would **break tile selection**. The selection cursor `selectedTile` is an index into the reducer's concealed array ([app.js `_ownConcealedTiles`](../../mahjong/web/static/app.js)), and both arrow-key nav and digit-key selection (`actionForKey`) resolve the chosen tile by that same raw index. Sorting only the *display* would leave digit "3" highlighting whatever sits at raw index 3 — not the third visible tile. Sorting in the reducer keeps the array, the cursor, and the display all in one order.

The just-drawn "sits apart" cue is unaffected: the renderer pulls `view.last_drawn` out to the end by **value**, not array position, so it still offsets correctly over a fully-sorted array.

### Fix landed

[apply_event.js](../../mahjong/web/static/apply_event.js): added `_tileSortKey` (mirrors [engine/tiles.py `tile_sort_key`](../../mahjong/engine/tiles.py), sections W<B<T<F<J<H) + `sortOwnConcealed`, called in `applyDraw` right after the drawn tile is appended. Opponent concealed (a count) is untouched. Pinned by [tests/web/test_reducer_sort.py](../../tests/web/test_reducer_sort.py) (`test_draw_resorts_own_concealed_into_canonical_order`, `test_just_drawn_tile_is_sorted_in_array_but_offset_by_renderer`).

### Worked example

Client local hand has drifted to `[W2, W3, B5, T7, W9, B1]` (W9/B1 stranded at the tail from earlier draws). Next DRAW of `T2` → reducer re-sorts → `[W2, W3, W9, B1, B5, T2, T7]`. The renderer then offsets `T2` (last_drawn) to the end; suit-breaks fire on B1 (W→B) and T2 (B→T).

### Non-goals

- **Engine-side change.** None needed — the engine is already canonical.
- **Renderer-side sort.** Rejected: breaks selection-cursor indexing (see above).

---

## § 22.9 Hand-end scoring summary (Tier 3 — renderer)

### Goal

When a hand ends — HU (someone wins) or a draw (exhausted wall) — the client shows nothing beyond the per-seat score in each block header and a neutral `·` on the pinwheel. The player gets no summary of *what happened*: who won, off which tile, the fan breakdown (which patterns scored and for how much), the total fan, and the per-seat point swing. Add an end-of-hand summary panel.

### Background — the data is already on the wire

No wire or reducer change is needed. [apply_event.js](../../mahjong/web/static/apply_event.js) `applyHandEnd` already populates `view.terminal` from the `HAND_END` event with everything required:

```js
view.terminal = {
  kind,            // "HU" | "DRAW" (exhausted) | ...
  winner,          // seat index, or null on a draw
  win_tile,        // the winning tile token
  win_type,        // "SELF_DRAW" | "DISCARD"
  deal_in_seat,    // who dealt the winning tile (null on self-draw / draw)
  fan,             // [{ name, points, count? }, ...] — the scored patterns
  fan_total,       // total fan
  score_delta,     // [d0, d1, d2, d3] per-seat point change
};
```

`score_delta` is also already applied to each seat's running `score`. The gap is purely that [render.js](../../mahjong/web/static/render.js) renders nothing for `phase === "TERMINAL"`.

### Interface

[render.js](../../mahjong/web/static/render.js): add `renderHandEndSummary(view, ownSeat)` that returns a panel when `view.terminal != null`, wired into `renderTable` (or surfaced as a sibling block in the game pane). Contents:

- **Headline.** HU: `"{seat wind} ({You|Seat N}) wins"` + win-type (`"self-draw"` / `"on {discarder wind}'s discard"`) + the winning tile glyph. Draw: `"Exhausted draw — no winner"`.
- **Fan breakdown.** One row per `fan[]` entry: pattern name + its point value (× count when an entry repeats). MCR fan names come straight from the engine's scorer (per [engine-api.md](engine-api.md) / the Botzone 81-fan table — do not re-translate names client-side; render what the event carries).
- **Total.** `fan_total` (must be ≥ 8 for a legal MCR win).
- **Point swing.** The four `score_delta` values, labelled by seat, with the winner highlighted.

Styling lives in the `<game-pane>` shadow CSS ([app.js](../../mahjong/web/static/app.js)), consistent with §22.3/§22.4. Use `--accent` for the winner row, `--accent-red`/`--fg-dim` for payers.

### Non-goals

- **Match-level / cumulative scoreboard across hands.** This panel is per-hand. A running multi-hand scoreboard is a separate feature (likely Layer 9, tied to the multi-hand match flow).
- **Replaying the winning hand tile-by-tile.** Out of scope — show the final melds + winning tile, not an animation.
- **"Next hand" button wiring.** The summary is display-only; hand advancement is the existing `START_HAND` / orchestrator path.

### Verification fixtures

[tests/web/test_hand_display.py](../../tests/web/test_hand_display.py) (or a new `test_hand_end_summary.py`):

- **hu_summary_shows_winner_and_fan** — feed a `view.terminal` with `kind=HU`, a known winner, two fan entries, `fan_total`; render; assert the panel shows the winner, both fan names + points, and the total.
- **self_draw_vs_discard_headline** — `win_type=SELF_DRAW` → "self-draw"; `win_type=DISCARD` + `deal_in_seat` → "on {wind}'s discard".
- **draw_summary_shows_no_winner** — `kind=DRAW`, `winner=null` → "exhausted draw" headline, no fan rows.
- **score_delta_rendered_per_seat** — assert each seat's delta appears and the winner's row carries the accent/highlight class.
- **no_summary_before_terminal** — `view.terminal == null` (mid-hand) → panel absent.

Mount the real `<game-pane>` for any computed-style assertions (the §22.3/§22.4 pattern), bare-div render for structure-only checks.

### Why Tier 3

Pure presentation over data the reducer already holds; no wire/engine/manager change. Low blast radius, high player-facing value — currently a win just silently bumps the score with no explanation of the fan, which is exactly the part of MCR a learning player most needs to see.

---

## § 22.8 Outstanding work from layer8-closeout.md

For completeness — this spec does NOT close out the following items from [layer8-closeout.md](layer8-closeout.md):

- **§ 5 Lifecycle hardening.** SIGTERM drain is wired; `/health` endpoint, startup integrity check, and structured seat-hold logging remain. Linux-deploy-adjacent — fold into the deploy phase rather than the play-polish phase.
- **§ 6 `abort_hand` cleanup on engine exception.** Persistence method doesn't exist; left deferred until the first crash-mid-hand failure mode actually appears in production.

---

## Cross-cutting: suggested implementation order

By "smallest blast radius first" with one exception: § 22.5 (BUGANG) is biggest by lines-of-code but unblocks live play with kongs, so it goes first.

1. ✅ **§ 22.5 BUGANG replacement draw** — DONE 2026-06-01 (diff-layer DRAW emission, all 3 gang variants).
2. ✅ **§ 22.1 Claim-window arrow leak** — DONE 2026-06-01.
3. ✅ **§ 22.7 Hand re-sort** — DONE 2026-06-01 (reducer, not renderer).
4. ✅ **§ 22.3 Selection highlight under Unicode** — DONE 2026-06-01 (`color-mix` tint).
5. ✅ **§ 22.4 Discard tile size** — DONE 2026-06-01.
6. **§ 22.2 Claimable alerts (visual)** — CSS keyframe + chip + fixtures. 1–2 hours. Sound deferred. *(in progress)*
7. **§ 22.9 Hand-end scoring summary** — renderer-only; data already in `view.terminal`. ~1–2 hours.
8. **§ 22.6 Table-creation options + claim window** — bigger work. Part A (options) is ~1 day; Part B (claim window) is a careful refactor of the table manager — TDD-mandatory per the working agreement. 1–2 days.

**Gate to close Layer 8:** items 1–7 done + browser-verified. § 22.6 Part B can land separately as the first piece of Layer 9 if it gets too large for the close-out window.

## Open questions

- **§ 22.6 Part B default window length.** 1.5s is a guess. After two-tab play with the window enabled, we should pick the value that feels right rather than the one the spec defaulted to.
- **§ 22.2 sound — when?** If we have a target session for adding sound, the audio asset + Web Audio plumbing is small (~1 hour). Just needs to be scheduled.
