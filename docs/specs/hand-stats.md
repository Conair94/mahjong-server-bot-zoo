# Spec 37 — Hand stats: shanten, waits, fan potential, tiles remaining

A per-seat, decision-time analysis payload — *how far am I from a winning
hand, what tiles get me there, how many of each are still drawable, and how
many fan would it score* — computed server-side from the authoritative seat
view and attached to the existing `PROMPT` wire frame, rendered in a
toggleable detail panel (`Alt+S`) in the web client.

**Revision (2026-06-12).** Three scope changes from the original Spec 37:

1. **Discard-only.** Stats are computed and attached only on **DISCARD**
   prompts — the one moment the seat holds 14 tiles and the question "which
   tile, and how far does each leave me?" is well-posed. CLAIM-time stats
   (standing-hand shanten + per-claim reachability) are no longer surfaced.
2. **Detail-pane only — no inline strip.** The in-board stats strip is
   removed; the analysis is opt-in via the `Alt+S` pane, off by default.
3. **Per-table opt-out.** `CREATE_TABLE.options.stats_enabled = false`
   disables the analyzer for the whole table (resolves the "tournament mode"
   open question below). The pane then shows *"this game has stats
   disabled."*

Dual-purpose by construction: the same analysis that aids a human player is
the explainability surface for the bot zoo (it is exactly the quantity
`mahjong/bots/v0.py` ranks discards by), so the module lives in the shared
analysis layer, not the client.

Builds on:
- [engine-api.md](engine-api.md) § PyMahjongGB integration boundary —
  `pymj.shanten` / `winning_tiles` / `calculate_fan` are the only math
  primitives used.
- [state-schema.md](state-schema.md) § Per-seat projection — the input is the
  seat's own `SeatView` (privacy: stats derive from nothing the seat can't
  already see).
- [wire-protocol.md](wire-protocol.md) § PROMPT — the carrier frame; `stats`
  is a new **optional** field, no new message kind.
- [session-mux.md](session-mux.md) § The HumanAdapter — where the prompt is
  translated to the wire shape; the analysis hook is injected there.
- [v0-offense-bot.md](v0-offense-bot.md) — the fan-aware-distance rationale
  this surfaces to humans.
- [scoring-config.md](scoring-config.md) — `fan_cliff` (the house 3-fan
  floor) is read from the resolved ruleset config, same seam as legality.

## Goals

- On every **DISCARD prompt**: per legal discard candidate — resulting
  shanten, the tiles that advance the hand (waits at tenpai, effective tiles
  otherwise) each with **remaining unseen count**, and at tenpai the **fan
  total per wait** (discard-win and self-draw-win, raw — below-floor shown,
  not hidden).
- Floor-awareness: every fan number is comparable against the ruleset's
  `fan_cliff` so the client can mark sub-floor waits (the FB-15 trap — a
  structural tenpai that cannot legally win).
- Zero cost for bot seats, for tables without humans, and for tables that
  opted out (`stats_enabled = false`): the analyzer is attached only to a
  `HumanAdapter` whose table has stats enabled, at the composition root.

Non-DISCARD prompts (CLAIM windows, etc.) carry **no** `stats` — a 13-tile
hand has no single discard decision to rank, so the question the payload
answers isn't posed there.

## Non-goals

- **No mid-turn push updates.** Stats ride prompts; between your prompts your
  hand cannot change (claims and draws always arrive with a prompt). The
  only thing that drifts off-turn is the *remaining* counts as opponents
  discard — acceptable staleness for v1; the numbers are exact at the moment
  you decide, which is when they matter.
- **No win-probability / EV model.** Counts and fan are facts; probabilities
  are a bot-zoo project (Layer 9+), not a display feature.
- **No opponent-hand inference** (belief.py stays bot-side).
- **No TUI rendering** (web client only; the payload is client-agnostic).
- **No stats during the deal or for spectators** (first stats arrive with the
  seat's first prompt; spectator stats would leak nothing but also serve no
  decision).

## Payload schema (`PROMPT.stats`)

Computed by `mahjong.analysis.prompt_stats(view, seat, legal_actions,
prompt_kind)`. The live binding `analysis.stats_for_prompt` calls it **only
for DISCARD prompts** and returns `None` otherwise, so the wire payload below
is always the discard shape. (`prompt_stats` retains its CLAIM branch —
standing `hand` + `claims` — as a tested pure function; it is simply not
surfaced on the wire today.) Attached to the wire `PROMPT` frame as `stats`;
omitted entirely if analysis fails (the prompt must never be blocked by stats
— see § Failure containment).

```json
{
  "floor": 3,
  "wall_remaining": 42,
  "discards": [
    {
      "tile": "J1",
      "shanten": 0,
      "tiles": [
        {"tile": "W2", "remaining": 3, "fan_discard": 4, "fan_self_draw": 6}
      ]
    },
    {
      "tile": "B9",
      "shanten": 1,
      "tiles": [
        {"tile": "W2", "remaining": 3},
        {"tile": "B7", "remaining": 4}
      ]
    }
  ]
}
```

Field semantics:

- `floor` — the resolved `fan_cliff`. The client compares `fan_discard` /
  `fan_self_draw` against it to mark sub-floor waits; the server never hides
  a below-floor fan number (raw calculation with `fan_cliff: 0`).
- `wall_remaining` — `view.wall.remaining_count`, the "draws left" context
  for the remaining counts.
- `hand` — stats for a seat's standing 3k+1 hand. Computed by `prompt_stats`
  on CLAIM prompts but **not delivered on the wire** under the current
  discard-only binding; documented here because the pure function still
  returns it.
- `hand.shanten` / `discards[].shanten` — `pymj.shanten` (0 = tenpai). The
  engine never prompts a won hand, so −1 does not appear.
- `tiles[]` — at tenpai: the winning tiles (waits), each with raw fan totals
  for a discard win and a self-draw win (they differ: Self-Drawn, Fully
  Concealed). Below tenpai: the tiles whose draw lowers shanten (effective
  tiles / ukeire), no fan fields.
- `tiles[].remaining` — **unseen copies**: `4 − (copies visible to this
  seat)`, counting own concealed, every seat's exposed meld tiles, and every
  discard pond. An opponent's *hidden* concealed kong is not subtractable
  (the seat can't see it) — `remaining` is therefore an upper bound on
  drawable copies, which is the correct epistemic quantity for the player.
- `discards[]` — DISCARD prompts only; one row per legal PLAY, sorted by
  (`shanten`, then descending total `remaining`, then `tile_sort_key`) so
  the client's "best line" is `discards[0]` without re-deriving the policy.
- `claims[]` — computed by `prompt_stats` on CLAIM prompts (one row per legal
  PENG/CHI/GANG, `shanten_after` = best shanten over the forced follow-up
  discard) but, like `hand`, **not delivered on the wire** today.

## Delivery path

```
run_hand → _build_prompt (already carries view + legal_actions)
  → HumanAdapter._translate_prompt
      stats = self._stats_provider(prompt)        # injected; None → no stats
  → SeatPrompt.stats
  → SeatSession._send_prompt → PROMPT frame {... "stats": {...}}
  → client setPrompt(frame) → Alt+S detail panel
```

- `HumanAdapter` gains a `stats_provider: Callable[[Prompt, int], dict |
  None] | None = None` ctor kwarg (the int is the seat, from the adapter's
  `SeatContext`). Adapters stay free of any `pymj` import — the binding
  (`analysis.stats_for_prompt`) is wired at the composition roots
  (`server/registry.py` **and** `web/server.py`; the two hand-loop hosts are
  near-duplicates and both must be wired — see the mirror-both-hand-loops
  rule). `stats_for_prompt` itself returns `None` for non-DISCARD prompts.
- **Per-table opt-out.** `TableHandle` carries a frozen `stats_enabled`
  (from `CREATE_TABLE.options`, default `true`). When `false`, the
  composition root binds `stats_provider=None`, so no analysis runs and no
  `stats` is ever attached. The flag is also spliced onto the ATTACHED
  snapshot (`snapshot["stats_enabled"]`, same seam as `match_scores`) so the
  client can render the *"stats disabled"* message rather than a bare
  placeholder. The single-table `web/server.py` path has no options UI and
  always runs with stats enabled (it omits the snapshot flag; the client
  treats an absent flag as enabled).
- `SeatPrompt` gains `stats: dict[str, Any] | None = None`;
  `_send_prompt` includes it only when non-None (no schema churn for TUI /
  bot tooling reading PROMPT frames).
- Reconnect: `_reprompt_if_pending` resends the stored `SeatPrompt`
  unchanged, so the stats arrive again for free.

### Failure containment

The provider call is wrapped: any exception → log `hand_stats_failed` with
the prompt id, seat, and traceback, then send the prompt **without** stats.
A stats bug must never delay or break the decide path — the prompt is
game-critical, the garnish is not. (No ledger row: nothing is parked; the
log line carries its own traceback if it ever fires.)

### Alternatives considered

- **Client-side computation** — rejected: shanten/fan need PyMahjongGB; a JS
  port is a new conversion layer (prefer-existing-standards) and a second
  source of scoring truth.
- **On-demand `GET_HAND_STATS` request** — rejected: the live mid-hand
  `GameState` is local to `run_hand`; the router has no authoritative state
  to answer from (the snapshot provider only re-projects the *initial*
  state). Prompt-time piggyback needs no new state plumbing and is fresh
  exactly when the player decides.
- **Push per event via `event_callback`** — rejected: that seam is the
  *public/spectator* fanout (event_callback-spectator-seam memory); stats
  are per-seat private. Also ~10× more computations for no decision-time
  benefit.
- **Compute inside `run_hand` for all seats** — rejected: bots already do
  their own math; paying analysis cost on bot-only tables (self-play!) is
  waste.

## Client rendering

- **No inline strip.** The in-board stats strip is removed; the analysis is
  off by default and lives solely behind the `Alt+S` detail pane.
- **Detail panel** (the `Alt+S` pane hotkey; the old `<stats-pane>` stub is
  repurposed — career/cross-game stats belong to the profile page, not this
  pane): full per-candidate table, all waits/effective tiles with remaining
  counts and fan, sorted as delivered. Fed by `<table-page>` mirroring the
  game-pane's `prompt-changed` event. Rendered from `frame.stats` verbatim —
  the client does **no** game math (authoritative-state rule).
  - When the table opted out (`stats_enabled = false`, read from the snapshot
    the game-pane projects onto its view), the pane shows *"this game has
    stats disabled."* instead of any analysis.
  - Outside a DISCARD prompt (no `stats` on the live prompt), the pane shows
    a placeholder: *"hand analysis appears when it's your turn to discard."*
- The panel content clears when the prompt resolves (EVENT advancing the
  hand) — same lifecycle as `currentPrompt`.

## Verification fixtures

Analysis module (`tests/analysis/test_hand_stats.py`, TDD-first — this is
core math under the project's strict bucket):

1. **Tenpai with known waits**: crafted 13-tile hand with a two-sided wait →
   `hand.shanten == 0`, `tiles` exactly the two waits, fan fields present,
   self-draw fan > discard fan (Self-Drawn).
2. **Sub-floor tenpai (FB-15 shape)**: structural tenpai whose best discard
   win is below the 3-fan floor → wait listed with `fan_discard < floor`
   (raw, not hidden, not empty).
3. **Remaining counts subtract every visible zone**: a wait tile with copies
   spread across own hand, an opponent's exposed pung, and two discard
   ponds → `remaining == 4 − total visible`; an opponent's *hidden*
   concealed kong of the wait tile leaves `remaining` unchanged (upper
   bound).
4. **1-shanten effective tiles**: crafted 1-shanten hand → `tiles` is
   exactly the accepted set (cross-checked against `v0.fan_feasible_ukeire`
   counting), no fan fields.
5. **DISCARD prompt table**: 14-tile hand where exactly one discard reaches
   tenpai → that candidate first in `discards`, its `shanten == 0`; a
   known-bad discard ranks last; ordering key pinned.
6. **CLAIM prompt**: crafted view where PENG reaches tenpai after the best
   follow-up discard but CHI doesn't → `claims` rows carry the correct
   `shanten_after`; `hand` block present and correct.
7. **Determinism**: same view in → byte-identical payload (sorted
   candidates, sorted tiles).

Wire/session seam (`tests/sessions/` + `tests/wire/`):

8. PROMPT frame round-trips through the codec with `stats` preserved.
9. `HumanAdapter` with a provider → `SeatPrompt.stats` populated;
   provider raising → prompt still delivered, `stats` absent, one
   `hand_stats_failed` log line (the DEF-18 containment contract).
10. Mux `_send_prompt` omits the key when `stats is None`.

Gating + option (`tests/analysis/`, `tests/server/`):

11. `stats_for_prompt` returns the discard payload for a DISCARD prompt and
    `None` for a CLAIM (or any non-DISCARD) prompt.
12. `parse_table_options({"stats_enabled": false})` → `stats_enabled=False`;
    absent / any non-`false` value → `True`.

Client seam (`tests/web/`, real-frame dispatch per the wire→UI rule):

13. Dispatching a DISCARD PROMPT frame with `stats` renders **no** inline
    strip; pressing `Alt+S` shows the per-candidate detail table matching the
    payload; dispatching the resolving EVENT clears it.
14. An ATTACHED snapshot carrying `stats_enabled: false` makes the `Alt+S`
    pane show the *"stats disabled"* message.

## Settlement / hand-end stats (2026-06-14)

A second use of the same analysis primitives, requested from live play: the
hand-end summary should show, for **every seat that didn't win**, how close
they were and what they were playing for.

- **Function:** `analysis.settlement_hand_stats(seats, round_wind, ruleset,
  exclude_seats)` — pure, reuses `_shanten` / `_fan_total` /
  `pymj.winning_tiles`. Privacy is moot: at TERMINAL all hands are revealed
  (see [HAND_END.final_hands](../../mahjong/engine/state.py) `final_hands_view`),
  so this is settlement-time data, not an in-hand aid.
- **Carrier:** attached to the `HAND_END` record event as
  `terminal.final_hand_stats` in `records/diff.py._hand_end_event` — the
  single source that flows to seats, spectators, **and** the persisted record
  via `_terminal_from_record`. No new wire kind. Determinism is unaffected:
  the rollout hash is `state_hash(state)` (over GameState), not over events.
- **Shape** — `{ floor, seats: [ {seat, shanten, waits?|accepts?} ] }`:
  - shanten 0 (tenpai) → `waits`: every winning tile with raw
    `fan_discard`/`fan_self_draw` (cliff zeroed; `floor` shipped so the client
    dims sub-floor, the FB-15 convention).
  - shanten 1 → `accepts`: the **top-3** tiles that reach tenpai, ranked by the
    best fan reachable once tenpai is hit (a 2-ply optimistic max). Capped
    because a 1-shanten hand often accepts 8+ tiles.
  - shanten ≥ 2 → shanten only, no fan (deliberate, per the live-play ask).
  - Winner(s) excluded; on an exhausted draw all four seats appear.
- **Client:** `_summaryTenpai` section appended to `render.js`'s
  `HAND_END_SECTIONS`; renders nothing for pre-upgrade servers / replays
  lacking the field.

**Cost note (deferred):** `diff_to_events` also runs in the self-play / eval
loop. Typical hand-ends are ~0.01 ms (most non-winners are 2+ shanten → one
shanten call); the 1-shanten `accepts` path is ~3–12 ms for an all-1-shanten
hand. Acceptable at current eval scale; see the DEF row in
[feedback-backlog.md](feedback-backlog.md) for gating it behind a flag when a
high-throughput training loop lands.

### Settlement fixtures (`tests/analysis/test_settlement_stats.py`)

1. **Tenpai seat** → shanten 0, per-wait fan matching the independently-probed
   `TENPAI_A` figures (cross-checks the settlement path against prompt-stats).
2. **1-shanten seat** → top-3 `accepts`, ranked by best fan desc / tile order,
   capped (the 4th acceptance tile dropped).
3. **2-shanten seat** → shanten only, no `waits`/`accepts`.
4. **Winner excluded; draw reveals all four.**
5. **Non-3k+1 hand skipped** (defensive).
6. **Deterministic / JSON-safe.**

Wire/record seam: `tests/records/test_diff.py` (HAND_END carries
`final_hand_stats`, winner excluded) + `tests/wire/test_codec.py` (the field
survives the HAND_END round trip). Client: `tests/web/test_hand_end_summary.py`
(the `_summaryTenpai` section for 0/1/2-shanten + sub-floor dimming).

## Open questions

- Should the pane show the v1 bot's *defense* read (deal-in risk) once Stage B
  lands? Lean yes, as a separate dimmed line — but that couples the display to
  bot internals; revisit when v1 Stage B exists.
- ~~Per-account opt-out ("tournament mode": no aids)?~~ **Resolved
  (2026-06-12)** as a per-table option (`CREATE_TABLE.options.stats_enabled`),
  not per-account — the table is the natural scope for "no aids in this game".
- TUI client rendering of the same payload — when the TUI gets attention
  again.
