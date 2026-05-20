"""Transition for GANG (EXPOSED / CONCEALED / ADDED).

Spec: docs/specs/state-schema.md § Action grammar.

All three variants culminate in a 4-tile meld and a replacement draw from
the wall front (the claimer's hand size is preserved). The acting seat
remains in DISCARD phase after the replacement draw.
"""

from __future__ import annotations

from mahjong.engine.transition import clone_state, internal_draw
from mahjong.engine.types import Action, GameState


def apply_gang(state: GameState, seat: int, action: Action) -> GameState:
    kind = action["kind"]  # type: ignore[typeddict-item]
    tile = action["tile"]  # type: ignore[typeddict-item]
    if kind == "EXPOSED":
        return _gang_exposed(state, seat, tile)
    if kind == "CONCEALED":
        return _gang_concealed(state, seat, tile)
    if kind == "ADDED":
        return _gang_added(state, seat, tile)
    raise ValueError(f"unknown GANG kind: {kind!r}")


def _gang_exposed(state: GameState, seat: int, tile: str) -> GameState:
    new = clone_state(state)
    last = new["last_discard"]
    assert last is not None
    discarder = last["seat"]
    seat_data = new["seats"][seat]
    for _ in range(3):
        seat_data["concealed"].remove(tile)
    seat_data["melds"].append(
        {
            "type": "GANG_EXPOSED",
            "tiles": [tile] * 4,
            "called_tile": tile,
            "called_from_seat": discarder,
        }
    )
    new["seats"][discarder]["discards"].pop()
    new["last_discard"] = None
    new["pending_claims"] = []
    new["turn_index"] += 1
    return internal_draw(new, seat)


def _gang_concealed(state: GameState, seat: int, tile: str) -> GameState:
    new = clone_state(state)
    seat_data = new["seats"][seat]
    for _ in range(4):
        seat_data["concealed"].remove(tile)
    seat_data["melds"].append(
        {
            "type": "GANG_CONCEALED",
            "tiles": [tile] * 4,
            "called_from_seat": seat,
        }
    )
    new["turn_index"] += 1
    return internal_draw(new, seat)


def _gang_added(state: GameState, seat: int, tile: str) -> GameState:
    new = clone_state(state)
    seat_data = new["seats"][seat]
    seat_data["concealed"].remove(tile)
    promoted = False
    for meld in seat_data["melds"]:
        if meld["type"] == "PENG" and meld["tiles"][0] == tile:
            # Preserve provenance (called_from_seat) of the original PENG.
            meld["type"] = "GANG_ADDED"
            meld["tiles"] = [tile] * 4
            promoted = True
            break
    assert promoted, "GANG_ADDED legality implies a matching PENG meld"
    new["turn_index"] += 1
    return internal_draw(new, seat)
