# Spec 40 — In-game scoreboard, discard layout toggle, panel placement

Three player-requested table-view improvements (ConnorL, 2026-06-11), grouped
because they share the table-page layout and the snapshot:

1. **Running point totals** — a cumulative match score per seat, accumulated
   across the hands played at a table since it was created. Today the per-seat
   `Score:` line shows the engine's *per-hand* score, which is `0` for the
   whole hand (MCR scores only at terminal) — so during play there is no
   running tally. Two surfaces: (a) the cumulative total shown **inline next
   to each player's name**, and (b) a **toggleable widget** with a per-player
   cumulative line graph.
2. **Discard layout toggle** — switch the discard presentation between
   per-player rows (default) and one combined chronological pond.
3. **Panel placement** — an open chat pane currently eats a third of the
   width. Move the non-chat panes (stats, the new score widget) **below** the
   game pane and keep **chat to the side** in a narrower column.

Builds on:
- [state-schema.md](state-schema.md) / `project_state` — the per-seat snapshot
  is the authoritative state a client renders. Cumulative scores are a
  **table/match** concept (not in `GameState`), so they are injected at the
  one seam that already holds both the projection and table composition:
  `TableHandle._annotate_seat_names` (registry.py) and its `WebOrchestrator`
  mirror (the **mirror-both-hand-loops** rule applies).
- [render.js](../../mahjong/web/static/render.js) `renderScoreGraph` — the
  cumulative-series ASCII chart already built for the profile page; the widget
  reuses it, one line per seat.
- DEF-15 / `view.discard_pond` — the combined-pond projection already exists
  (exact from hand start; approximate on mid-hand reconnect). The "pond"
  toggle mode reuses it rather than re-deriving order client-side.
- Spec 34 seat-name snapshot annotation — names already ride each `seat_view`;
  `match_score` rides the same dict.

## Goals

- A seated player sees each opponent's **cumulative match score** beside their
  name, stable through the current hand, updated when a hand ends.
- A toggleable score widget (keyboard chord + Settings entry, like the other
  client toggles) shows one cumulative line graph per seat over the match's
  completed hands.
- A discard-layout toggle flips between per-player rows (default) and combined
  pond, in the main game view.
- Cumulative state is **server-authoritative**: it rides the snapshot, so a
  reconnecting client / late joiner / spectator gets the correct totals and
  full series without client-side accumulation (prefer-authoritative rule).
- Chat keeps a side column (narrower); stats + score widget stack under the
  game pane.

## Non-goals

- **No draggable/movable panels** (the player's stated ideal). A free-form
  windowing system fights the fixed ASCII layout and is a large surface for a
  cosmetic win. Deferred — **DEF-19**.
- **No persistence of match standings.** Cumulative score is the live table's
  running tally since creation; it is not written to SQLite and does not
  survive a server restart. (Career totals already live in the profile.)
- **No match-length / target-score rules.** This is a display of the sum of
  per-hand deltas, not a new game-end condition.
- **No new wire frame.** Standings ride the existing per-seat snapshot; the
  per-hand delta is already revealed in `HAND_END.score_delta`.
- **No exact pond re-ordering on reconnect** beyond what DEF-15 already does.

## Server: cumulative score accumulation

State on the table host (both `TableHandle` and `WebOrchestrator`):

```python
self._cumulative_scores: list[int] = [0, 0, 0, 0]   # per seat, since table creation
self._score_series: list[list[int]] = []            # standings after each completed hand
```

In the hand loop, **after** `run_hand` returns `final_state` and **before**
`begin_next_hand()` rebuilds the snapshot (registry.py ~line 856):

```python
terminal = final_state["terminal"] if final_state is not None else None
deltas = terminal.get("score_delta") if terminal else None
if not (isinstance(deltas, list) and len(deltas) == 4):
    deltas = [0, 0, 0, 0]              # draw / aborted hand → no change, still a series point
for s in range(4):
    self._cumulative_scores[s] += int(deltas[s])
self._score_series.append(list(self._cumulative_scores))
```

`begin_next_hand()` already re-pushes a fresh per-seat snapshot, so the next
hand carries the updated totals with no extra plumbing. The HAND_END hold
(`_await_humans_ready`, FB-02) means the player reads the per-hand delta in the
summary, then sees the new cumulative total when the next hand's board appears.

### Snapshot additions (`_annotate_seat_names`)

Per seat view (alongside the existing `name` / `is_bot`):

```json
{ "seat": 1, "name": "bot1", "is_bot": true, "score": 0, "match_score": -16 }
```

Top-level, once per snapshot (for the widget — full history, server-authoritative):

```json
{
  "match_scores": {
    "cumulative": [48, -16, -8, -24],
    "series": [[24, -8, -8, -8], [48, -16, -8, -24]],
    "hands_complete": 2
  }
}
```

- `series[i]` = cumulative standings after completed hand `i` (0-based). Seat
  `p`'s line for the graph is `[series[0][p], series[1][p], …]`.
- Before any hand completes, `series` is `[]` and every `match_score` is `0`.
- `match_score` per seat is redundant with `cumulative[seat]` but spares the
  per-seat renderer an index into the top-level block.

## Client

### Inline running total (render.js)

`seatHeader` / the multi-view roster line append the cumulative total to the
existing label. Keep the per-hand `Score:` only where it is non-trivial
(HAND_END summary already shows per-hand deltas), or drop it from the live row
to avoid two zeros mid-hand:

```
bot1 ·bot — South (Seat 2)   Match: -16
```

`match_score` falls back to `0` when absent (older servers / pre-first-hand).

### Score widget pane (`<score-pane>`)

- New pane, parallel to `<stats-pane>` / `<chat-pane>`; keyboard chord
  (proposed `Alt+P` — points; `Alt+S` is taken by stats) and a Settings entry.
- Renders four `renderScoreGraph` lines from `match_scores.series` (one per
  seat, labeled by name + wind), plus a current-standings line. Empty state
  before the first hand: "(no completed hands yet)".
- Reads from the live snapshot the game-pane already holds; re-renders on each
  new snapshot (i.e. each new hand) — no local accumulation.

### Discard layout toggle

- New client setting `discardLayout: "rows" | "pond"`, default `"rows"`,
  cycled from a header/Settings toggle + chord (proposed `Alt+D`). Mirrors the
  existing `tileStyle` / `viewMode` toggle plumbing.
- `"rows"`: a consolidated discard board — four rows in fixed seat order
  (E,S,W,N), each that seat's discards (wrap at 12, the current `renderDiscards`
  row width), labeled by seat/name.
- `"pond"`: one combined chronological clump from `view.discard_pond`
  (DEF-15's projection; the existing combined-pond renderer).
- Persisted in `localStorage` like the other client toggles.

### Panel placement (table-page grid)

- Chat → **side** column, capped narrower (target ≈ 26ch / `minmax(0, 0.6fr)`
  instead of the current `1fr`), so it no longer eats a third of the board.
- Stats + score widget → **below** the game pane (full game-column width),
  stacked. Multiple under-panes stack vertically.
- Grid areas: `game` (top-left), `side` (chat, right), `under` (stats/score,
  below game). `no-side` / `no-under` collapse to the single game column as
  today.

## Verification fixtures

1. **Server accumulation (unit, test-first).** Drive two finalized hands
   through the hand loop (or a focused unit on the accumulation block) with
   known `score_delta`s; assert `_cumulative_scores` and `_score_series` after
   each, including a draw (all-zero delta still appends a series point), and
   that the totals sum to zero each hand.
2. **Snapshot carries standings.** After N completed hands, a fresh snapshot
   has `match_score` per seat == `cumulative[seat]` and `match_scores.series`
   of length N; a from-zero snapshot (no completed hands) has `series == []`
   and all `match_score == 0`.
3. **Mirror check.** The same assertions hold for `WebOrchestrator` (single
   table) — both hand loops accumulate identically.
4. **Reconnect authority.** A client attaching after hand 2 receives the full
   `series` (length 2) and correct `cumulative` in its first snapshot — no
   client-side accumulation needed.
5. **Client render (Playwright / view test).** An injected snapshot with
   `match_scores` renders inline totals beside names and a `<score-pane>` with
   four graph lines; `Alt+D` flips discards rows↔pond on the real frame path;
   an open chat pane no longer occupies the full side third (computed width
   assertion on a mounted table-page).

## Open questions

- Should the inline live row drop the per-hand `Score:` entirely (always `0`
  mid-hand) or show `Match: X (hand: Y)` at terminal? Leaning drop-from-live,
  keep per-hand in the HAND_END summary.
- Chord letters (`Alt+P` score, `Alt+D` discards) — confirm no collision with
  the existing keymap during implementation.
- Match reset semantics if/when tables gain an explicit "new match" control
  (today a table is one open-ended match since creation).
