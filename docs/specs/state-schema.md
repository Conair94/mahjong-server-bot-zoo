# Spec 1 — Game state schema

The single value object passed through the rules engine. Every component the system has — engine, table manager, adapters, bots, overlays, training feature extractors — sees or projects from this object. Locking its shape is the single highest-leverage thing the design phase produces.

**Status:** draft, pre-S0.

## Goals

- One canonical state object. Not "table state plus seat state plus wall state" passed separately — one structure, one type, one source of truth.
- **Immutable by convention.** The engine returns a new state from `apply_action(state, action)`; it never mutates. Cheaper to test, trivially serializable, no aliasing bugs.
- **Serializable.** Round-trips through JSON without information loss. This is what makes determinism testable (hash the JSON) and what makes records loadable (the record is the action log; replaying it reproduces the state).
- **Per-seat projection is a pure function of the canonical state.** "What seat S sees" is `project(state, seat=S)`. Concealed information lives in the state but is filtered out by the projection. This is what makes the same record file usable by all four seats' replay views, the spectator view, and the all-seeing self-play driver.
- **Bound to a rule-set version.** The state carries the ID + version of the rule set it was created under, so a record loaded years later is scored under its original rules.

## Non-goals

- No incremental update API. The state is recomputed by applying actions to the prior state; there is no "patch" object.
- No object identity. Two states with equal fields are equal. `hash(state) == hash(state)` always.
- No internal pointers, no graphs, no shared mutable substructures. Plain data only.

## Tile encoding

We adopt the **Botzone token format** verbatim — see the [Botzone Chinese Standard Mahjong wiki](https://wiki.botzone.org.cn/index.php?title=Chinese-Standard-Mahjong/en) for the canonical definition. No translation layer between "our format" and Botzone's — they are the same strings. This eliminates a class of conversion bugs and means every fixture is directly interchangeable with the official judge and `botzone-mahjong-environment`.

| Suit | Token prefix | Range | Meaning |
| --- | --- | --- | --- |
| Characters (万 wan) | `W` | `W1`–`W9` | 9 ranks × 4 copies = 36 tiles |
| Dots (筒 bing) | `B` | `B1`–`B9` | 9 ranks × 4 copies = 36 tiles |
| Bamboo (条 tiao) | `T` | `T1`–`T9` | 9 ranks × 4 copies = 36 tiles |
| Winds (风 feng) | `F` | `F1`–`F4` | East/South/West/North × 4 = 16 tiles |
| Dragons (箭 jian) | `J` | `J1`–`J3` | Red/Green/White × 4 = 12 tiles |
| Flowers/Seasons (花 hua) | `H` | `H1`–`H8` | 1 copy each = 8 tiles |

**Total: 144 tiles.** `H1`–`H4` are the four flowers (plum, orchid, chrysanthemum, bamboo); `H5`–`H8` are the four seasons (spring, summer, autumn, winter). These are bonus tiles: they are drawn, immediately set aside, and the player draws a replacement. They do not enter the player's playable hand.

**A tile token is a `str`.** Not an int, not an enum, not a wrapper. Strings are JSON-native, comparable, hashable, and human-readable in logs and fixtures. The marginal performance cost vs. integer encoding is paid back many times over in debuggability. If performance ever becomes a bottleneck (it won't at v1 scale), integer encoding is an internal optimization inside hot loops, never the public type.

## Top-level state object

```python
GameState = {
    # Rule context
    "ruleset": {
        "id": "mcr-2006",       # canonical name
        "version": 1,           # bumped when our config-overlay changes
        "config_hash": "sha256:abc123…",  # hash of the resolved config
    },

    # Round / hand context
    "round_wind": "F1",        # E/S/W/N as F1–F4
    "dealer_seat": 0,          # 0..3; seat 0 is East at hand start
    "hand_index": 0,           # which hand within the round (0..N)
    "turn_index": 0,           # monotonic across the hand; +1 per draw or claim

    # Wall
    "wall": {
        "remaining": ["W3", "T7", ...],   # ordered; index 0 is next to draw
        "drawn_count": 14,                # how many have been drawn so far
        "total": 144,                     # constant; included for sanity checks
    },

    # Seats (always length 4, index = seat number)
    "seats": [
        {
            "seat": 0,
            "seat_wind": "F1",            # E/S/W/N as F1–F4; rotates per hand
            "concealed": ["W1", "W1", "B3", ...],   # multiset, sorted canonically
            "melds": [                     # ordered by call time
                {
                    "type": "PENG",         # PENG | CHI | GANG_CONCEALED | GANG_EXPOSED | GANG_ADDED
                    "tiles": ["B5", "B5", "B5"],
                    "called_tile": "B5",    # the tile that completed the meld (omitted for concealed kong)
                    "called_from_seat": 2,  # whose discard; self for concealed kong / added kong
                },
            ],
            "discards": ["W9", "F4", ...], # ordered; index 0 is first discard
            "flowers": ["H1", "H5"],       # bonus tiles set aside; do not count toward hand
            "score": 0,                    # running score for the hand
        },
        # … seats 1, 2, 3 same shape
    ],

    # The most recent discard (or null at hand start / after a claim resolves)
    "last_discard": {
        "tile": "B5",
        "seat": 2,
        "turn_index": 14,
    } | None,

    # Pending claims that may interrupt normal turn order
    "pending_claims": [
        # Set when last_discard is non-null and at least one seat could claim it.
        # Resolved in priority order: HU > PENG/GANG > CHI. Multiple seats
        # may have HU; CHI is only the next seat in turn order.
        {
            "seat": 0,
            "claim": "HU" | "PENG" | "GANG" | "CHI",
            "chi_tiles": ["B4", "B5", "B6"] | None,  # only for CHI; the three tiles forming the run
        },
    ],

    # Whose turn it is to act. Disambiguates "draw-and-discard" (current_actor's
    # own turn) from "claim window" (other seats deciding).
    "phase": "DEAL" | "DRAW" | "DISCARD" | "CLAIM_WINDOW" | "TERMINAL",
    "current_actor": 0,         # seat whose action is awaited; meaningful in DRAW/DISCARD phases

    # Terminal state (None until phase == TERMINAL)
    "terminal": {
        "kind": "HU" | "DRAW",        # win or exhaustive draw
        "winner": 2 | None,           # seat that won (None for exhaustive draw)
        "win_tile": "T8" | None,      # the tile that completed the win
        "win_type": "SELF_DRAW" | "DISCARD" | "ROBBED_KONG" | "LAST_TILE" | None,
        "deal_in_seat": 1 | None,     # seat whose discard was won on (None for self-draw)
        "fan": [                       # list of yaku names + values, from PyMahjongGB
            {"name": "All Pungs", "value": 6},
            {"name": "Half Flush", "value": 6},
        ],
        "fan_total": 12,
        "score_delta": [-12, -8, +28, -8],  # per-seat score change for this hand
    } | None,

    # RNG state — see determinism.md spec for full contract
    "rng": {
        "seed": 0xDEADBEEF,         # original seed for the hand
        "cursor": 17,               # how many bytes of the RNG stream have been consumed
    },
}
```

### Notes on specific fields

**`concealed` is sorted.** Canonical sort order is suit-then-rank (`W1 < W2 < … < W9 < B1 < … < T9 < F1 < … < F4 < J1 < J2 < J3`). Sorting is part of the canonical form: two states with the same multiset of concealed tiles must serialize identically. The engine sorts on every state construction; consumers should not assume order in `discards` (which is intentionally ordered by time).

**`melds` are ordered by call time.** This is observable information — opponents saw the order, so the record must too.

**`called_from_seat` for self-formed melds.** Concealed kong: `called_from_seat == self`. Added kong (`BUGANG`): `called_from_seat == self` (the player added to their own exposed pung). This keeps the field shape uniform; consumers that care about "did this come from an opponent" check `called_from_seat != seat`.

**`flowers` are separate from `concealed`.** Bonus tiles are scored at hand end (one fan per flower/season; matching seat-wind flower/season is worth extra). They're not part of the playable hand and never appear in legal-action enumeration.

**`pending_claims` is the queue that interrupts turn order.** When a discard happens, the engine populates `pending_claims` with every legal claim from every seat, sets `phase = "CLAIM_WINDOW"`, and the table manager prompts the relevant seats. Once all decisions are in, the engine resolves them in MCR priority order and transitions to the next phase. **Open question (see below):** should `pending_claims` carry the *decisions* (after seats respond) or just the *opportunities* (before)? Working answer: the state holds opportunities only; decisions are actions submitted back through `apply_action`.

**`phase` is the state-machine label.** Makes it impossible to ask `legal_actions(state, seat=X)` without the answer being well-defined for every (state, seat) pair. Phase determines what action grammar is even applicable:

- `DEAL` — engine is dealing; no seat actions accepted.
- `DRAW` — `current_actor` draws from the wall. Engine-driven (no seat decision unless flower replacement creates one).
- `DISCARD` — `current_actor` must `PLAY` a tile (or declare `HU` on self-draw, or `GANG` on a concealed/added kong).
- `CLAIM_WINDOW` — non-current seats may submit `PASS` / `PENG` / `CHI` / `GANG` / `HU` on `last_discard`. Engine waits for all relevant seats.
- `TERMINAL` — hand is over; no actions accepted.

**`rng.cursor` is the determinism hook.** See [determinism.md](determinism.md). The short version: `(seed, cursor)` is sufficient to reproduce all subsequent random draws, so the record can omit raw wall contents — the wall is regenerated from the seed and consumed deterministically.

## Per-seat projection

A bot or human player sees less than the canonical state. The projection is a pure function:

```python
def project(state: GameState, seat: int) -> SeatView: ...
```

Where `SeatView` is the same shape as `GameState` except:

- `seats[i].concealed` is replaced with a count (`{"count": 13}`) for every `i != seat`.
- `wall.remaining` is replaced with just `{"remaining_count": 70}` — order and contents are hidden.
- `rng` is omitted entirely.
- `pending_claims` is filtered to only include this seat's own opportunities.
- `terminal.fan` and `terminal.score_delta` are full (everyone sees the score).

The projection is what every adapter, bot, and overlay actually receives. The full canonical state is only seen by the engine, the table manager, the record store (writes the unprojected event log), and the self-play driver (which is allowed god-mode for training purposes — see [research-ideas.md](../research-ideas.md) for oracle-guiding, which deliberately uses this).

**Why a function, not two types:** keeping projection a function over a single type means there's exactly one source of truth for what a tile token looks like, what a meld looks like, what "phase" means. A separate `SeatView` type that drifted from `GameState` would be a bug factory.

## Worked example: state after a single discard

Hand just started, dealer (seat 0) drew W3 and discards it. The canonical state immediately after the discard, before the claim window resolves:

```json
{
  "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "sha256:abc..."},
  "round_wind": "F1",
  "dealer_seat": 0,
  "hand_index": 0,
  "turn_index": 1,
  "wall": {"remaining": ["...130 tiles..."], "drawn_count": 14, "total": 144},
  "seats": [
    {"seat": 0, "seat_wind": "F1", "concealed": ["B1","B2","...12 more..."],
     "melds": [], "discards": ["W3"], "flowers": [], "score": 0},
    {"seat": 1, "seat_wind": "F2", "concealed": ["...13..."],
     "melds": [], "discards": [], "flowers": [], "score": 0},
    {"seat": 2, "seat_wind": "F3", "concealed": ["...13..."],
     "melds": [], "discards": [], "flowers": [], "score": 0},
    {"seat": 3, "seat_wind": "F4", "concealed": ["...13..."],
     "melds": [], "discards": [], "flowers": [], "score": 0}
  ],
  "last_discard": {"tile": "W3", "seat": 0, "turn_index": 1},
  "pending_claims": [
    {"seat": 1, "claim": "CHI", "chi_tiles": ["W1","W2","W3"]},
    {"seat": 1, "claim": "CHI", "chi_tiles": ["W2","W3","W4"]},
    {"seat": 1, "claim": "CHI", "chi_tiles": ["W3","W4","W5"]}
  ],
  "phase": "CLAIM_WINDOW",
  "current_actor": 1,
  "terminal": null,
  "rng": {"seed": 305419896, "cursor": 56}
}
```

Note: only seat 1 had any legal claim (the example assumes no one had `PENG`/`HU` opportunities). `current_actor` advances to seat 1 because they are the next-in-turn fallback if no claim fires.

## Worked example: per-seat projection

The same state, projected for seat 2 (`project(state, 2)`):

```json
{
  "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "sha256:abc..."},
  "round_wind": "F1",
  "dealer_seat": 0,
  "hand_index": 0,
  "turn_index": 1,
  "wall": {"remaining_count": 130, "drawn_count": 14, "total": 144},
  "seats": [
    {"seat": 0, "seat_wind": "F1", "concealed": {"count": 13},
     "melds": [], "discards": ["W3"], "flowers": [], "score": 0},
    {"seat": 1, "seat_wind": "F2", "concealed": {"count": 13},
     "melds": [], "discards": [], "flowers": [], "score": 0},
    {"seat": 2, "seat_wind": "F3", "concealed": ["B1","B7","T2","..."],
     "melds": [], "discards": [], "flowers": [], "score": 0},
    {"seat": 3, "seat_wind": "F4", "concealed": {"count": 13},
     "melds": [], "discards": [], "flowers": [], "score": 0}
  ],
  "last_discard": {"tile": "W3", "seat": 0, "turn_index": 1},
  "pending_claims": [],
  "phase": "CLAIM_WINDOW",
  "current_actor": 1,
  "terminal": null
}
```

Seat 2 sees their own hand fully, sees only counts for others, sees the wall count but not contents, and sees an empty `pending_claims` because they had no opportunity on W3. The `rng` field is gone entirely.

## Action grammar

Actions are the only inputs to `apply_action`. Each is a small dict:

```python
{"type": "PASS"}
{"type": "PLAY",  "tile": "B5"}
{"type": "PENG",  "tile": "B5"}              # claim last_discard
{"type": "CHI",   "tiles": ["B4","B5","B6"]} # the three tiles forming the run, including the claim
{"type": "GANG",  "tile": "B5", "kind": "EXPOSED" | "CONCEALED" | "ADDED"}
{"type": "HU"}                                # win on last_discard or self-draw
```

`PLAY` is for own-turn discards. `PENG`/`CHI`/`GANG (EXPOSED)`/`HU (on discard)` are claim-window actions. `GANG (CONCEALED)` and `GANG (ADDED)` are own-turn actions during `DISCARD` phase. `HU` during `DISCARD` phase is self-draw.

Action grammar mirrors Botzone exactly. This is a deliberate choice (see Alternatives below) and the bot-runner protocol spec relies on it being a near-trivial mapping.

## Engine API surface

These three functions are the entire public API of the rules engine:

```python
def initial_state(ruleset: RuleSetRef, seed: int) -> GameState: ...
    # Deal hands, set phase = "DISCARD" (dealer draws as part of deal), return.

def legal_actions(state: GameState, seat: int) -> list[Action]: ...
    # The set of actions seat may legally submit right now. Empty if seat has
    # no decision to make in the current phase. Pure function; no side effects.

def apply_action(state: GameState, seat: int, action: Action) -> GameState: ...
    # Returns the new state after applying the action. Raises IllegalAction
    # if action not in legal_actions(state, seat). Pure; no I/O.
```

**No `step()` taking a dict of seat→action.** Each seat's action is applied individually. Claim resolution is internal: when the engine has received PASS-or-decision from every seat with an opportunity, it transitions phase. This keeps the engine API minimal and lets the table manager handle ordering / timeouts.

**No streaming events out of the engine.** The engine returns the new state; the table manager diffs the old and new states to produce the events that go into the record. This is the inversion that makes the engine pure: events are an *output projection*, not an internal log.

## Alternatives considered

**Tile encoding: integers vs. strings.**

- Considered: int 0–135 (Riichi convention) or a custom enum.
- Chose Botzone strings because every external tool (PyMahjongGB, botzone-mahjong-environment, the judge, every public dataset and replay) speaks them. A conversion layer would be (a) a maintenance burden, (b) a source of skew between training corpus parsing and serving. Performance is not the bottleneck at v1 scale.

**Mutable vs. immutable state.**

- Considered: mutate in place for performance.
- Chose immutable because the cost is unmeasurable at this scale and the benefits (trivial determinism testing, no aliasing bugs, free undo/replay) are large. Reconsider only if a profiled hot path proves it matters.

**Single canonical state vs. seat-view-as-primary.**

- Considered: each adapter sees only what it should see, with no "god view" anywhere.
- Chose canonical state with a projection function because (a) the engine *has to* see everything to enforce rules, (b) the record store has to see everything to write a replayable log, (c) the self-play driver wants god-view for training, and (d) one type with a filter is cheaper to maintain than two parallel types. Privacy is enforced at the projection boundary, not by hiding state from the engine.

**Pending claims as state vs. as ephemeral side-channel.**

- Considered: not putting `pending_claims` in the state — let the table manager manage the claim queue separately.
- Chose to put it in the state because (a) the state should be self-contained enough that a serialized state can be resumed from disk after a crash, (b) the engine's `legal_actions` needs to know what claims are open, (c) records need to capture claim windows for accurate replay (a discard with no claims is meaningfully different from a discard with three CHI options where everyone passed).

**`phase` as enum vs. derived.**

- Considered: deriving the phase from other fields (e.g., "if `last_discard` and unresolved claims → CLAIM_WINDOW").
- Chose explicit `phase` because the derivation rules are themselves non-trivial (terminal detection, draw-after-claim, exhaustive draw) and an enum field is one cheap source of truth instead of a derivation re-implemented in every consumer.

**Putting RNG state in the game state.**

- Considered: keeping RNG separate from state, threaded through engine calls as a second argument.
- Chose to include it because (a) the state is otherwise sufficient to resume a hand from disk; carrying RNG separately would mean the record needed to track it too, (b) it makes determinism testing literally `hash(state_n) == hash(replay_state_n)`, (c) the `rng.cursor` field lets us validate that two replays consumed the same amount of randomness, catching divergence before it cascades.

## Verification fixtures this spec implies

These are the fixtures S0 must produce to claim conformance with this spec. They live alongside the engine code, not in a global fixtures directory.

1. **Tile-token round-trip.** Every legal tile token serializes to itself; every Botzone-format example file we pull in parses to tokens matching this spec.
2. **Canonical-form invariance.** Two states constructed by different paths but representing the same game position serialize to byte-identical JSON. (Specifically: concealed tiles are sorted; meld order matches call order; phase is set correctly.)
3. **Projection privacy.** For every (state, seat) pair in the fixture suite, `project(state, seat)` contains zero tile tokens from other seats' concealed hands and zero tiles from the wall remaining-list. Automated check, not manual review.
4. **Projection reversibility for own seat.** For every state, `project(state, seat).seats[seat] == state.seats[seat]` (the projecting seat sees their own concealed hand unchanged).
5. **Phase transitions.** A table of (phase, action_type) → resulting_phase is checked-in; the engine's transitions match the table exhaustively.
6. **`legal_actions` ∩ `apply_action` consistency.** For every state in the fixture suite and every seat, every action returned by `legal_actions` succeeds when passed to `apply_action`; every action *not* returned raises `IllegalAction`.
7. **Initial-state determinism.** `initial_state(ruleset, seed)` is byte-identical across runs for the same `(ruleset, seed)` pair. Cross-platform check (Linux + macOS) because Python's RNG is platform-stable but we should prove it.
8. **Record-format round-trip with projection.** (Defers to record-format.md.) The record store's per-seat replay produces a sequence of `SeatView`s identical to `project(state_t, seat)` for each `t`.

## Open questions

- **Honors-and-knitted edge cases.** Some MCR yaku (Honors and Knitted Tiles, Knitted Straight variants) cross the suit boundary in ways that interact with concealed-hand canonicalization. Open question: does the sort order need a tiebreaker for visually-identical-but-semantically-different states? Working answer: no — sort by token string, accept that PyMahjongGB owns yaku-specific interpretation.
- **Multiple simultaneous HU claims.** MCR allows multiple winners on the same discard. The current `pending_claims` schema supports this (multiple `claim: "HU"` entries), but the score-split rules are ruleset-dependent. Defer the schema for `terminal` in that case until S5 (rule-set versioning) when we know which house rule we're locking in. Working answer: `terminal.winner` becomes `list[int]` and `score_delta` already accommodates per-seat deltas.
- **Restoring from a serialized state mid-hand.** The schema is self-contained enough to support this, but the *operational* semantics (which seat adapters get re-prompted? what's resent to clients?) belong in the seat-port and bot-runner specs. Flagged here so the schema doesn't accidentally drop a field that resumption needs.
- **Hand-end vs. round-end vs. match-end.** `terminal` covers hand-end. Round-end (dealer rotates) and match-end (E-round → S-round → … → N-round, or shorter formats) are higher-level state. Working answer: model them as table-manager state, not engine state — the engine's job ends at hand end and the table manager calls `initial_state` for the next hand with rotated `dealer_seat` and incremented `hand_index`.
