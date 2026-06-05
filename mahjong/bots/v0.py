"""v0 offense bot — the pure decision policy.

Spec: docs/specs/v0-offense-bot.md.

An offense-only, k=1 greedy policy. No I/O, no async, no RNG — a pure function
of the seat view and the legal-action list, so it is deterministic and
unit-testable apart from the async seat machinery (`V0Adapter` in
`mahjong.adapters.v0` is the thin shell that wraps it).

Decision order:
    1. Take any legal HU (a legal win — legality already enforced the floor).
    2. Take any legal GANG (house-rules heuristic: a kong adds fan directly and
       does most of the work toward the 3-fan floor).
    3. CLAIM phase  → take a PENG/CHI only if it strictly lowers the hand's
       fan-aware distance versus passing; otherwise PASS.
    4. DISCARD phase → play the tile minimising fan-aware distance, breaking
       ties by ukeire, then by tile_sort_key (deterministic).

The core metric is *fan-aware distance*: shanten toward a hand that is a legal
win off a discard (a ron), not toward any 14-tile completion — so the bot never
locks into a sub-floor (illegal) tenpai. See the spec for the rationale on the
DISCARD-probe and always-GANG decisions.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from mahjong.engine import pymj
from mahjong.engine.rulesets import resolve_config
from mahjong.engine.tiles import Tile, tile_sort_key
from mahjong.engine.types import Action, LastDiscard, Meld, SeatView

# Defined locally rather than imported from adapters.base to keep the policy
# free of any server/adapter dependency (it calls only pymj + rulesets).
PromptKind = Literal["DISCARD", "CLAIM"]

# Hashable key for a meld set, used to memoize shanten.
MeldKey = tuple[tuple[str, tuple[Tile, ...]], ...]

# A sub-floor (not-ron-able) tenpai is structurally one tile from completion but
# not from a *legal* win, so it ranks between a ron-feasible tenpai (0.0) and a
# genuine 1-shanten (1.0). Tunable; tests pin the ordering, not the literal.
SUBFLOOR_TENPAI_DISTANCE = 0.5

# The 34 playable tile types (suited + winds + dragons); flowers never enter the
# hand, so they are excluded from the ukeire scan.
_ALL_TILE_TYPES: tuple[Tile, ...] = tuple(
    f"{prefix}{rank}"
    for prefix, max_rank in (("W", 9), ("B", 9), ("T", 9), ("F", 4), ("J", 3))
    for rank in range(1, max_rank + 1)
)


def decide_action(
    view: SeatView,
    legal_actions: list[Action],
    seat: int,
    prompt_kind: PromptKind,
) -> Action:
    """Choose one action from `legal_actions` for `seat`.

    `view` is the seat's own projection; `view["seats"][seat]` is the full
    `Seat` (list `concealed`). The fan floor is resolved from the ruleset ref —
    the same seam legality uses, so the bot's floor and the engine's never
    disagree.
    """
    # 1. A legal win is always taken.
    for action in legal_actions:
        if action["type"] == "HU":
            return action

    # 2. Always GANG (deterministic pick among several).
    gangs = [a for a in legal_actions if a["type"] == "GANG"]
    if gangs:
        return min(gangs, key=lambda a: (tile_sort_key(a["tile"]), a["kind"]))

    config = resolve_config(view["ruleset"])
    if prompt_kind == "CLAIM":
        return _decide_claim(view, legal_actions, seat, config)
    return _decide_discard(view, legal_actions, seat, config)


def fan_aware_distance(
    concealed: list[Tile],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    config: dict[str, Any],
) -> float:
    """Steps toward a *legal-on-discard* win for a 3k+1 hand.

    `float(shanten)` for non-tenpai hands; `0.0` for a tenpai with at least one
    ron-feasible wait (clears `fan_cliff` on a DISCARD win);
    `SUBFLOOR_TENPAI_DISTANCE` for a tenpai with no ron-feasible wait (sub-floor
    or self-draw-only — the bot reshapes toward a ron-able shape, but still
    takes any self-draw win via the unconditional HU at step 1).
    """
    s = _shanten(tuple(concealed), _meld_key(melds))
    if s != 0:
        return float(s)
    for wait in pymj.winning_tiles(concealed, melds):
        fans = pymj.calculate_fan(
            concealed,
            melds,
            wait,
            win_type="DISCARD",
            seat_wind=seat_wind,
            round_wind=round_wind,
            ruleset_config=config,
        )
        if fans:
            return 0.0
    return SUBFLOOR_TENPAI_DISTANCE


def fan_feasible_ukeire(
    concealed: list[Tile],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    config: dict[str, Any],
) -> int:
    """Acceptance count for a 3k+1 hand — the discard tie-break.

    At tenpai, the number of distinct winning tiles (wait width). Otherwise, the
    number of distinct tile types whose draw lowers shanten (standard ukeire).
    Raw (not fan-weighted): fan-awareness lives in the primary `fan_aware_distance`
    key; this only separates equal-distance candidates by hand flexibility.
    """
    melds_key = _meld_key(melds)
    base = _shanten(tuple(concealed), melds_key)
    if base == 0:
        return len(pymj.winning_tiles(concealed, melds))
    count = 0
    for tile in _ALL_TILE_TYPES:
        if concealed.count(tile) >= 4:
            continue
        drawn = [*concealed, tile]
        best = min(_shanten(tuple(_without(drawn, d)), melds_key) for d in set(drawn))
        if best < base:
            count += 1
    return count


# --- DISCARD ---------------------------------------------------------------


def _decide_discard(
    view: SeatView, legal_actions: list[Action], seat: int, config: dict[str, Any]
) -> Action:
    seat_data = view["seats"][seat]
    concealed: list[Tile] = list(seat_data["concealed"])
    melds: list[Meld] = list(seat_data["melds"])
    seat_wind = seat_data["seat_wind"]
    round_wind = view["round_wind"]

    plays = [a for a in legal_actions if a["type"] == "PLAY"]
    scored: list[tuple[float, Action, list[Tile]]] = []
    for action in plays:
        rem = _without(concealed, action["tile"])
        scored.append((fan_aware_distance(rem, melds, seat_wind, round_wind, config), action, rem))

    best_distance = min(d for d, _, _ in scored)
    tied = [(a, rem) for d, a, rem in scored if d == best_distance]
    if len(tied) == 1:
        return tied[0][0]
    # Tie-break: maximise ukeire, then minimise tile_sort_key (deterministic).
    return min(
        tied,
        key=lambda ar: (
            -fan_feasible_ukeire(ar[1], melds, seat_wind, round_wind, config),
            tile_sort_key(ar[0]["tile"]),  # type: ignore[typeddict-item]
        ),
    )[0]


# --- CLAIM -----------------------------------------------------------------


def _decide_claim(
    view: SeatView, legal_actions: list[Action], seat: int, config: dict[str, Any]
) -> Action:
    seat_data = view["seats"][seat]
    concealed: list[Tile] = list(seat_data["concealed"])
    melds: list[Meld] = list(seat_data["melds"])
    seat_wind = seat_data["seat_wind"]
    round_wind = view["round_wind"]
    last = view["last_discard"]
    assert last is not None, "CLAIM prompt with no last_discard"

    pass_action: Action = {"type": "PASS"}
    pass_distance = fan_aware_distance(concealed, melds, seat_wind, round_wind, config)

    best_action: Action = pass_action
    best_distance = pass_distance
    for action in legal_actions:
        if action["type"] not in ("PENG", "CHI"):
            continue
        after_concealed, after_melds = _apply_claim(action, concealed, melds, last)
        # Claiming forces an immediate discard, so the achievable distance is the
        # best over the subsequent legal discards.
        reachable = min(
            fan_aware_distance(_without(after_concealed, d), after_melds, seat_wind, round_wind, config)
            for d in set(after_concealed)
        )
        if reachable < best_distance:
            best_distance = reachable
            best_action = action
    return best_action


def _apply_claim(
    action: Action, concealed: list[Tile], melds: list[Meld], last: LastDiscard
) -> tuple[list[Tile], list[Meld]]:
    """The (concealed, melds) that result from a PENG/CHI, mirroring
    `engine.transition.claim`. The called tile comes from the discard pile, not
    from hand, so only the tiles contributed from hand are removed."""
    discarder = last["seat"]
    called = last["tile"]
    new_concealed = list(concealed)
    if action["type"] == "PENG":
        tile = action["tile"]
        new_concealed.remove(tile)
        new_concealed.remove(tile)
        new_meld: Meld = {
            "type": "PENG",
            "tiles": [tile, tile, tile],
            "called_tile": tile,
            "called_from_seat": discarder,
        }
    else:  # CHI
        tiles: list[Tile] = list(action["tiles"])  # type: ignore[typeddict-item]
        for t in tiles:
            if t != called:
                new_concealed.remove(t)
        new_meld = {
            "type": "CHI",
            "tiles": tiles,
            "called_tile": called,
            "called_from_seat": discarder,
        }
    return new_concealed, [*melds, new_meld]


# --- internals -------------------------------------------------------------


def _without(tiles: list[Tile], tile: Tile) -> list[Tile]:
    out = list(tiles)
    out.remove(tile)
    return out


def _meld_key(melds: list[Meld]) -> MeldKey:
    """Hashable key for the meld set, for shanten memoization."""
    return tuple((m["type"], tuple(m["tiles"])) for m in melds)


@lru_cache(maxsize=200_000)
def _shanten(concealed: tuple[Tile, ...], melds_key: MeldKey) -> int:
    """Memoized shanten. The ukeire scan recomputes shanten on hundreds of
    near-identical hands per decision; caching collapses that to a handful of
    real PyMahjongGB calls. Pure function of its inputs, so the cache is sound."""
    melds: list[Meld] = [
        {"type": t, "tiles": list(tiles), "called_from_seat": 0}  # type: ignore[typeddict-item]
        for t, tiles in melds_key
    ]
    return pymj.shanten(list(concealed), melds)


__all__ = [
    "SUBFLOOR_TENPAI_DISTANCE",
    "decide_action",
    "fan_aware_distance",
    "fan_feasible_ukeire",
]
