# Spec 18 — Cardinal-direction table renderer

Replace the current vertically-stacked seat layout (own seat at bottom, opponents stacked above) with a 4-cell cardinal layout that mirrors how mahjong is played at a physical table: you sit at the south edge, the three opponents occupy the west / north / east cells, and the table center carries the last-discarded tile plus an arrow pointing at the current actor.

Tier-3 spec. Single consumer: the web client renderer ([mahjong/web/static/render.js](../../mahjong/web/static/render.js)). No wire-protocol change — the existing `SeatView` projection has every field this layout needs. Builds on [state-schema.md](state-schema.md) (`current_actor`, `last_discard`, `seats[]`) and is informed by the user-driven request 2026-05-26 ("a diagram of all four players arranged in a circle aligned with the cardinal directions with an arrow radiating from the center indicating whose turn it currently is, along with a tile depiction of the last tile discarded").

## Goals

- **Spatial layout matches physical mahjong.** The four seats render as four blocks arranged on the cardinal axes (south, west, north, east), with the local player's seat at south. The next player to act after the local player is on the east (right) edge; play proceeds counter-clockwise (E → S → W → N) so east → north → west → south matches the engine's `(actor + 1) % 4` advance.
- **Turn at a glance.** A single visual indicator — an arrow rendered from the table center to the current actor's edge — answers "whose turn is it?" without the user reading the metadata strip. The arrow points down to the local seat when it's their turn.
- **Last discard front and center.** The most recent discarded tile is rendered as a real tile glyph in the center cell of the layout, alongside a short caption identifying who discarded it. This is the same `last_discard` field already in [state-schema.md § SeatView](state-schema.md), surfaced visually instead of as a sentence on the metadata strip.
- **Preserve seat-block content.** Each seat block keeps the same fields the current renderer shows (own-seat: full concealed list + melds + flowers + discards; opponent: face-down count + melds + flowers + discards). The only thing that changes is the *positioning* of those blocks, not their content.
- **No regressions on existing display conventions.** Rank-first tile shorthand, suit colors, dragon glyphs, ASCII vs Unicode toggle, face-down rendering — all unchanged. See [render.js header comment](../../mahjong/web/static/render.js#L8-L28) for the locked conventions.

## Non-goals

- **Real "circular" geometry.** A 3 × 3 CSS grid is enough; the four blocks occupy the four edge cells. Rotating individual blocks to face their cardinal direction (so opponent text reads sideways) is a visual flourish, not a goal — readability wins.
- **Animated arrow.** The arrow is static text/SVG; we don't tween it between actors on each turn.
- **Reshuffling between hands.** The local player is always at south; we do not rotate the cardinal layout to track the dealer.  Dealer indication is already on the metadata strip and on the seat block itself.
- **Spectator-mode reskin.** Spectators currently see no per-seat projection at all (concealed lists are empty in the public projection per [state-schema.md § Public projection](state-schema.md)); this spec is for the per-seat consumer.  A spectator-tailored layout is a future enhancement.

## The layout

The renderer in `renderTable(seatView, ownSeat, options)` switches from a `<pre>` stack to a CSS grid.  Three sections survive: the cardinal grid replaces the current "three opponents + last discard" + "you" sections; the bottom metadata strip (Round / Hand / Turn / Wall / Phase / Dealer / Active) carries over unchanged.

```
┌──────────────┬────────────────────┬──────────────┐
│              │  North seat block  │              │
│              │  (across)          │              │
├──────────────┼────────────────────┼──────────────┤
│ West seat    │   ┌─ center ─┐     │  East seat   │
│ block (left, │   │  ← arrow │     │  block       │
│ previous     │   │  [tile]  │     │  (right,     │
│ actor)       │   │  caption │     │  next actor) │
│              │   └──────────┘     │              │
├──────────────┼────────────────────┼──────────────┤
│              │  South seat block  │              │
│              │  (YOU)             │              │
└──────────────┴────────────────────┴──────────────┘

                  Round / Hand / Turn / Wall / Phase / Dealer / Active
```

### Seat-to-cell mapping

Counter-clockwise play order is `E → S → W → N` (per [state-schema.md § Per-seat projection](state-schema.md) seat-wind rotation).  From the local player's point of view:

| Cell    | Engine seat                  | Why                                                                  |
| ------- | ---------------------------- | -------------------------------------------------------------------- |
| south   | `ownSeat`                    | local player                                                         |
| east    | `(ownSeat + 1) % 4`          | next to act after you (CCW play order from your seat is rightward)   |
| north   | `(ownSeat + 2) % 4`          | across from you (two seats away in either direction)                 |
| west    | `(ownSeat + 3) % 4`          | previous actor (CCW from your seat ends to your left)                |

This is the same mapping the current `seatPositions(view, ownSeat)` produces; we are renaming `top → north`, `middle → north` (same cell), `just_above → west`, `bottom → south` and *also* breaking `north / west` apart in the actual grid instead of stacking them.

### Center cell — last discard + turn arrow

The center cell renders two stacked elements:

1. **Last-discarded tile** — a real tile glyph rendered through the existing `tile(...)` helper (`mahjong/web/static/render.js`), in whichever style (`ascii` / `unicode`) the user has selected.  Below the glyph, a caption: *"discarded by East (Seat 3) · turn 14"*.  When there is no last discard (between hands, or before the first discard), the cell shows a dim `(no discards yet)` placeholder.  The data source is `seatView.last_discard = {seat, tile, turn_index}`.
2. **Turn arrow** — a single arrow character positioned within the center cell to point at the current actor's edge.  Source: `seatView.current_actor`.

Arrow direction:

| `current_actor` (relative to `ownSeat`) | Cardinal target | Arrow glyph |
| --------------------------------------- | --------------- | ----------- |
| `== ownSeat`                            | south (you)     | `↓`         |
| `== (ownSeat + 1) % 4`                  | east            | `→`         |
| `== (ownSeat + 2) % 4`                  | north           | `↑`         |
| `== (ownSeat + 3) % 4`                  | west            | `←`         |

When the current phase is `CLAIM_WINDOW` (multiple seats may be deciding in parallel), the arrow is replaced by `?` and the caption reads `"claim window — N decisions pending"`.  Terminal phase (`HAND_END` rendered as `phase: TERMINAL`): no arrow; caption reads `"hand over — see results"`.

### Per-seat block

The existing `seatBlock` function continues to produce one Lit fragment per seat.  No changes to `renderOwn` / `renderOpponent` content.  The grid wraps each block in a `<div class="cell cell-{cardinal}">` so CSS can pad / align the four cells independently (e.g., the north block stretches horizontally to fill the top row).

Active-actor highlight: the seat block whose `seat === current_actor` gets an extra `.active` class so its border picks up `var(--accent-red)` (the same color the existing table header uses).  This is redundant with the arrow but pins down "who's playing" when the arrow is replaced (claim window / terminal).

## Implementation sketch

```js
// render.js (excerpt)

const ARROW_FOR_RELATIVE = { 0: "↓", 1: "→", 2: "↑", 3: "←" };

function renderCenterCell(view, ownSeat, options) {
  const arrow = _arrowToActor(view, ownSeat);
  const ld = view.last_discard;
  const tileBlock = ld
    ? html`${tile(ld.tile, options)}
        <div class="caption">
          ${fullSeatName(view, ld.seat)} · turn ${ld.turn_index}
        </div>`
    : html`<div class="caption dim">(no discards yet)</div>`;
  return html`<div class="cell cell-center">
    <div class="arrow">${arrow}</div>
    ${tileBlock}
  </div>`;
}

function _arrowToActor(view, ownSeat) {
  if (view.phase === "CLAIM_WINDOW") return "?";
  if (view.phase === "TERMINAL") return "";
  const relative = ((view.current_actor ?? ownSeat) - ownSeat + 4) % 4;
  return ARROW_FOR_RELATIVE[relative];
}
```

The `<game-pane>` styles add a 3 × 3 grid; the metadata strip stays a separate trailing `<pre class="section">`.

## Worked example

A 2H + 2B table where the local player is alice (seat 0, dealer = East).  Hand is in DISCARD phase; bob (seat 1, South) is the current actor; the last discard was alice's `T5` on turn 7.

```
┌─────────────────┬──────────────────────┬─────────────────┐
│                 │  North · Seat 3      │                 │
│                 │  canned-pass         │                 │
│                 │  ?? ?? ?? ?? (13)    │                 │
│                 │  Discards: B1 W4     │                 │
├─────────────────┼──────────────────────┼─────────────────┤
│ West · Seat 2   │       →              │ East · Seat 1   │
│ canned-pass     │     ┌──┐             │ guest2 (bob)    │
│ ?? ?? ?? (13)   │     │T5│             │ ?? ?? ?? (14)   │
│ Discards: W7    │     └──┘             │ Discards: F2    │
│                 │  alice · turn 7      │                 │
├─────────────────┼──────────────────────┼─────────────────┤
│                 │  South · Seat 0      │                 │
│                 │  guest1 (alice) — YOU│                 │
│                 │  W1 W3 W5 B2 ... (13)│                 │
│                 │  Discards: T3 T5     │                 │
└─────────────────┴──────────────────────┴─────────────────┘
Round: East   Hand: 1   Turn: 7   Wall: 73 left
Phase: Discard   Dealer: East (Seat 1)   Active: East (Seat 2)
```

The East cell (bob's seat) gets the `.active` highlight; the arrow in the center points east `→`; the tile glyph in the center shows `T5` colored as bamboo (or its Unicode glyph in unicode-tile mode); the caption credits alice (the seat that played T5).

## Alternatives considered

- **Keep the stacked layout, just add the arrow.**  Considered; rejected.  The user's "ah-ha" was that the spatial layout matters — a turn arrow on a vertical stack is far less legible than the same arrow on a cardinal grid that already encodes who is to your right / left / across.  We get both wins by changing layout once.
- **Rotate opponent blocks to face their cardinal direction (west block reads top-to-bottom, etc.).**  Rejected: cute but degrades readability for English-language UI tokens (`?? ??`, `Discards: ...`, seat names).  Layout-only is enough.
- **Animate the arrow.**  Rejected for v1 — adds tweening machinery without adding information.  Static arrow updates on each `current_actor` change are sufficient.
- **Use SVG for the arrow.**  Considered; rejected.  A single Unicode arrow character positioned with CSS is simpler, themeable, and avoids vector-graphics dependencies in a no-build client.

## Verification fixtures

Each fixture below is a single failing test before implementation (jsdom + Lit's `render` are already in scope per [test_e2e_s2.py](../../tests/web/test_e2e_s2.py) ancestors, but a fresh `tests/web/test_cardinal_render.js`-style suite under pytest's web tests is the right home).

1. **Seat-to-cell mapping respects `ownSeat`.**  With `ownSeat = 2`, the south cell renders seat 2; east is seat 3; north is seat 0; west is seat 1.  Pinned via DOM query: each cell's `.cell-south .seat-block` carries the seat-2 user_id (or "YOU" badge).
2. **Arrow direction tracks `current_actor`.**  For each value of `current_actor` (0..3) with `ownSeat = 0`, the arrow glyph matches the table above.  Verify by querying the center cell's `.arrow` text content.
3. **Claim-window arrow is `?`.**  With `phase = "CLAIM_WINDOW"`, the center cell's arrow text is exactly `?` (or rendered absent).  Caption shows "claim window".
4. **Terminal phase has no arrow.**  With `phase = "TERMINAL"`, no arrow glyph in the center cell; caption shows "hand over".
5. **Last-discard tile glyph.**  With `last_discard = {seat: 1, tile: "T5", turn_index: 7}`, the center cell contains a `.tile` element whose data attribute or text matches T5 (under ASCII and Unicode tile-style options).  Caption credits seat 1.
6. **No last-discard placeholder.**  With `last_discard = null`, center cell shows the dim "(no discards yet)" string and no `.tile` element.
7. **Active highlight follows `current_actor`.**  With `current_actor = 1` and `ownSeat = 0`, the east cell has `.active` class; the other three cells do not.
8. **Ownseat full hand renders only in the south cell.**  For `ownSeat = 0`, the south cell shows the seat-0 concealed *list* (full tiles); the other cells render face-down counts even if the engine state happened to leak (defense-in-depth — should never happen given [state-schema.md § Per-seat projection](state-schema.md), but the renderer should not surface a list it accidentally received).

## Open questions

- **How wide should the side cells be vs. the center cell?**  Working answer: equal-width thirds, with the center cell padded so its content (tile glyph + arrow + caption) is centered.  Re-evaluate after the first render — narrow viewports may want the center cell to consume less horizontal space.
- **Should the arrow shift to match wind labels instead of cardinal directions (i.e., point at the current actor's *wind*, not their *seat*)?**  Working answer: no — winds rotate between hands, but the on-screen layout doesn't.  Pointing at the seat is what the user asked for.
- **Mid-hand claim-window UI.**  When this player is one of N claim-window participants, do we also show a per-cell "deciding" badge?  Working answer: yes — add `.deciding` class to any seat with a `pending_claims` entry for that seat in the local seat view.  Stretch goal; not in the load-bearing fixtures above.

## Cross-spec impact

- [state-schema.md](state-schema.md) — no change.  Every field the cardinal renderer needs is already projected (`seats[i]`, `current_actor`, `last_discard`, `phase`).
- [wire-protocol.md](wire-protocol.md) — no change.
- [tui-client.md](tui-client.md) — superseded for the web client.  The TUI spec is now historical; the web ASCII client (Lit + render.js) is the live consumer.  Cardinal layout is web-only.
- [render.js header comment](../../mahjong/web/static/render.js) — locked seat-positioning convention ("own seat at bottom, right opponent at top, across in the middle, left opponent just above you") becomes a *2D* convention; update the comment.
