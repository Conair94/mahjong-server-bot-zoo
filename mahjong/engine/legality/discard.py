"""Legal actions during DISCARD phase (own-turn: PLAY, concealed/added GANG, self-draw HU).

Spec: docs/specs/state-schema.md § Action grammar, docs/specs/engine-api.md.
"""

from __future__ import annotations

from collections import Counter

from mahjong.engine import pymj
from mahjong.engine.types import Action, GameState, Meld


def discard_actions(state: GameState, seat: int) -> list[Action]:
    """Actions legal for `seat` during the DISCARD phase.

    Includes:
        PLAY one_of(distinct(concealed))
        GANG (CONCEALED) for each tile held in quantity 4 in concealed
        GANG (ADDED) for each concealed tile matching an existing PENG meld
        HU on self-draw if some decomposition reaches the MCR fan cliff
    """
    if seat != state["current_actor"]:
        return []

    seat_data = state["seats"][seat]
    concealed = list(seat_data["concealed"])
    counts = Counter(concealed)
    melds: list[Meld] = list(seat_data["melds"])

    actions: list[Action] = []

    for tile in sorted(counts):
        actions.append({"type": "PLAY", "tile": tile})

    for tile, n in sorted(counts.items()):
        if n == 4:
            actions.append({"type": "GANG", "tile": tile, "kind": "CONCEALED"})

    peng_tiles = sorted({m["tiles"][0] for m in melds if m["type"] == "PENG"})
    for tile in peng_tiles:
        if counts.get(tile, 0) >= 1:
            actions.append({"type": "GANG", "tile": tile, "kind": "ADDED"})

    if _self_draw_hu_legal(concealed, melds, seat_data["seat_wind"], state["round_wind"]):
        actions.append({"type": "HU"})

    return actions


def _self_draw_hu_legal(
    concealed: list[str], melds: list[Meld], seat_wind: str, round_wind: str
) -> bool:
    """True iff some tile in `concealed` can be treated as the just-drawn
    win tile to yield a calculate_fan result above the MCR cliff."""
    if len(concealed) % 3 != 2:
        return False
    tried: set[str] = set()
    for win_tile in concealed:
        if win_tile in tried:
            continue
        tried.add(win_tile)
        remaining = list(concealed)
        remaining.remove(win_tile)
        fans = pymj.calculate_fan(
            remaining,
            melds,
            win_tile,
            win_type="SELF_DRAW",
            seat_wind=seat_wind,
            round_wind=round_wind,
            ruleset_config={},
        )
        if fans:
            return True
    return False


__all__ = ["discard_actions"]
