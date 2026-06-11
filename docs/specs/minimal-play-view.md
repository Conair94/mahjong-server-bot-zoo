# Spec 34 — Minimal play view & in-game player names

A second, decluttered rendering of the play state, **toggleable** with the
existing table view, plus the long-missing ability to **see player names while
playing**. Driven by the user's 2026-06-11 request: the current play state shows
too much; what a player needs to act is *what's been discarded (and the most
recent), what melds/flowers are out and for whom, whose turn it is, and a
prominent claim-window cue* — in large print.

Tier-3 spec (client renderer) **plus one additive server enrichment** (seat
names on the snapshot). The classic view is preserved unchanged and is slated to
grow into the stats-heavy view later; the minimal view is the new default while
it's the focus of iteration.

Builds on:
- [state-schema.md](state-schema.md) § Per-seat projection — the `SeatView` the
  renderer consumes (`seats[]`, `last_discard`, `current_actor`, `phase`).
- [cardinal-ui.md](cardinal-ui.md) — the pinwheel; minimal mode omits it (it
  carries its own whose-turn + last-discard cues).
- [profile-and-settings.md](profile-and-settings.md) § Part A — the settings
  registry the toggle plugs into (`mahjong/web/static/settings.js`).
- [layer8-closeout-r2.md](layer8-closeout-r2.md) § 22.2 — the claim-available
  alert predicate (`isClaimAvailable`) the prominent banner reuses.

## Goals

- A play view that shows **only** the decision-relevant information, large:
  whose turn, the most-recent discard (large), each player's melds + flowers +
  score, a combined discard pond, your own hand, and a prominent claim cue.
- **Player names visible in-game** for every seat (humans by display name, bots
  by `bot_id`), in both the minimal and classic views.
- A **persisted toggle** between minimal and classic, mirroring the existing
  theme / tile-style preference plumbing (settings menu row + `Alt+M` hotkey).

## Non-goals

- Touching the engine projection's privacy rules. Names are server-roster
  metadata, spliced on at the snapshot boundary; the pure projection is
  unchanged.
- A new wire **kind**. Names ride the existing `ATTACHED.snapshot.seats[i]`;
  the codec already preserves unknown optional fields.
- The stats-heavy view. The classic view is untouched here beyond gaining names;
  it grows into the stats view in a later spec.
- Exact discard-pond ordering on a *mid-hand reconnect* (parked — DEF-15).

## Player names on the snapshot (server)

`SeatView` (engine `project_state`) carries no names by design — names are an
account/registry concept. The one place that holds **both** the projection and
the seat composition is `TableHandle._snapshot_provider`
([registry.py](../../mahjong/server/registry.py)), so the enrichment lives there:

```python
def _snapshot_provider(self, seat):
    snapshot = project_state(self._initial_state, seat)
    self._annotate_seat_names(snapshot)   # splice name/is_bot per seat
    return snapshot
```

Each projected seat gains two additive fields:

```jsonc
{
  "seat": 0,
  "seat_wind": "F1",
  "score": 0,
  "concealed": [...],          // or {"count": N} for opponents
  "melds": [...], "flowers": [...], "discards": [...],
  "name": "Alice",             // NEW — human display name or bot_id
  "is_bot": false              // NEW — true for bot seats
}
```

Rules (`_seat_name_map`):
- **human, occupied** → identity display name (falls back to `user_id`).
- **human, empty** → `name: null` (client renders the wind+seat fallback).
- **bot** → `name: <bot_id>` (e.g. `"v0"`), `is_bot: true`.

Delivery: once, on `ATTACHED`. The client reducer (`apply_event.js`
`cloneSeatView`) spreads `...s` per seat, so the fields persist across every
subsequent `EVENT` without re-sending. **Known caveat:** `TableHandle.attach`
records the identity *after* the session builds that seat's own first ATTACHED,
so a player's *own* name in their *first* snapshot is the `user_id`; every later
snapshot (and every other seat) has the display name. Cosmetically irrelevant —
the own seat renders as "YOU".

## The combined discard pond (client reducer)

Decision (2026-06-11): the pond is **one combined chronological list**, not
per-seat rows. `SeatView` only has per-seat `discards`, so the global order is
maintained in the reducer as `view.discard_pond` (`{seat, tile}` in arrival
order):

- **seeded** on the first discard from the per-seat piles (exact when watched
  from hand start: seeds at one tile);
- **appended** in arrival order thereafter;
- **pulled** when a discard is claimed into a meld (mirrors
  `pullCalledTileOffDiscarder`), so a melded tile doesn't double-count;
- deep-copied by `cloneSeatView` so a push never mutates a prior view.

Mid-hand reconnect can't recover exact global order from the snapshot; the seed
approximates by interleaving the piles round-robin from the dealer and
self-heals as play continues. Exact reconnect ordering is parked (DEF-15).

## Layout (minimal)

`renderMinimal(seatView, ownSeat, options)` → a `<div class="mv">` stack
(top → bottom). Tiles honour the player's tile-style; "large" is CSS, not forced
unicode.

```
        ● YOUR TURN ●              ← whose-turn banner (or "Alice's turn"; never announces claim windows — see below)
 ─────────────────────────────
   Last discard — Bob (South)     ← mv-lastdiscard
            🀝                     ← large
 ─────────────────────────────
 Bob   (S)  ♦200  [PENG …]  🌸 —   ← opponents in CCW play order
 Carol (W)  ♦300  —         🌸 5F
 Dave ·bot (N) ♦400 [CHI …] 🌸 —
 ─────────────────────────────
 Discards (7)                     ← mv-pond (combined, latest highlighted)
   🀇 🀝 🀞 🀟 🀚 🀛 🀜
 ─────────────────────────────
 YOU · Alice (E)  ♦100  …melds…  🌸 1F
   🀇 🀈 🀉 🀊 🀋 🀌 🀍 …          ← your hand, large (selection/just-drawn cues kept)
```

The **prominent claim cue** is owned by `<game-pane>`, not `renderMinimal`: when
`isClaimAvailable(currentPrompt)` and the view is minimal, the existing
`.claim-chip` gains `.mv-claim` and CSS renders it as a full-width pulsing alert.
This keeps the cue alive even when the table content scrolls.

**No claim-window leak.** The whose-turn banner deliberately does *not* announce
`CLAIM_WINDOW` (an early draft showed "Claim window open"). Surfacing the
window's existence/duration is an information leak — the same tell Spec 22
§ 22.1 keeps off the pinwheel: at a physical table you only learn someone is
deciding-whether-to-claim from body language. During the window `current_actor`
still points at the discarder, so the banner simply stays on the prior turn (no
flicker, no tell). Your *own* claim opportunity is still surfaced (the prompt bar
and the `.mv-claim` banner) — that's information you're entitled to. The future
**auto-claim** flow removes the window entirely, eliminating the leak at the
source.

## Toggle

A `view-mode` row in the settings registry (`["minimal", "classic"]`,
`scope: "global"`, hotkey `Alt+M`), persisted to `localStorage`
(`mahjong-view-mode`, default `"minimal"`), threaded
`MahjongApp → table-page → game-pane` exactly like `tileStyle`. `<game-pane>`
picks `renderMinimal` vs `renderTable` and omits the pinwheel in minimal mode.

## Verification fixtures

Backend ([tests/server/test_seat_names_snapshot.py](../../tests/server/test_seat_names_snapshot.py)):
1. Bot seats in the ATTACHED snapshot carry `is_bot: true`, `name: "v0"`.
2. A second player's snapshot shows the first player's display name on their seat.

Reducer ([tests/web/test_reducer_discard_pond.py](../../tests/web/test_reducer_discard_pond.py)):
3. Three discards from different seats land in `discard_pond` in arrival order.
4. A PENG resolution removes the claimed tile from the pond (and forms the meld).

Renderer ([tests/web/test_minimal_view.py](../../tests/web/test_minimal_view.py)):
5. Names headline opponent rows; the bot seat is badged `·bot`; own block shows YOU + name.
6. The pond renders `discard_pond` in order; the last tile carries `.pond-latest`.
7. `current_actor == ownSeat` → the YOUR TURN banner (`.mv-turn-you`).
8. Another active seat → the banner names them.
9. The last discard renders large with the discarder's name + tile.
10. A CLAIM_WINDOW prompt raises `.claim-chip.mv-claim` in a minimal `<game-pane>`.

Browser-verify owed (→ DEF-04 bucket): live look at the minimal layout, large-print
legibility, the claim banner, and `Alt+M` toggling — the visual/UX pass a headless
Playwright run can't make. **This spec ships the first cut for that feedback pass.**

## Open questions

- **Roster ordering.** Opponents render in CCW play order from you (next-to-act
  first). Alternative: fixed wind order (E,S,W,N). Play-order chosen as more
  decision-relevant; revisit on feedback.
- **Bot name.** Shows the bare `bot_id` (`v0`). Could show the registry label
  ("v0 — greedy offense"); deferred as too long for a large-print row.
- **Own-hand placement.** Bottom (physical-table convention). The very large
  last-discard + hand may need a height budget on small screens — a feedback item.
