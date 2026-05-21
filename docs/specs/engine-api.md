# Spec 6 — Rules engine API surface

The public interface of the rules engine. Most of the design is already locked by [state-schema.md](state-schema.md) — three pure functions (`initial_state`, `legal_actions`, `apply_action`) over a `GameState` value object. This spec pins the *surface* around those functions: full signatures, exception types, the PyMahjongGB integration boundary, the pure-function discipline, and the engine's internal submodule layout.

**Tier:** 2. Mostly affects one consumer (the table manager). Speccing it because the PyMahjongGB boundary and the exception taxonomy will be tedious to refactor once dozens of fixtures are pinned against them.

**Status:** draft, pre-S0.

## Goals

- **Minimal public surface.** Three functions plus a small set of helpers. Everything else is internal.
- **Pure functions only.** No I/O, no globals, no time, no logging from inside the engine. A caller can run `legal_actions(state, seat)` ten thousand times in a tight loop with no side effects.
- **One PyMahjongGB seam.** Exactly one module wraps PyMahjongGB. The rest of the engine doesn't import it. Swapping wrappers (or stubbing for tests) is one file's worth of work.
- **Exceptions are typed and few.** Three exception classes cover every failure path. Each carries enough context to debug from the traceback alone.

## Non-goals

- **Not a game UI.** The engine produces states; it doesn't render them.
- **Not an event emitter.** The engine returns the new state; the table manager diffs old → new to produce record events. (Already committed in [state-schema.md](state-schema.md).)
- **Not opinionated about persistence.** The engine doesn't read or write files.
- **No async.** Engine functions are synchronous. Concurrency lives in the table manager.

## Public API

The full surface, in one place:

```python
# mahjong.engine

# --- Construction ---

def initial_state(ruleset: RuleSetRef, seed: int) -> GameState:
    """Deal a fresh hand. Returns the canonical state at the moment dealer
    must take their first action (phase == 'DISCARD' for dealer's turn).
    Raises ValueError if seed is missing or ruleset is unknown."""

# --- Queries (pure; no state change) ---

def legal_actions(state: GameState, seat: int) -> list[Action]:
    """Exhaustive list of actions seat may submit right now. Empty list if
    seat has no decision in the current phase. Never raises for in-range
    seats."""

def project(state: GameState, seat: int) -> SeatView:
    """Privacy-filtered state for seat's perspective. Pure data filter."""

def is_terminal(state: GameState) -> bool:
    """Convenience: state.phase == 'TERMINAL'."""

def state_hash(state: GameState) -> str:
    """Canonical hash per determinism.md. Convenience re-export."""

# --- Transition (pure; returns new state) ---

def apply_action(state: GameState, seat: int, action: Action) -> GameState:
    """Returns the new state after applying the action. Raises IllegalAction
    if the action is not in legal_actions(state, seat). Raises InvalidState
    if the input state is malformed (shouldn't happen for engine-produced
    states; catches caller bugs)."""

# --- Exception types ---

class EngineError(Exception):
    """Base class. Never raised directly; catch this in callers that want
    to be defensive about any engine failure."""

class IllegalAction(EngineError):
    """Caller submitted an action not in legal_actions(state, seat).
    Carries: state_hash, seat, attempted_action, legal_actions (the list
    that was actually legal)."""

class InvalidState(EngineError):
    """The input state failed an invariant check. Should never happen for
    states the engine itself produced. Carries: state_hash, invariant_name,
    detail. Indicates a bug in either the caller or the engine."""

class RulesetError(EngineError):
    """Ruleset reference couldn't be resolved (unknown id, config_hash
    mismatch, malformed config). Carries: ruleset_ref, detail."""
```

That's the complete external surface. **Six functions and three exception types.** Everything else in the `mahjong.engine` package is internal.

### What's deliberately not in the public API

- **No `step(actions: dict)`.** Already rejected in state-schema.md. The table manager applies actions per-seat.
- **No `legal_actions_for_all_seats(state)`.** Equivalent to four `legal_actions` calls; not worth a separate function.
- **No `diff_states(old, new) -> list[RecordEvent]`.** That belongs in the record store, not the engine — the engine knows nothing about record events.
- **No `undo(state)`.** The engine is forward-only; "undo" means replay from `initial_state`.
- **No `clone(state)`.** States are immutable; aliasing is safe.
- **No `set_logger(...)`.** Engine doesn't log.

## Pure-function discipline

The engine module is held to a stricter standard than the rest of the codebase:

- **No I/O.** No `open()`, no `print()`, no `requests`, no subprocess. Engine code that needs to fail does so by raising; the caller decides what to do with the failure.
- **No global mutable state.** No module-level `_cache = {}` that grows. Memoization that's worth doing happens behind a `functools.cache` decorator on a pure function, where the cache key is the full argument tuple — never module-level dict mutation.
- **No clocks.** `time.time()`, `datetime.now()`, `monotonic()` are all banned. Determinism depends on this; record `ts` fields are stamped by the table manager, not the engine.
- **No RNG except the canonical DRBG.** Already pinned in [determinism.md](determinism.md): `random` and `numpy.random` imports are statically forbidden in `mahjong.engine.*`.
- **No `logging`.** Logger calls are I/O. The engine returns; the caller logs.

Enforced by lint, not convention. The S0 verification ladder includes an AST-based linter pass over `mahjong.engine.*` that flags any of the above.

## PyMahjongGB integration boundary

PyMahjongGB is imported in exactly one module: `mahjong.engine.pymj`. The rest of the engine talks to it via the wrapper.

```python
# mahjong.engine.pymj

def calculate_fan(
    hand: ConcealedHand,
    melds: list[Meld],
    win_tile: str,
    *,
    win_type: WinType,
    seat_wind: str,
    round_wind: str,
    ruleset_config: dict,
) -> list[FanEntry]:
    """Returns the list of yaku and their values for a winning hand.
    Wraps PyMahjongGB's MahjongFanCalculator. Returns [] for hands that
    don't reach the 8-fan minimum (caller is responsible for the cliff)."""

def shanten(hand: ConcealedHand, melds: list[Meld]) -> int:
    """Number of tile swaps to reach tenpai. 0 means tenpai. Wraps
    PyMahjongGB's MahjongShanten, which requires a standing-position
    hand (len(hand) + 3*len(melds) == 13). To test 'is this a winning
    hand', use `winning_tiles` or `calculate_fan` instead — they handle
    the 14-tile case explicitly."""

def shanten_specialized(hand: ConcealedHand, variant: ShantenVariant) -> int:
    """Shanten for Seven Pairs / Thirteen Orphans / Honors-and-Knitted
    /Knitted-Straight variants."""

def winning_tiles(hand: ConcealedHand, melds: list[Meld]) -> list[str]:
    """Tiles that would complete this hand into a winning shape (any
    fan count; the 8-fan filter is separate). Used by the deal-in risk
    overlay and by legal_actions for HU detection."""
```

That's the boundary. **Any future engine logic that needs to compute fan, shanten, or winning tiles goes through this module.** Direct imports of `MahjongGB` from elsewhere in `mahjong.engine.*` are a lint failure.

**Why this matters:**

1. **One place to handle PyMahjongGB version skew.** If the library's API changes between releases, the fix is in `pymj.py` and nowhere else.
2. **One place to stub for tests.** Unit tests that don't care about real fan calculation can monkey-patch `mahjong.engine.pymj.calculate_fan`; the engine code under test is unaware.
3. **One place to discover gaps.** If we ever hit an edge case PyMahjongGB doesn't cover (the AI plan flags this risk), the fallback layer is added here, not scattered.
4. **One place to add caching.** Fan calculation on a stable hand is deterministic; `@functools.cache` on the wrapper buys speedup if profiling ever asks for it.

## Internal submodule layout

The engine package's internal layout. **Internal modules are not import targets for code outside `mahjong.engine.*`.**

```
mahjong/
  engine/
    __init__.py            # re-exports the public API listed above
    types.py               # GameState, SeatView, Action, RuleSetRef type definitions
    tiles.py               # canonical tile set, tile-token validation, sort order
    state.py               # state construction, invariant checks, project()
    pymj.py                # PyMahjongGB wrapper (the one allowed import)
    rng.py                 # DRBG, shuffled_wall, uniform_int (per determinism.md)
    rulesets/              # bundled ruleset configs (per determinism.md open question)
      __init__.py
      mcr-2006.json
      MANIFEST.json
    legality/              # legal_actions, decomposed by phase
      __init__.py
      discard.py           # legality during DISCARD phase (own-turn actions)
      claim.py             # legality during CLAIM_WINDOW (PENG/CHI/GANG/HU on discard)
    transition/            # apply_action, decomposed by action type
      __init__.py
      play.py              # PLAY (discard from hand)
      claim.py             # PENG/CHI/GANG (exposed) — claim consumes last_discard
      gang.py              # GANG (concealed/added) — own-turn kong
      hu.py                # HU — terminal transition; calls pymj.calculate_fan
      pass_.py             # PASS — claim-window decline
      # internal_draw + claim-window opening live in transition/__init__.py
      # as shared helpers (engine-internal; no caller surface)
    errors.py              # EngineError, IllegalAction, InvalidState, RulesetError
```

A few notes on the decomposition:

- **`types.py` has no logic.** Just `TypedDict`s and dataclasses with field validators.
- **`legality/` and `transition/` are sibling concerns.** For every action type, there's a legality check and a transition. Keeping them parallel makes it easy to grep for "everything about CHI" — it's in `legality/claim.py` and `transition/claim.py`.
- **Wall draw is engine-internal**, not caller-facing. The `internal_draw` helper lives in `transition/__init__.py` and is invoked by transitions that need to pull a fresh tile (after a PLAY's auto-advance, after a GANG's replacement draw). The caller's view of "draw a tile" is `apply_action(state, seat, PLAY)` — the engine pulls the new draw inside that transition.
- **`rulesets/` ships as data**, not code. The `MANIFEST.json` maps human-readable IDs (`mcr-2006`) to the current `config_hash`; the per-config JSON files are immutable once shipped (per [determinism.md](determinism.md)).

## Verification fixtures this spec implies

Mostly inherited from the Tier 1 specs; this section just enumerates the engine-specific additions.

1. **Pure-function discipline lint.** AST check that no module under `mahjong.engine.*` imports `random`, `numpy.random`, `time`, `datetime`, `logging`, `requests`, or calls `open()` / `print()`. Run pre-commit.
2. **PyMahjongGB-boundary lint.** AST check that no module under `mahjong.engine.*` except `pymj.py` imports `MahjongGB` (or the package's actual import name). Run pre-commit.
3. **No-clone-needed.** A test that mutating a returned `GameState`'s mutable substructures (lists in `discards`, etc.) and re-running `apply_action` on the original produces the same result. Catches accidental shared-reference bugs.
4. **`pymj` wrapper round-trip.** For each of the six wrapper functions, a checked-in `(inputs → expected outputs)` table covering at least one MCR-canonical example. Catches PyMahjongGB version drift on upgrade.
5. **Exception payload completeness.** Every raised `IllegalAction` carries non-empty `state_hash`, `seat`, `attempted_action`, `legal_actions`. Every raised `InvalidState` carries non-empty `state_hash`, `invariant_name`, `detail`. A test parses the exception and asserts the fields.
6. **Internal-module import enforcement.** A test that grep-asserts no file outside `mahjong/engine/` imports from `mahjong.engine.legality.*`, `mahjong.engine.transition.*`, `mahjong.engine.pymj`, etc. — only the re-exported public API.

## Open questions

- **Memoization of `legal_actions`.** Same `(state, seat)` input always gives same output. Worth `@functools.cache`? Working answer: yes, but only after profiling proves it matters; the cache key is the canonical hash of the state, not the state itself. Defer.
- **`apply_action` returning a delta alongside the new state.** Some callers (record-event emitter) need to know "what changed." Working answer: keep `apply_action` returning just the new state; the diff is the table manager's job (already committed in seat-port.md). Reconsider if the diff cost becomes meaningful.
- **`legal_actions` shape for claim windows.** Currently returns a flat `list[Action]` per seat. A claim window has multiple seats with opportunities; the table manager makes four calls. Worth a batch function? Working answer: no — four calls is fine, batching would just hide the per-seat structure.
