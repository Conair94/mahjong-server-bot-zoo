"""legal_actions, decomposed by phase.

Spec: docs/specs/engine-api.md § Internal submodule layout,
      docs/specs/state-schema.md § Action grammar.
"""

from __future__ import annotations

from mahjong.engine.legality.claim import claim_actions
from mahjong.engine.legality.discard import discard_actions
from mahjong.engine.types import Action, GameState


def legal_actions(state: GameState, seat: int) -> list[Action]:
    """Exhaustive list of actions `seat` may submit right now.

    Empty list if `seat` has no decision in the current phase. Pure;
    raises ValueError only for out-of-range `seat`.
    """
    if seat < 0 or seat >= 4:
        raise ValueError(f"seat must be in 0..3, got {seat!r}")

    phase = state["phase"]
    if phase == "DISCARD":
        return discard_actions(state, seat)
    if phase == "CLAIM_WINDOW":
        return claim_actions(state, seat)
    return []


__all__ = ["legal_actions"]
