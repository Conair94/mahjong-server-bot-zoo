"""Transition for PASS (claim-window decline).

Spec: docs/specs/state-schema.md § Action grammar.

Removes only the passing seat's opportunities from `pending_claims`. When
all opportunities are cleared, the discard becomes final and the next seat
draws.
"""

from __future__ import annotations

from mahjong.engine.transition import advance_to_next_seat_discard, clone_state
from mahjong.engine.types import GameState


def apply_pass(state: GameState, seat: int) -> GameState:
    new = clone_state(state)
    new["pending_claims"] = [c for c in new["pending_claims"] if c["seat"] != seat]
    if not new["pending_claims"]:
        return advance_to_next_seat_discard(new)
    return new
