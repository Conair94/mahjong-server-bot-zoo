"""Belief Stage A — tile-location hard accounting.

Spec: docs/specs/v1-rule-bot.md § Component 1A; AI plan component 1, Stage A.

Per playable tile type, how many copies are *not visible* to a seat: the unseen
pool (wall + opponents' concealed hands). Exact, learning-free — every visible
tile (own concealed hand, every pond, every exposed meld) is subtracted from 4.
Recomputed from the `SeatView` each decision (Botzone-stateless convention:
cheap to rebuild beats incrementally maintained).

An opponent's concealed kong is masked by the projection (`hidden: True`, no
`tiles`) — its four tiles stay in the unseen pool even though they can never be
drawn. A small, documented overcount; correcting it needs a joint posterior
Stage A deliberately doesn't compute.
"""

from __future__ import annotations

from mahjong.engine.tiles import Tile
from mahjong.engine.types import SeatView

# The 34 playable tile types. Flowers never enter a hand, so they are outside
# the accounting universe entirely.
ALL_TILE_TYPES: tuple[Tile, ...] = tuple(
    f"{prefix}{rank}"
    for prefix, max_rank in (("W", 9), ("B", 9), ("T", 9), ("F", 4), ("J", 3))
    for rank in range(1, max_rank + 1)
)


def remaining_counts(view: SeatView, seat: int) -> dict[Tile, int]:
    """Copies of each tile type not visible to `seat`: 4 minus own concealed,
    all ponds, and all exposed meld tiles.

    `last_discard` is already present in the discarder's pond, so the pond is
    the single source (no double-count). Counts clamp at 0: a negative count
    would mean a projection bug, and this is overlay-grade signal — log-worthy,
    not crash-worthy.
    """
    visible: dict[Tile, int] = dict.fromkeys(ALL_TILE_TYPES, 0)

    def _see(tile: Tile) -> None:
        if tile in visible:
            visible[tile] += 1

    for i, s in enumerate(view["seats"]):
        concealed = s["concealed"]
        if i == seat and isinstance(concealed, list):
            for tile in concealed:
                _see(tile)
        for meld in s["melds"]:
            for tile in meld.get("tiles", ()):  # masked concealed kong: no tiles
                _see(tile)
        for tile in s["discards"]:
            _see(tile)

    return {t: max(0, 4 - visible[t]) for t in ALL_TILE_TYPES}


__all__ = ["ALL_TILE_TYPES", "remaining_counts"]
