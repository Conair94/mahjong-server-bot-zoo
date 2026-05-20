"""Transitions for PENG and CHI (claim-window meld formation).

Spec: docs/specs/state-schema.md § Action grammar.

PENG / CHI / GANG (EXPOSED) share a pattern: claim the last discard, move
2-3 tiles from concealed into a new meld, advance the claimer to DISCARD.
GANG_EXPOSED lives in `gang.py` to keep all the gang variants together.
"""

from __future__ import annotations

from mahjong.engine.transition import clone_state
from mahjong.engine.types import Action, GameState


def apply_peng(state: GameState, seat: int, action: Action) -> GameState:
    new = clone_state(state)
    tile = action["tile"]  # type: ignore[typeddict-item]
    last = new["last_discard"]
    assert last is not None  # legal_actions guards this
    discarder = last["seat"]

    seat_data = new["seats"][seat]
    seat_data["concealed"].remove(tile)
    seat_data["concealed"].remove(tile)
    seat_data["melds"].append(
        {
            "type": "PENG",
            "tiles": [tile, tile, tile],
            "called_tile": tile,
            "called_from_seat": discarder,
        }
    )

    _consume_discard(new, discarder)
    _hand_to_claimer(new, seat)
    return new


def apply_chi(state: GameState, seat: int, action: Action) -> GameState:
    new = clone_state(state)
    tiles: list[str] = list(action["tiles"])  # type: ignore[typeddict-item]
    last = new["last_discard"]
    assert last is not None
    discarder = last["seat"]
    called_tile = last["tile"]

    seat_data = new["seats"][seat]
    for t in tiles:
        if t == called_tile:
            continue
        seat_data["concealed"].remove(t)
    seat_data["melds"].append(
        {
            "type": "CHI",
            "tiles": list(tiles),
            "called_tile": called_tile,
            "called_from_seat": discarder,
        }
    )

    _consume_discard(new, discarder)
    _hand_to_claimer(new, seat)
    return new


def _consume_discard(state: GameState, discarder: int) -> None:
    """Remove the just-claimed tile from the discarder's discard pile."""
    state["seats"][discarder]["discards"].pop()
    state["last_discard"] = None
    state["pending_claims"] = []


def _hand_to_claimer(state: GameState, claimer: int) -> None:
    state["phase"] = "DISCARD"
    state["current_actor"] = claimer
    state["turn_index"] += 1
