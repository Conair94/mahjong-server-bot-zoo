"""State construction, invariant checks, and the project() seat-view filter.

Spec: docs/specs/state-schema.md § Top-level state object, § Per-seat projection,
      docs/specs/engine-api.md § Public API.

Dealing convention (locked by the golden fixture, not specified by the spec):
    1. Shuffle 144-tile canonical set via `shuffled_wall(seed)`.
    2. Sequentially deal 13 tiles to seats 0,1,2,3 from wall front; dealer
       takes one more (= 14). Any H* tile drawn goes to that seat's `flowers`
       and is immediately replaced from the wall front until non-flower.
    3. Sort each seat's `concealed` canonically.
    4. `phase = "DISCARD"`, `current_actor = dealer_seat = 0`, `turn_index = 0`.

A change to this convention is a determinism contract break — refactor
protocol (determinism.md) applies.
"""

from __future__ import annotations

from typing import Any, cast

from mahjong.engine.hashing import canonical_hash
from mahjong.engine.rng import shuffled_wall
from mahjong.engine.rulesets import load_ruleset
from mahjong.engine.tiles import Tile, tile_sort_key
from mahjong.engine.types import (
    GameState,
    PendingClaim,
    RuleSetRef,
    Seat,
    SeatView,
    SeatViewOpponent,
    WallView,
)

_SEED_MAX = 1 << 128


def initial_state(
    ruleset: RuleSetRef,
    seed: int,
    *,
    dealer_seat: int = 0,
    hand_index: int = 0,
) -> GameState:
    """Deal a fresh hand. See module docstring for the dealing convention.

    Layer-8 amendment: ``dealer_seat`` (0-3) designates the current dealer.
    The dealer receives the 14th tile and starts the discard phase.  Seat
    winds rotate with the dealer: `dealer_seat` gets East (F1), the seat to
    its left gets South (F2), etc.  Defaults to 0 for backwards compatibility
    with all existing callers.

    ``hand_index`` is stored in the state as metadata (used by the multi-hand
    orchestrator).  Defaults to 0.

    Raises `ValueError` if `seed` or `dealer_seat` is out of range, or if
    `RulesetError` can't resolve the ruleset reference.
    """
    if seed < 0 or seed >= _SEED_MAX:
        raise ValueError(f"seed must be a 128-bit unsigned integer, got {seed!r}")
    if dealer_seat < 0 or dealer_seat >= 4:
        raise ValueError(f"dealer_seat must be 0-3, got {dealer_seat!r}")

    config = load_ruleset(dict(ruleset))
    config_hash = canonical_hash(config)

    wall, cursor = shuffled_wall(seed)

    flowers: list[list[Tile]] = [[], [], [], []]
    concealed: list[list[Tile]] = [[], [], [], []]
    wall_pos = 0
    dealer_last_drawn: Tile | None = None

    def draw_one(seat_idx: int) -> Tile:
        nonlocal wall_pos
        while True:
            tile = wall[wall_pos]
            wall_pos += 1
            if tile.startswith("H"):
                flowers[seat_idx].append(tile)
                continue
            concealed[seat_idx].append(tile)
            return tile

    for _ in range(13):
        for s in range(4):
            draw_one(s)
    dealer_last_drawn = draw_one(dealer_seat)  # dealer's first draw

    for s in range(4):
        concealed[s].sort(key=tile_sort_key)

    # Seat winds rotate with the dealer: dealer gets East (F1), next seat gets
    # South (F2), etc.  Wind index = (seat - dealer_seat) % 4, 1-based.
    seats: list[Seat] = [
        {
            "seat": s,
            "seat_wind": f"F{(s - dealer_seat) % 4 + 1}",
            "concealed": concealed[s],
            "melds": [],
            "discards": [],
            "flowers": flowers[s],
            "score": 0,
        }
        for s in range(4)
    ]

    state: GameState = {
        "ruleset": {
            "id": ruleset["id"],
            "version": ruleset["version"],
            "config_hash": config_hash,
        },
        "round_wind": "F1",
        "dealer_seat": dealer_seat,
        "hand_index": hand_index,
        "turn_index": 0,
        "wall": {
            "remaining": wall[wall_pos:],
            "drawn_count": wall_pos,
            "total": 144,
        },
        "seats": seats,
        "last_discard": None,
        "last_drawn": {"seat": dealer_seat, "tile": dealer_last_drawn},
        "pending_claims": [],
        "phase": "DISCARD",
        "current_actor": dealer_seat,
        "terminal": None,
        "rng": {"seed": str(seed), "cursor": cursor},
    }
    return state


def project(state: GameState, seat: int | None) -> SeatView:
    """Privacy-filtered view of `state` for `seat`.

    `seat` may be `None` for the public (spectator) projection: every seat's
    `concealed` is reduced to a count and `pending_claims` is empty.

    Spec: state-schema.md § Per-seat projection, § Public (spectator) projection.
    """
    if seat is not None and (seat < 0 or seat >= 4):
        raise ValueError(f"seat must be in 0..3 or None, got {seat!r}")

    seats_view: list[Seat | SeatViewOpponent] = []
    for i, s in enumerate(state["seats"]):
        if seat is not None and i == seat:
            own: Seat = {
                "seat": s["seat"],
                "seat_wind": s["seat_wind"],
                "concealed": list(s["concealed"]),
                "melds": [dict(m) for m in s["melds"]],  # type: ignore[misc]
                "discards": list(s["discards"]),
                "flowers": list(s["flowers"]),
                "score": s["score"],
            }
            seats_view.append(own)
        else:
            opponent: SeatViewOpponent = {
                "seat": s["seat"],
                "seat_wind": s["seat_wind"],
                "concealed": {"count": len(s["concealed"])},
                "melds": [dict(m) for m in s["melds"]],  # type: ignore[misc]
                "discards": list(s["discards"]),
                "flowers": list(s["flowers"]),
                "score": s["score"],
            }
            seats_view.append(opponent)

    wall_view: WallView = {
        "remaining_count": len(state["wall"]["remaining"]),
        "drawn_count": state["wall"]["drawn_count"],
        "total": state["wall"]["total"],
    }

    own_claims: list[PendingClaim] = (
        []
        if seat is None
        else [cast(PendingClaim, dict(c)) for c in state["pending_claims"] if c["seat"] == seat]
    )

    view: SeatView = {
        "ruleset": cast(RuleSetRef, dict(state["ruleset"])),
        "round_wind": state["round_wind"],
        "dealer_seat": state["dealer_seat"],
        "hand_index": state["hand_index"],
        "turn_index": state["turn_index"],
        "wall": wall_view,
        "seats": seats_view,
        "last_discard": (
            cast("Any", dict(state["last_discard"])) if state["last_discard"] is not None else None
        ),
        "pending_claims": own_claims,
        "phase": state["phase"],
        "current_actor": state["current_actor"],
        "terminal": (
            cast("Any", dict(state["terminal"])) if state["terminal"] is not None else None
        ),
    }
    return view


def project_event(event: dict[str, Any], seat: int | None) -> dict[str, Any]:
    """Privacy-filtered view of a record event for `seat`.

    `seat=None` is the public (spectator) projection. The DRAW event's `tile`
    field is private to the drawing seat; all other event kinds are public.
    Pure: returns a fresh dict; never mutates the input.

    Spec: state-schema.md § Per-event projection.
    """
    if event.get("event") == "DRAW" and (seat is None or seat != event.get("seat")):
        projected = dict(event)
        projected.pop("tile", None)
        return projected
    return dict(event)


def is_terminal(state: GameState) -> bool:
    """`state.phase == 'TERMINAL'`."""
    return state["phase"] == "TERMINAL"


def state_hash(state: GameState) -> str:
    """Canonical hash of `state` (re-export of `canonical_hash`)."""
    return canonical_hash(cast(dict[str, Any], state))
