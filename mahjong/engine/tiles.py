"""Tile-token validation, canonical sort order, and the 144-tile canonical set.

Spec: docs/specs/state-schema.md § Tile encoding,
      docs/specs/determinism.md § canonical_tile_set order.

A tile token is a `str` (Botzone format verbatim). No enum, no int — strings
are JSON-native, comparable, and interchangeable with PyMahjongGB / the
official judge / every public dataset.
"""

from __future__ import annotations

from typing import Final

Tile = str

# Section prefix → (max rank, copies per rank). Order here defines the
# canonical sort order across sections (W < B < T < F < J < H).
_SECTIONS: Final[tuple[tuple[str, int, int], ...]] = (
    ("W", 9, 4),  # Characters (wan)
    ("B", 9, 4),  # Dots (bing)
    ("T", 9, 4),  # Bamboo (tiao)
    ("F", 4, 4),  # Winds (feng)
    ("J", 3, 4),  # Dragons (jian)
    ("H", 8, 1),  # Flowers/Seasons (hua) — 1 copy each
)

_SECTION_INDEX: Final[dict[str, int]] = {prefix: i for i, (prefix, _, _) in enumerate(_SECTIONS)}
_SECTION_MAX_RANK: Final[dict[str, int]] = {prefix: mx for prefix, mx, _ in _SECTIONS}


def validate_tile(s: str) -> bool:
    """Return True iff `s` is a legal Botzone-format tile token.

    Locked here so every consumer can validate before letting an untrusted
    token cross an engine boundary. The set of legal tokens is closed: 34
    suited + honors + 8 bonus = 42 distinct token strings.
    """
    if not isinstance(s, str) or len(s) != 2:
        return False
    prefix, rank_char = s[0], s[1]
    max_rank = _SECTION_MAX_RANK.get(prefix)
    if max_rank is None:
        return False
    if not ("1" <= rank_char <= "9"):
        return False
    rank = int(rank_char)
    return 1 <= rank <= max_rank


def canonical_tile_set() -> list[Tile]:
    """Return the 144 tile tokens in canonical order.

    Order: W1x4, W2x4, ..., W9x4, B1x4, ..., B9x4, T1x4, ..., T9x4,
    F1x4..F4x4, J1x4..J3x4, H1..H8 (one copy each).

    This order is **load-bearing**: it is the input to `shuffled_wall`, so a
    change here invalidates every record ever written. Pinned by
    determinism.md fixture 2.
    """
    tiles: list[Tile] = []
    for prefix, max_rank, copies in _SECTIONS:
        for rank in range(1, max_rank + 1):
            tiles.extend([f"{prefix}{rank}"] * copies)
    return tiles


def tile_sort_key(tile: Tile) -> tuple[int, int]:
    """Sort key: (section_index, rank). Yields canonical order.

    Assumes a valid token; callers that may see untrusted input should
    `validate_tile` first.
    """
    return _SECTION_INDEX[tile[0]], int(tile[1])
