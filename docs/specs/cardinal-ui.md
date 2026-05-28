# Spec 18 — Cardinal pinwheel widget

Add a small compact "who's-playing + last-discard" pinwheel widget that sits next to the existing stacked seat layout. The pinwheel mirrors how mahjong is played at a physical table (you at south, next-to-act on your right at east, across at north, previous actor on your left at west) and carries the turn arrow plus a miniature glyph of the most recently discarded tile.

**Design pivot (2026-05-27).** The original Step 8.9 spec replaced the vertically-stacked seat blocks with a full 3×3 cardinal grid. Manual two-human play showed the grid broke the seat-block formatting — opponents' hand rows, melds, and discards are wide ASCII content that doesn't fit gracefully into a third-of-pane cell. The revised design separates two concerns: the seat blocks stay stacked (their existing wide layout works), and a small *separate* pinwheel widget answers "who just discarded" and "what tile" at a glance.

**Second revision (2026-05-27, same session).** Initial pinwheel implementation badged seats by relative position (`E4`, `N1`, `W2`, `YOU`). User correction: badge each seat by its **wind number** (MCR convention — East=1, South=2, West=3, North=4) so the dealer is always "1" and the play-order rotation is readable at a glance. The arrow follows the **last discarder**, not the current actor, and the center tile is rendered with the unicode glyph at large size.

Tier-3 spec. Single consumer: the web client renderer ([mahjong/web/static/render.js](../../mahjong/web/static/render.js)). No wire-protocol change — the existing `SeatView` projection has every field this widget needs. Builds on [state-schema.md](state-schema.md) (`current_actor`, `last_discard`, `seats[]`) and is informed by the user-driven request 2026-05-26 ("a diagram of all four players arranged in a circle aligned with the cardinal directions with an arrow radiating from the center indicating whose turn it currently is, along with a tile depiction of the last tile discarded"), revised 2026-05-27 ("the players hands are arranged in a clock wise pattern which breaks the formatting … just a small separate pinwheel, perhaps only 5x5 characters wide").

## Goals

- **Cardinal spatial cue without breaking the stack.** A small compact pinwheel (~5×5 chars + one badge per edge) sits next to the existing stacked seat blocks. The pinwheel encodes "you at south, neighbour to your right (east), across (north), to your left (west)" without consuming the horizontal space the wide opponent rows need.
- **Wind numbers, not relative positions.** Each badge shows the seat's per-hand wind number: East=1, South=2, West=3, North=4. The dealer is always "1" and the numbers increase counter-clockwise (mahjong play order). This makes "who's the dealer right now?" and "where am I in the rotation?" readable at a glance, independent of which physical seat the viewer happens to sit in.
- **Large unicode tile front and centre.** The last-discarded tile is the pinwheel's visual anchor. It is rendered with the unicode mahjong glyph (U+1F000–U+1F029) at a large font size, so a player can read it across the room. The local tile-style toggle (`ascii` vs `unicode`) does *not* apply inside the pinwheel — it forces unicode.
- **Arrow points at the last discarder.** The arrow in the center cell points at the cardinal edge of whichever seat just played the tile shown — same source of truth as the tile itself. Before the first discard (or between hands) the arrow is a neutral `·`; during a `CLAIM_WINDOW` it is `?`.
- **Stacked seat blocks unchanged.** `renderTable(seatView, ownSeat, options)` still produces the same `<pre class="section">` blocks it has produced since 7.5c.i. No regressions on suit colors, dragon glyphs, ASCII vs Unicode toggle for the stacked layout, face-down rendering, or the dealer / wind metadata strip.

## Non-goals

- **Full cardinal grid for seat content.** Attempted 2026-05-27, rolled back — the wide ASCII opponent rows wrapped clockwise inside narrow grid cells and the result was confusing. The pinwheel is the cardinal map; the seat blocks stay stacked.
- **Animated arrow.** Static text glyph; re-renders only when `current_actor` changes.
- **Reshuffling between hands.** The local player is always at south; the pinwheel does not rotate to track the dealer. Dealer indication stays on the metadata strip.
- **Spectator-mode reskin.** Spectators currently see the public projection (concealed lists empty); a spectator-tailored pinwheel is a future enhancement.

## The widget

`renderPinwheel(view, ownSeat, options) -> Lit fragment` is exported from [render.js](../../mahjong/web/static/render.js) alongside the existing `renderTable`. `<game-pane>` mounts it inside the existing `.table-ascii` wrapper, absolutely positioned in the top-right via `.pinwheel-wrap { position: relative }` + `.pinwheel { position: absolute; top: 0; right: 0 }`. The pinwheel does not nest inside `<pre>`; it owns its own div so its grid layout works.

```text
       ┌───────────────┐
       │       4       │      <- north badge: wind number (4 = North)
       │ 3    ↓   1    │      <- west / center (arrow + tile) / east badges
       │     🀝         │      <- last-discard tile in unicode, large
       │       2       │      <- south badge (YOU, underlined/accented)
       └───────────────┘
```

The center cell stacks the arrow above the large unicode last-discard tile. The four edge cells render a single-digit seat badge — the per-hand wind number of the seat occupying that cardinal position. The south badge additionally carries an `.own` class so CSS can underline / accent it without changing the badge text (the local player can always be found at the bottom of the pinwheel).

### Seat-to-edge mapping (spatial)

Counter-clockwise play order is `E → S → W → N` (per [state-schema.md § Per-seat projection](state-schema.md) seat-wind rotation). The pinwheel positions are determined by spatial offset from `ownSeat` — YOU at south, neighbours radiating outward:

| Edge  | Engine seat         | Why                                                                |
| ----- | ------------------- | ------------------------------------------------------------------ |
| south | `ownSeat`           | local player (badge carries `.own` class)                          |
| east  | `(ownSeat + 1) % 4` | next to act after you (CCW play order from your seat is rightward) |
| north | `(ownSeat + 2) % 4` | across from you                                                    |
| west  | `(ownSeat + 3) % 4` | previous actor                                                     |

### Badge content (wind number)

The badge *content* is independent of spatial position — each seat shows its per-hand wind number. With `dealer_seat = D`, seat `S`'s wind is `F((S - D) mod 4 + 1)`, and the badge is:

| `seat.seat_wind` | Wind   | Badge |
| ---------------- | ------ | ----- |
| `F1`             | East   | `1`   |
| `F2`             | South  | `2`   |
| `F3`             | West   | `3`   |
| `F4`             | North  | `4`   |

So the dealer's badge is always `1`, and the badges go up counter-clockwise around the table.

### Arrow direction

The arrow points at whichever cardinal position holds `last_discard.seat` — same source of truth as the tile it sits above.

| `last_discard.seat` (relative to `ownSeat`) | Cardinal target | Arrow glyph |
| ------------------------------------------- | --------------- | ----------- |
| `== ownSeat`                                | south (you)     | `↓`         |
| `== (ownSeat + 1) % 4`                      | east            | `→`         |
| `== (ownSeat + 2) % 4`                      | north           | `↑`         |
| `== (ownSeat + 3) % 4`                      | west            | `←`         |

Before the first discard of a hand (`last_discard == null`): arrow is a neutral `·`. During a `CLAIM_WINDOW`: arrow is `?` (multiple seats may be deciding in parallel). At `TERMINAL`: arrow is `·` (no actor pending).

### Active highlight

The badge whose seat equals `last_discard.seat` gets a `.active` class so its colour picks up `var(--accent-red)` (the existing table-active accent). The active badge and the arrow share the same source — they point at the same seat — so the viewer sees a coherent "this seat just played that tile" signal.

## Implementation sketch

```js
// render.js (excerpt)

const WIND_TO_NUMBER = { F1: 1, F2: 2, F3: 3, F4: 4 };
const PINWHEEL_ARROW = { 0: "↓", 1: "→", 2: "↑", 3: "←" };

function _seatBadge(seat) {
  if (!seat) return "?";
  return String(WIND_TO_NUMBER[seat.seat_wind] ?? "?");
}

function _pinwheelArrow(view, ownSeat) {
  if (view.phase === "CLAIM_WINDOW") return "?";
  if (view.phase === "TERMINAL") return "·";
  const ld = view.last_discard;
  if (!ld || ld.seat == null) return "·";
  const relative = (((ld.seat - ownSeat) % 4) + 4) % 4;
  return PINWHEEL_ARROW[relative] ?? "·";
}

export function renderPinwheel(view, ownSeat, options = {}) {
  // 3×3 grid: corners empty; north / east / south / west carry wind-number
  // badges; center carries arrow + LARGE unicode last-discard tile.
  // tileStyle is forced to "unicode" regardless of caller options.
}
```

The `<game-pane>` styles add a small absolute-positioned `.pinwheel` block; the center tile font-size is bumped to ~2.6em so the unicode glyph dominates the widget.

## Worked example

Local player is alice (seat 0, dealer this hand → wind East / badge `1`). bob is seat 1 (wind South / badge `2`). Hand is in DISCARD phase; alice just discarded a `T5` on turn 6, the engine has advanced `current_actor` to bob for his draw / discard, and `last_discard = {seat: 0, tile: "T5", turn_index: 6}`. The pinwheel — top-right of the game pane next to the stacked opponents block — reads:

```text
       4         <- north badge (seat 2 = West wind would be 3 here; with dealer=0 the across seat is seat 2 = wind 3, so N badge = 3.  See below for the worked numbers.)
3       2        <- west / east badges (seat 3 = North = 4 on left; seat 1 = South = 2 on right)
       🀝         <- last-discard tile (unicode bamboo-5), large
       1         <- south badge: YOU (underlined / accented because of .own class), wind East
```

With `ownSeat = 0`, `dealer_seat = 0`:

- south = seat 0, wind F1 East → badge `1` (carries `.own`, also `.active` because seat 0 is the last discarder).
- east  = seat 1, wind F2 South → badge `2`.
- north = seat 2, wind F3 West → badge `3`.
- west  = seat 3, wind F4 North → badge `4`.

Arrow: `last_discard.seat == 0 == ownSeat` → relative 0 → `↓` (pointing down at alice's own south badge). Center tile: `T5` rendered as the unicode mahjong glyph 🀔 at ~2.6em font size. The stacked seat layout below the pinwheel is unchanged — wide opponent rows, full own concealed list, and the existing `Last discard: …` caption above the metadata strip.

## Alternatives considered

- **Full cardinal grid replacing the stack.** Attempted 2026-05-27; rolled back. Opponent rows are wide ASCII content that wrapped clockwise inside narrow cells, producing a worse layout than the stack it replaced. The pinwheel-plus-stack split keeps the spatial cue without paying that cost.
- **Badge content = relative position (`E4`, `N1`, `W2`, `YOU`).** First attempt; superseded the same session. Wind-number badges (`1`/`2`/`3`/`4`) communicate "who is the dealer" and "where am I in the rotation" — facts that don't change with the viewer's seat — whereas relative-position labels just restated information the spatial layout already conveyed.
- **Arrow follows `current_actor`.** First attempt; superseded. The arrow + tile share a single data source (`last_discard`) so a viewer can read "this seat played this tile" in one motion. `current_actor` answers the different question "whose turn is it now," and that lives on the metadata strip.
- **Pinwheel as a header strip across the top.** Considered; rejected. The widget is small enough that absolute-positioning into the top-right corner of the existing table area uses zero new vertical space.
- **Show the full "discarded by X · turn Y" caption inside the pinwheel.** Rejected to keep the widget ~5×5. The caption already exists on the stacked layout's last-discard line.
- **Respect the caller's `tileStyle` option inside the pinwheel.** Rejected. The pinwheel exists to be readable at a glance, and the unicode mahjong glyphs are more legible at the large size than the rank+suit ASCII shorthand. The stacked layout still honors the user's ASCII/unicode toggle.
- **Animate the arrow.** Rejected for v1 — adds tweening machinery without adding information.
- **SVG arrow.** Rejected. A single Unicode glyph positioned with CSS is themeable and avoids vector-graphics dependencies in the no-build client.

## Verification fixtures

Tests live in `tests/web/test_cardinal_render.py`. Each fixture dynamically imports `renderPinwheel` from `/static/render.js`, renders into a disposable div, and asserts on the DOM via Playwright.

1. **Badges are wind numbers, not position labels.** With `ownSeat = 2` and `dealer_seat = 0`: south = `3` (own seat is West), east = `4` (seat 3 = North), north = `1` (seat 0 = East), west = `2` (seat 1 = South).
   - **1b.** For every `ownSeat ∈ {0, 1, 2, 3}` the four badges are exactly `{1, 2, 3, 4}` — the load-bearing "every hand has one East, one South, one West, one North" invariant.
2. **Arrow points at the last discarder.** For each `last_discard.seat` ∈ {0, 1, 2, 3} with `ownSeat = 0`, the `.pw-arrow` text matches `{↓, →, ↑, ←}`. `current_actor` is deliberately set to a *different* seat to prove the arrow does not follow it.
3. **Claim-window arrow is `?`.** With `phase = "CLAIM_WINDOW"`, the arrow is exactly `?`, even when a `last_discard` is present.
4. **Terminal phase shows a neutral marker.** With `phase = "TERMINAL"`, the arrow text is not in `{↑, ↓, ←, →, ?}` — a static `·`.
5. **Last-discard tile renders as a unicode glyph.** With `last_discard = {seat: 1, tile: "T5", turn_index: 7}` and `options.tileStyle = "ascii"`, the rendered `.tile` text is the unicode bamboo-5 codepoint `U+1F014`, not the ASCII shorthand. The pinwheel forces unicode.
6. **No last-discard placeholder.** With `last_discard = null`, the center cell carries `.pw-empty` and no `.tile` element.
7. **Active highlight follows the discarder.** With `last_discard.seat = 1` and `ownSeat = 0`, the east badge has `.active`; south, north, west do not.
8. **South badge carries `.own`.** For every `ownSeat ∈ {0, 1, 2, 3}`, the south-position badge has the `.own` class; the other three do not. The badge text is the seat's wind number (so the underlying value still varies), but the `.own` class pins "this is the local player's seat" for CSS.

## Open questions

- **Pinwheel positioning under narrow viewports.** Working answer: absolute top-right of `.table-ascii`. Re-evaluate if a future side-pane layout makes the absolute corner ambiguous.
- **Should the badge carry the player display name instead of the seat number?** Working answer: no — names are wide and break the ≤5×5 footprint. The seat number is enough to disambiguate; the full name lives on the stacked seat block's header.
- **Mid-hand claim-window per-seat "deciding" badge.** Working answer: not in the pinwheel for v1. The single center `?` is enough; per-seat decision indicators belong on the stacked seat blocks if they're added.

## Cross-spec impact

- [state-schema.md](state-schema.md) — no change. Every field the pinwheel needs is already projected (`seats[i]`, `current_actor`, `last_discard`, `phase`).
- [wire-protocol.md](wire-protocol.md) — no change.
- [tui-client.md](tui-client.md) — unchanged. The TUI spec is historical; the web client (Lit + render.js) is the live consumer.
- [render.js header comment](../../mahjong/web/static/render.js) — the locked stacked-seat convention is preserved; the pinwheel is documented as a separate widget alongside it.
