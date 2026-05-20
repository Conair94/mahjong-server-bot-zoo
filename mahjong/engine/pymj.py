"""The single PyMahjongGB integration seam.

Spec: docs/specs/engine-api.md § PyMahjongGB integration boundary.

Any future engine logic needing fan, shanten, or winning-tile calculation goes
through this module. Direct imports of MahjongGB from elsewhere in
mahjong.engine.* are a lint failure (see tests/lint/test_engine_purity.py).

The wrappers do three jobs:
    1. Type conversion: our Tile/Meld/WinType -> PyMahjongGB's tuple format.
    2. Cliff enforcement: MCR 8-fan minimum (the library scores all yaku
       regardless; this is where we apply the contract).
    3. Single point of contact: a PyMahjongGB version bump touches one file.
"""

from __future__ import annotations

from typing import Any, Literal

from MahjongGB import (  # type: ignore[import-not-found]
    HonorsAndKnittedTilesShanten,
    KnittedStraightShanten,
    MahjongFanCalculator,
    MahjongShanten,
    SevenPairsShanten,
    ThirteenOrphansShanten,
)

from mahjong.engine.tiles import Tile
from mahjong.engine.types import FanEntry, Meld, WinType

ShantenVariant = Literal[
    "SEVEN_PAIRS",
    "THIRTEEN_ORPHANS",
    "HONORS_AND_KNITTED",
    "KNITTED_STRAIGHT",
]

# MCR 8-fan cliff: hands below this don't qualify for HU.
MCR_FAN_CLIFF = 8

# Wind tokens (F1..F4) -> PyMahjongGB wind index (0=east, 1=south, 2=west, 3=north).
_WIND_INDEX = {"F1": 0, "F2": 1, "F3": 2, "F4": 3}

# Suited candidates for `winning_tiles` enumeration. Bonus tiles (H*) and
# the dragons/winds are included; flowers are not since they never enter
# the playable hand (state-schema.md § Tile encoding).
_CANDIDATE_TILES: tuple[str, ...] = tuple(
    f"{prefix}{rank}"
    for prefix, max_rank in (("W", 9), ("B", 9), ("T", 9), ("F", 4), ("J", 3))
    for rank in range(1, max_rank + 1)
)


def calculate_fan(
    hand: list[Tile],
    melds: list[Meld],
    win_tile: Tile,
    *,
    win_type: WinType,
    seat_wind: Tile,
    round_wind: Tile,
    ruleset_config: dict[str, Any],
    flower_count: int = 0,
) -> list[FanEntry]:
    """Returns yaku list for a winning hand, or [] if below the 8-fan cliff.

    `ruleset_config` is currently unused (PyMahjongGB pins MCR 2006); accepted
    for future home-rule overlays (server-plan S5).
    """
    pack = _melds_to_pack(melds)
    hand_tuple = tuple(hand)
    is_self_drawn = win_type == "SELF_DRAW"
    is_about_kong = win_type == "ROBBED_KONG"
    is_wall_last = win_type == "LAST_TILE"
    seat_idx = _WIND_INDEX[seat_wind]
    prevalent_idx = _WIND_INDEX[round_wind]
    try:
        result = MahjongFanCalculator(
            pack,
            hand_tuple,
            win_tile,
            flower_count,
            is_self_drawn,
            False,  # is4thTile — engine-side bookkeeping; conservative default
            is_about_kong,
            is_wall_last,
            seat_idx,
            prevalent_idx,
            True,  # verbose: gives English names
        )
    except TypeError:
        # PyMahjongGB raises TypeError for non-winning shapes and for the
        # "ERROR_NOT_HU" / "ERROE_WRONG_TILE_CODE" family. Treat all as
        # "this hand can't claim HU" -> empty.
        return []

    fans: list[FanEntry] = []
    total = 0
    for fan_point, cnt, _name_zh, name_en in result:
        value = fan_point * cnt
        fans.append({"name": name_en, "value": value})
        total += value
    if total < MCR_FAN_CLIFF:
        return []
    return fans


def shanten(hand: list[Tile], melds: list[Meld]) -> int:
    """Steps to tenpai. 0 = tenpai, -1 = already won."""
    pack = _melds_to_pack(melds)
    return int(MahjongShanten(pack, tuple(hand)))


def shanten_specialized(hand: list[Tile], variant: ShantenVariant) -> int:
    """Shanten for a specialized hand variant.

    Variants are concealed-only forms; melds are not accepted (PyMahjongGB
    contract).
    """
    hand_tuple = tuple(hand)
    if variant == "SEVEN_PAIRS":
        result = SevenPairsShanten(hand_tuple)
    elif variant == "THIRTEEN_ORPHANS":
        result = ThirteenOrphansShanten(hand_tuple)
    elif variant == "HONORS_AND_KNITTED":
        result = HonorsAndKnittedTilesShanten(hand_tuple)
    elif variant == "KNITTED_STRAIGHT":
        result = KnittedStraightShanten(hand_tuple)
    else:
        raise ValueError(f"unknown shanten variant: {variant!r}")
    # Variant calls return (shanten, useful_tuple).
    return int(result[0])


def winning_tiles(hand: list[Tile], melds: list[Meld]) -> list[Tile]:
    """Tiles that would complete `hand` into a winning shape.

    Enumerates each candidate tile and asks PyMahjongGB whether
    `(hand + candidate, candidate)` is a winning configuration. The
    library raises `ERROR_NOT_WIN` for non-winning shapes, so a
    successful call is the witness.

    Independent of the 8-fan cliff — the caller applies that separately
    via `calculate_fan`.
    """
    if shanten(hand, melds) != 0:
        return []
    pack = _melds_to_pack(melds)
    hand_tuple = tuple(hand)
    out: list[Tile] = []
    for candidate in _CANDIDATE_TILES:
        if hand.count(candidate) >= 4:
            continue  # a fifth copy can't exist
        # PyMahjongGB convention: `hand` is the standing position (13 tiles
        # for no-meld), `winTile` is the 14th, supplied separately. The full
        # hand is the union — so the candidate must be representable as the
        # 14th tile of the (standing + candidate) hand.
        try:
            MahjongFanCalculator(
                pack,
                hand_tuple,
                candidate,
                0,
                False,
                False,
                False,
                False,
                0,
                0,
            )
        except TypeError:
            continue
        out.append(candidate)
    return out


# --- internal: meld conversion ---


def _melds_to_pack(melds: list[Meld]) -> tuple[tuple[str, str, int], ...]:
    """Convert our Meld TypedDicts to PyMahjongGB's pack tuple format.

    PyMahjongGB pack entry: `(kind, tile, offer)` where kind is
    "CHI"/"PENG"/"GANG" and `offer` is 0..3 (the absolute seat of the
    discarder; per the Botzone convention).
    """
    out: list[tuple[str, str, int]] = []
    for meld in melds:
        kind_raw = meld["type"]
        # GANG_CONCEALED/GANG_EXPOSED/GANG_ADDED all flatten to "GANG" for
        # PyMahjongGB; the variant matters for fan calculation flags
        # (is_about_kong) which is the caller's job to set.
        kind = "GANG" if kind_raw.startswith("GANG") else kind_raw
        tile = meld.get("called_tile") or meld["tiles"][0]
        offer = meld["called_from_seat"]
        out.append((kind, tile, offer))
    return tuple(out)
