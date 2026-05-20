"""Transition for PLAY (own-turn discard).

Spec: docs/specs/state-schema.md § Action grammar.
"""

from __future__ import annotations

from mahjong.engine.transition import clone_state, open_claim_window_or_advance
from mahjong.engine.types import Action, GameState


def apply_play(state: GameState, seat: int, action: Action) -> GameState:
    new = clone_state(state)
    tile = action["tile"]  # type: ignore[typeddict-item]
    seat_data = new["seats"][seat]
    seat_data["concealed"].remove(tile)
    seat_data["discards"].append(tile)
    new["turn_index"] += 1
    new["last_discard"] = {"tile": tile, "seat": seat, "turn_index": new["turn_index"]}
    return open_claim_window_or_advance(new)
