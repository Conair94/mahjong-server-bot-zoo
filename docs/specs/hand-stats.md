# Spec 37 — Hand stats: shanten, waits, fan potential, tiles remaining

A per-seat, decision-time analysis payload — *how far am I from a winning
hand, what tiles get me there, how many of each are still drawable, and how
many fan would it score* — computed server-side from the authoritative seat
view and attached to the existing `PROMPT` wire frame, rendered as a stats
strip + toggleable detail panel in the web client.

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
- On every **CLAIM prompt**: the current hand's same stats, plus per claim
  option (PENG/CHI/GANG) the **best reachable shanten after the forced
  discard** — "does taking this claim actually advance me?"
- Floor-awareness: every fan number is comparable against the ruleset's
  `fan_cliff` so the client can mark sub-floor waits (the FB-15 trap — a
  structural tenpai that cannot legally win).
- Zero cost for bot seats and for tables without humans: the analyzer is
  attached only to `HumanAdapter` at the composition root.

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
prompt_kind)`. Attached to the wire `PROMPT` frame as `stats`; omitted
entirely if analysis fails (the prompt must never be blocked by stats — see
§ Failure containment).

```json
{
  "floor": 3,
  "wall_remaining": 42,
  "hand": {
    "shanten": 0,
    "tiles": [
      {"tile": "W2", "remaining": 3, "fan_discard": 4, "fan_self_draw": 6},
      {"tile": "W5", "remaining": 2, "fan_discard": 2, "fan_self_draw": 4}
    ]
  },
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
  ],
  "claims": [
    {"action": {"type": "PENG", "tile": "B5"}, "shanten_after": 0},
    {"action": {"type": "CHI", "tiles": ["B4", "B5", "B6"]}, "shanten_after": 1}
  ]
}
```

Field semantics:

- `floor` — the resolved `fan_cliff`. The client compares `fan_discard` /
  `fan_self_draw` against it to mark sub-floor waits; the server never hides
  a below-floor fan number (raw calculation with `fan_cliff: 0`).
- `wall_remaining` — `view.wall.remaining_count`, the "draws left" context
  for the remaining counts.
- `hand` — stats for the seat's current 3k+1 hand. Present on **CLAIM**
  prompts (your standing 13-tile hand) and **omitted on DISCARD prompts**
  (a 14-tile hand has no single shanten; the per-candidate table is the
  answer there).
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
- `claims[]` — CLAIM prompts only; one row per legal PENG/CHI/GANG.
  `shanten_after` = best shanten over the forced follow-up discard (mirrors
  `v0._decide_claim`'s reachability probe). HU needs no row (it is the win).
  For GANG the follow-up is a replacement draw, not a discard; its
  `shanten_after` is the shanten of the post-meld 3k+1 hand.

## Delivery path

```
run_hand → _build_prompt (already carries view + legal_actions)
  → HumanAdapter._translate_prompt
      stats = self._stats_provider(prompt)        # injected; None → no stats
  → SeatPrompt.stats
  → SeatSession._send_prompt → PROMPT frame {... "stats": {...}}
  → client setPrompt(frame) → stats strip + Alt+S detail panel
```

- `HumanAdapter` gains a `stats_provider: Callable[[Prompt, int], dict |
  None] | None = None` ctor kwarg (the int is the seat, from the adapter's
  `SeatContext`). Adapters stay free of any `pymj` import — the binding
  (`analysis.stats_for_prompt`) is wired at the composition roots
  (`server/registry.py` **and** `web/server.py`; the two hand-loop hosts are
  near-duplicates and both must be wired — see the mirror-both-hand-loops
  rule).
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

- **Stats strip** (both minimal and classic views), shown while a prompt
  with `stats` is live:
  - DISCARD: one line for the currently selected tile —
    `B9 → 1-shanten · 5 kinds / 17 tiles · best: W2 ×3` — plus a
    `best line` hint when a strictly better candidate exists. At tenpai the
    line shows waits with fan: `J1 → TENPAI · W2 ×3 (4f / 6f self)`;
    sub-floor waits render dimmed with `<floor`.
  - CLAIM: `now: 1-shanten · PENG → tenpai · CHI → 1-shanten` (options that
    don't improve are dimmed).
- **Detail panel** (the existing `Alt+S` pane hotkey; the old `<stats-pane>`
  stub is repurposed — career/cross-game stats belong to the profile page,
  not this pane): full per-candidate table, all waits/effective tiles with
  remaining counts and fan, sorted as delivered. Fed by `<table-page>`
  mirroring the game-pane's `prompt-changed` event. Rendered from
  `frame.stats` verbatim — the client does **no** game math
  (authoritative-state rule).
- Strip and panel clear when the prompt resolves (EVENT advancing the hand)
  — same lifecycle as `currentPrompt`.

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

Client seam (`tests/web/`, real-frame dispatch per the wire→UI rule):

11. Dispatching a PROMPT frame with `stats` renders the strip (selected
    candidate's shanten + counts); dispatching the resolving EVENT clears
    it.
12. `Alt+S` toggles the detail panel; panel rows match the frame payload.

## Open questions

- Should the strip show the v1 bot's *defense* read (deal-in risk) once
  Stage B lands? Lean yes, as a separate dimmed line — but that couples the
  display to bot internals; revisit when v1 Stage B exists.
- Per-account opt-out ("tournament mode": no aids)? Settings toggle exists
  client-side; a server-enforced table option belongs with the future
  table-options work (`server/table_options.py`).
- TUI client rendering of the same payload — when the TUI gets attention
  again.
