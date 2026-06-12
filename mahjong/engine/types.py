"""GameState, SeatView, Action, RuleSetRef type definitions.

Spec: docs/specs/state-schema.md § Top-level state object, § Action grammar.

Design choice: **TypedDict, not dataclass.** The state IS JSON - the record
format is JSONL, the canonical hash takes a plain Python object and feeds it
to `json.dumps`. Wrapping the shape in a class would mean a conversion layer
on every record write and read, and conversion layers are a long-term cost
(see project memory: prefer-existing-standards).

The cost we pay: structural typing only. `dict[str, Any]` flows freely.
The mitigation: mypy strict on this module, plus runtime invariant checks
in `validate_state_invariants` for the properties that actually matter.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from mahjong.engine.errors import InvalidState
from mahjong.engine.hashing import canonical_hash
from mahjong.engine.tiles import Tile, tile_sort_key, validate_tile

# --- Ruleset reference ---


class RuleSetRef(TypedDict):
    """Reference to a versioned ruleset. Resolved by `rulesets.load_ruleset`."""

    id: str
    version: int
    config_hash: str  # sha256:<hex>


# --- Action grammar (state-schema.md § Action grammar) ---
#
# Mirrors Botzone exactly so the bot-runner adapter is a near-trivial mapping.
# Runtime shape is a plain dict; TypedDicts give mypy a static check.


class PassAction(TypedDict):
    type: Literal["PASS"]


class PlayAction(TypedDict):
    type: Literal["PLAY"]
    tile: Tile


class PengAction(TypedDict):
    type: Literal["PENG"]
    tile: Tile


class ChiAction(TypedDict):
    type: Literal["CHI"]
    tiles: list[Tile]  # the three tiles forming the run


class GangAction(TypedDict):
    type: Literal["GANG"]
    tile: Tile
    kind: Literal["EXPOSED", "CONCEALED", "ADDED"]


class HuAction(TypedDict):
    type: Literal["HU"]


Action = PassAction | PlayAction | PengAction | ChiAction | GangAction | HuAction


# --- Meld ---

MeldType = Literal["PENG", "CHI", "GANG_CONCEALED", "GANG_EXPOSED", "GANG_ADDED"]


class Meld(TypedDict):
    type: MeldType
    tiles: list[Tile]
    called_tile: NotRequired[Tile]
    called_from_seat: int


# --- Seat ---


class Seat(TypedDict):
    seat: int
    seat_wind: Tile
    concealed: list[Tile]
    melds: list[Meld]
    discards: list[Tile]
    flowers: list[Tile]
    score: int


class SeatViewOpponent(TypedDict):
    """Opponent seat as visible to a non-self projection: counts only."""

    seat: int
    seat_wind: Tile
    concealed: dict[str, int]
    melds: list[Meld]
    discards: list[Tile]
    flowers: list[Tile]
    score: int


# --- Wall ---


class Wall(TypedDict):
    remaining: list[Tile]
    drawn_count: int
    total: int


class WallView(TypedDict):
    """Wall as visible in a projection: counts only, no contents."""

    remaining_count: int
    drawn_count: int
    total: int


# --- Discard / claim / terminal ---


class LastDiscard(TypedDict):
    tile: Tile
    seat: int
    turn_index: int


class LastDrawn(TypedDict):
    """The most recent tile drawn from the wall and the seat that drew it.

    Cleared (set to `None`) when an actor takes a tile from the discard pile
    via CHI/PENG/GANG, and at TERMINAL. See state-schema.md for the full
    contract on what derives from this field (tsumogiri detection,
    self-draw HU win_tile selection, LAST_TILE/ROBBED_KONG gating).
    """

    seat: int
    tile: Tile


ClaimType = Literal["HU", "PENG", "GANG", "CHI"]


class PendingClaim(TypedDict):
    seat: int
    claim: ClaimType
    chi_tiles: NotRequired[list[Tile]]


WinType = Literal["SELF_DRAW", "DISCARD", "ROBBED_KONG", "LAST_TILE"]
TerminalKind = Literal["HU", "DRAW"]


class FanEntry(TypedDict):
    name: str
    value: int


class Terminal(TypedDict):
    kind: TerminalKind
    winner: int | None
    win_tile: Tile | None
    win_type: WinType | None
    deal_in_seat: int | None
    fan: list[FanEntry]
    fan_total: int
    score_delta: list[int]


class RngState(TypedDict):
    """Determinism hook. `seed` is serialized as a decimal string in JSON
    to avoid 64-bit overflow risk in downstream consumers (determinism.md).
    """

    seed: str
    cursor: int


Phase = Literal["DEAL", "DRAW", "DISCARD", "CLAIM_WINDOW", "TERMINAL"]


class GameState(TypedDict):
    ruleset: RuleSetRef
    round_wind: Tile
    dealer_seat: int
    hand_index: int
    turn_index: int
    wall: Wall
    seats: list[Seat]
    last_discard: LastDiscard | None
    last_drawn: LastDrawn | None
    pending_claims: list[PendingClaim]
    phase: Phase
    current_actor: int
    terminal: Terminal | None
    rng: RngState


class FinalHand(TypedDict):
    """One seat's settlement reveal inside `terminal.final_hands` (FB-17).

    Identical shape to the HAND_END record event's `final_hands` entries —
    both are built by `state.final_hands_view` so they cannot drift.
    """

    seat: int
    concealed: list[Tile]
    melds: list[Meld]
    flowers: list[Tile]


class SeatView(TypedDict):
    """Per-seat projection (state-schema.md § Per-seat projection).

    Same shape as GameState minus `rng`, with `wall` replaced by `WallView`
    and opponent seats' `concealed` collapsed to a count.

    FB-17 additions so a reconnect snapshot is self-sufficient:
    - `last_drawn` (per-seat views only; tile redacted unless own) — the
      public/spectator view omits the key entirely.
    - at TERMINAL, `terminal` additionally carries `final_hands` (the
      settlement reveal; see `FinalHand`).
    """

    ruleset: RuleSetRef
    round_wind: Tile
    dealer_seat: int
    hand_index: int
    turn_index: int
    wall: WallView
    seats: list[Seat | SeatViewOpponent]
    last_discard: LastDiscard | None
    last_drawn: NotRequired[LastDrawn | None]
    pending_claims: list[PendingClaim]
    phase: Phase
    current_actor: int
    terminal: Terminal | None


# --- Runtime invariant checks ---


def validate_state_invariants(state: dict[str, Any]) -> None:
    """Raise `InvalidState` if `state` violates a canonical-form invariant.

    Today this checks only the concealed-sorted invariant. Additional
    invariants land here as they get tests - per CLAUDE.md scope discipline,
    we don't pre-build checks the tests don't require.
    """
    for seat_dict in state.get("seats", []):
        seat = seat_dict.get("seat")
        concealed = seat_dict.get("concealed", [])
        if not isinstance(concealed, list):
            continue
        _check_concealed_sorted(state, seat, concealed)


def _check_concealed_sorted(state: dict[str, Any], seat: int | None, concealed: list[Tile]) -> None:
    prev_key: tuple[int, int] | None = None
    for tile in concealed:
        if not validate_tile(tile):
            raise InvalidState(
                state_hash=canonical_hash(state),
                invariant_name="concealed_sorted",
                detail=f"seat {seat}: invalid tile token {tile!r} in concealed",
            )
        key = tile_sort_key(tile)
        if prev_key is not None and key < prev_key:
            raise InvalidState(
                state_hash=canonical_hash(state),
                invariant_name="concealed_sorted",
                detail=f"seat {seat}: concealed not in canonical order at {tile!r}",
            )
        prev_key = key
