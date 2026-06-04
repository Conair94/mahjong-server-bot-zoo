"""Legal actions during CLAIM_WINDOW (PASS, PENG, CHI next-seat-only, exposed GANG, HU on discard).

Spec: docs/specs/state-schema.md § Action grammar, docs/specs/engine-api.md.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mahjong.engine import pymj
from mahjong.engine.rulesets import resolve_config
from mahjong.engine.tiles import tile_sort_key
from mahjong.engine.types import Action, GameState, Meld

_SUITED_PREFIXES = {"W", "B", "T"}


def claim_actions(state: GameState, seat: int) -> list[Action]:
    """Actions `seat` may submit during the CLAIM_WINDOW.

    The discarder gets no actions. All other seats get at minimum a PASS;
    PENG/CHI/GANG/HU are added per legality.
    """
    last = state["last_discard"]
    if last is None:
        return []
    if seat == last["seat"]:
        return []

    seat_data = state["seats"][seat]
    concealed = list(seat_data["concealed"])
    counts = Counter(concealed)
    discarded = last["tile"]
    melds: list[Meld] = list(seat_data["melds"])

    actions: list[Action] = [{"type": "PASS"}]

    # PENG: two copies in hand.
    if counts.get(discarded, 0) >= 2:
        actions.append({"type": "PENG", "tile": discarded})

    # GANG (EXPOSED): three copies in hand.
    if counts.get(discarded, 0) >= 3:
        actions.append({"type": "GANG", "tile": discarded, "kind": "EXPOSED"})

    # CHI: next-seat only, suited tiles, within rank bounds.
    if seat == (last["seat"] + 1) % 4:
        actions.extend(_chi_actions(concealed, discarded))

    # HU on discard: (concealed + discarded) yields a fan-bearing decomposition.
    config = resolve_config(state["ruleset"])
    if _claim_hu_legal(
        concealed, melds, discarded, seat_data["seat_wind"], state["round_wind"], config
    ):
        actions.append({"type": "HU"})

    return actions


def _chi_actions(concealed: list[str], discarded: str) -> list[Action]:
    if discarded[0] not in _SUITED_PREFIXES:
        return []
    prefix = discarded[0]
    rank = int(discarded[1])
    hand_counts = Counter(t for t in concealed if t.startswith(prefix))
    out: list[Action] = []
    # Three possible runs containing `discarded`: positions [-2,-1,0], [-1,0,+1], [0,+1,+2].
    for offset in (-2, -1, 0):
        ranks = (rank + offset, rank + offset + 1, rank + offset + 2)
        if any(r < 1 or r > 9 for r in ranks):
            continue
        tiles = [f"{prefix}{r}" for r in ranks]
        needed = [t for t in tiles if t != discarded]
        if all(hand_counts.get(t, 0) >= needed.count(t) for t in set(needed)):
            run = sorted(tiles, key=tile_sort_key)
            out.append({"type": "CHI", "tiles": run})
    return out


def _claim_hu_legal(
    concealed: list[str],
    melds: list[Meld],
    win_tile: str,
    seat_wind: str,
    round_wind: str,
    config: dict[str, Any],
) -> bool:
    """True iff (concealed + win_tile) clears the ruleset's fan cliff."""
    if len(concealed) % 3 != 1:
        # After adding win_tile we want len ≡ 2 (mod 3); concealed must be ≡ 1.
        return False
    fans = pymj.calculate_fan(
        concealed,
        melds,
        win_tile,
        win_type="DISCARD",
        seat_wind=seat_wind,
        round_wind=round_wind,
        ruleset_config=config,
    )
    return bool(fans)


__all__ = ["claim_actions"]
