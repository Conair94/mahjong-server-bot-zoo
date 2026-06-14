"""Decision-time hand analysis: shanten, waits, fan potential, remaining counts.

Spec: docs/specs/hand-stats.md.

Pure functions of a seat's own `SeatView` — nothing here reads anything the
seat can't already see, so the payload is privacy-safe by construction. The
math goes through `mahjong.engine.pymj` (the single PyMahjongGB seam), same
as legality and settlement, so the fan numbers shown are exactly what the
engine would pay.

This is also the bot-explainability surface: `discards[]` is the quantity
`mahjong.bots.v0` ranks candidates by, exposed as data instead of a policy.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from functools import lru_cache
from typing import Any, Literal

from mahjong.engine import pymj
from mahjong.engine.rulesets import resolve_config
from mahjong.engine.tiles import Tile, tile_sort_key
from mahjong.engine.types import Meld

PromptKind = Literal["DISCARD", "CLAIM"]

# The 34 playable tile types; flowers (H*) never enter the hand.
_ALL_TILE_TYPES: tuple[Tile, ...] = tuple(
    f"{prefix}{rank}"
    for prefix, max_rank in (("W", 9), ("B", 9), ("T", 9), ("F", 4), ("J", 3))
    for rank in range(1, max_rank + 1)
)

_MeldKey = tuple[tuple[str, tuple[Tile, ...]], ...]


def remaining_counts(view: dict[str, Any]) -> dict[Tile, int]:
    """Unseen copies of each tile type: 4 minus every copy visible to the
    viewing seat (own concealed, every seat's exposed meld tiles, every
    discard pond). An opponent's *hidden* concealed kong carries no `tiles`
    in the projection and subtracts nothing — the result is an upper bound
    on drawable copies, which is the correct epistemic quantity."""
    counts: dict[Tile, int] = {t: 4 for t in _ALL_TILE_TYPES}
    for seat_data in view["seats"]:
        concealed = seat_data["concealed"]
        if isinstance(concealed, list):  # own seat; opponents carry {"count": N}
            for tile in concealed:
                counts[tile] -= 1
        for meld in seat_data["melds"]:
            for tile in meld.get("tiles", ()):
                counts[tile] -= 1
        for tile in seat_data["discards"]:
            counts[tile] -= 1
    return counts


def prompt_stats(
    view: dict[str, Any],
    seat: int,
    legal_actions: list[dict[str, Any]],
    prompt_kind: PromptKind,
) -> dict[str, Any]:
    """The `PROMPT.stats` payload (spec § Payload schema).

    DISCARD prompts get a per-candidate `discards` table; CLAIM prompts get
    the standing hand's stats (`hand`) plus per-claim-option reachability
    (`claims`). Deterministic: candidates and tile lists are sorted.
    """
    config = resolve_config(view["ruleset"])
    floor = config.get("fan_cliff", pymj.MCR_FAN_CLIFF)
    # Raw fan calculation: the cliff is applied by the *client* against
    # `floor` so sub-floor waits are shown dimmed, not hidden (FB-15 shape).
    raw_config = {**config, "fan_cliff": 0}

    seat_data = view["seats"][seat]
    concealed: list[Tile] = list(seat_data["concealed"])
    melds: list[Meld] = list(seat_data["melds"])
    seat_wind: Tile = seat_data["seat_wind"]
    round_wind: Tile = view["round_wind"]
    counts = remaining_counts(view)

    out: dict[str, Any] = {
        "floor": floor,
        "wall_remaining": view["wall"]["remaining_count"],
    }

    if prompt_kind == "DISCARD":
        rows = []
        for action in legal_actions:
            if action["type"] != "PLAY":
                continue
            standing = _without(concealed, action["tile"])
            rows.append(
                {
                    "tile": action["tile"],
                    "shanten": _shanten(tuple(standing), _meld_key(melds)),
                    "tiles": _tiles_block(
                        standing, melds, seat_wind, round_wind, raw_config, counts
                    ),
                }
            )
        rows.sort(
            key=lambda r: (
                r["shanten"],
                -sum(t["remaining"] for t in r["tiles"]),
                tile_sort_key(r["tile"]),
            )
        )
        out["discards"] = rows
        return out

    # CLAIM: the standing 3k+1 hand, plus what each claim option reaches.
    out["hand"] = {
        "shanten": _shanten(tuple(concealed), _meld_key(melds)),
        "tiles": _tiles_block(concealed, melds, seat_wind, round_wind, raw_config, counts),
    }
    claims: list[dict[str, Any]] = []
    for action in legal_actions:
        if action["type"] not in ("PENG", "CHI", "GANG"):
            continue
        claims.append(
            {
                "action": dict(action),
                "shanten_after": _claim_reach(action, concealed, melds, view["last_discard"]),
            }
        )
    claims.sort(key=lambda c: (c["action"]["type"], str(c["action"])))
    out["claims"] = claims
    return out


def settlement_hand_stats(
    seats: list[Mapping[str, Any]],
    round_wind: Tile,
    ruleset: Mapping[str, Any],
    *,
    exclude_seats: Collection[int] = (),
) -> dict[str, Any]:
    """Hand-end tenpai/shanten reveal for every seat that didn't win.

    Settlement-time analysis (all hands face-up at TERMINAL), shipped as
    ``HAND_END.terminal.final_hand_stats``. For each seat not in
    ``exclude_seats`` (the winner(s)):

    - ``shanten`` (0 == tenpai).
    - shanten 0 → ``waits``: every winning tile with raw fan
      (``fan_discard`` / ``fan_self_draw``) — the same per-wait figures
      ``prompt_stats`` reports for a tenpai hand.
    - shanten 1 → ``accepts``: the **top-3** tiles that reach tenpai, ranked by
      the best fan reachable once tenpai is hit (a 2-ply optimistic max: draw
      this tile → best completion).
    - shanten >= 2 → no tile list (just the count).

    Raw fan (cliff zeroed) is reported with ``floor`` alongside so the client
    can dim sub-floor figures, exactly as ``prompt_stats`` does (FB-15 shape).
    A seat not in standing 3k+1 form (e.g. the self-draw winner, were it not
    excluded) is skipped defensively.
    """
    config = resolve_config(dict(ruleset))
    floor = config.get("fan_cliff", pymj.MCR_FAN_CLIFF)
    raw_config = {**config, "fan_cliff": 0}
    excluded = set(exclude_seats)

    out_seats: list[dict[str, Any]] = []
    for seat_data in seats:
        seat = seat_data["seat"]
        if seat in excluded:
            continue
        concealed: list[Tile] = list(seat_data["concealed"])
        if len(concealed) % 3 != 1:
            continue  # not a standing hand — skip rather than emit garbage
        melds: list[Meld] = list(seat_data["melds"])
        seat_wind: Tile = seat_data["seat_wind"]
        shanten = _shanten(tuple(concealed), _meld_key(melds))
        entry: dict[str, Any] = {"seat": seat, "shanten": shanten}
        if shanten == 0:
            entry["waits"] = _settlement_waits(
                concealed, melds, seat_wind, round_wind, raw_config
            )
        elif shanten == 1:
            entry["accepts"] = _settlement_accepts(
                concealed, melds, seat_wind, round_wind, raw_config
            )
        out_seats.append(entry)

    out_seats.sort(key=lambda e: e["seat"])
    return {"floor": floor, "seats": out_seats}


# --- internals ---------------------------------------------------------------


def _tiles_block(
    standing: list[Tile],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    raw_config: dict[str, Any],
    counts: dict[Tile, int],
) -> list[dict[str, Any]]:
    """At tenpai: waits with raw fan per win type. Otherwise: effective
    tiles (draws that lower shanten). Dead tiles (remaining 0) stay listed —
    a dead wait is exactly what the player needs to see."""
    melds_key = _meld_key(melds)
    base = _shanten(tuple(standing), melds_key)
    if base == 0:
        rows: list[dict[str, Any]] = []
        for wait in sorted(pymj.winning_tiles(standing, melds), key=tile_sort_key):
            rows.append(
                {
                    "tile": wait,
                    "remaining": counts[wait],
                    "fan_discard": _fan_total(
                        standing, melds, wait, "DISCARD", seat_wind, round_wind, raw_config
                    ),
                    "fan_self_draw": _fan_total(
                        standing, melds, wait, "SELF_DRAW", seat_wind, round_wind, raw_config
                    ),
                }
            )
        return rows
    rows = []
    for tile in _ALL_TILE_TYPES:
        if standing.count(tile) >= 4:
            continue
        drawn = [*standing, tile]
        best = min(_shanten(tuple(_without(drawn, d)), melds_key) for d in set(drawn))
        if best < base:
            rows.append({"tile": tile, "remaining": counts[tile]})
    rows.sort(key=lambda r: tile_sort_key(r["tile"]))
    return rows


def _settlement_waits(
    standing: list[Tile],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    raw_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Tenpai (shanten 0): each winning tile with raw discard/self-draw fan.

    Same per-wait figures as ``_tiles_block``'s tenpai branch, minus the
    remaining-count (a dead wait is moot once the hand is over)."""
    rows: list[dict[str, Any]] = []
    for wait in sorted(pymj.winning_tiles(standing, melds), key=tile_sort_key):
        rows.append(
            {
                "tile": wait,
                "fan_discard": _fan_total(
                    standing, melds, wait, "DISCARD", seat_wind, round_wind, raw_config
                ),
                "fan_self_draw": _fan_total(
                    standing, melds, wait, "SELF_DRAW", seat_wind, round_wind, raw_config
                ),
            }
        )
    return rows


def _settlement_accepts(
    standing: list[Tile],
    melds: list[Meld],
    seat_wind: Tile,
    round_wind: Tile,
    raw_config: dict[str, Any],
    *,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """1-shanten: the top-``top_n`` tiles that reach tenpai, each with the best
    fan reachable once tenpai is hit. For each candidate draw, find the discard
    that lands on tenpai and take the max fan over the resulting waits (the most
    the player could potentially have made). Ranked by best fan desc, then tile
    order, and capped — a 1-shanten hand often accepts 8+ tiles."""
    melds_key = _meld_key(melds)
    best_by_tile: dict[Tile, int] = {}
    for draw in _ALL_TILE_TYPES:
        if standing.count(draw) >= 4:
            continue
        drawn = [*standing, draw]
        best_fan: int | None = None
        for disc in set(drawn):
            after = _without(drawn, disc)
            if _shanten(tuple(after), melds_key) != 0:
                continue
            for wait in pymj.winning_tiles(after, melds):
                fan = max(
                    _fan_total(after, melds, wait, "DISCARD", seat_wind, round_wind, raw_config),
                    _fan_total(after, melds, wait, "SELF_DRAW", seat_wind, round_wind, raw_config),
                )
                if best_fan is None or fan > best_fan:
                    best_fan = fan
        if best_fan is not None:
            best_by_tile[draw] = best_fan
    rows: list[dict[str, Any]] = [{"tile": t, "best_fan": f} for t, f in best_by_tile.items()]
    rows.sort(key=lambda r: (-r["best_fan"], tile_sort_key(r["tile"])))
    return rows[:top_n]


def _fan_total(
    standing: list[Tile],
    melds: list[Meld],
    win_tile: Tile,
    win_type: str,
    seat_wind: Tile,
    round_wind: Tile,
    raw_config: dict[str, Any],
) -> int:
    fans = pymj.calculate_fan(
        standing,
        melds,
        win_tile,
        win_type=win_type,  # type: ignore[arg-type]
        seat_wind=seat_wind,
        round_wind=round_wind,
        ruleset_config=raw_config,
    )
    return sum(f["value"] for f in fans)


def _claim_reach(
    action: dict[str, Any],
    concealed: list[Tile],
    melds: list[Meld],
    last_discard: dict[str, Any] | None,
) -> int:
    """Best shanten reachable by taking `action`. PENG/CHI force a follow-up
    discard (min over them); an exposed GANG draws a replacement instead, so
    its post-meld 3k+1 hand is scored directly. Mirrors
    `engine.transition.claim`/`gang` hand bookkeeping."""
    assert last_discard is not None, "CLAIM stats with no last_discard"
    called: Tile = last_discard["tile"]
    discarder: int = last_discard["seat"]
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
    elif action["type"] == "CHI":
        tiles: list[Tile] = list(action["tiles"])
        for t in tiles:
            if t != called:
                new_concealed.remove(t)
        new_meld = {
            "type": "CHI",
            "tiles": tiles,
            "called_tile": called,
            "called_from_seat": discarder,
        }
    else:  # GANG (EXPOSED in a claim window)
        tile = action["tile"]
        for _ in range(3):
            new_concealed.remove(tile)
        new_meld = {
            "type": "GANG_EXPOSED",
            "tiles": [tile, tile, tile, tile],
            "called_tile": tile,
            "called_from_seat": discarder,
        }
        return _shanten(tuple(new_concealed), _meld_key([*melds, new_meld]))

    after_key = _meld_key([*melds, new_meld])
    return min(_shanten(tuple(_without(new_concealed, d)), after_key) for d in set(new_concealed))


def _without(tiles: list[Tile], tile: Tile) -> list[Tile]:
    out = list(tiles)
    out.remove(tile)
    return out


def _meld_key(melds: list[Meld]) -> _MeldKey:
    return tuple((m["type"], tuple(m["tiles"])) for m in melds)


@lru_cache(maxsize=200_000)
def _shanten(concealed: tuple[Tile, ...], melds_key: _MeldKey) -> int:
    """Memoized shanten (same shape as `bots.v0._shanten`; separate cache —
    the analysis layer must not import bot policy modules)."""
    melds: list[Meld] = [
        {"type": t, "tiles": list(tiles), "called_from_seat": 0}  # type: ignore[typeddict-item]
        for t, tiles in melds_key
    ]
    return pymj.shanten(list(concealed), melds)


def stats_for_prompt(prompt: Mapping[str, Any], seat: int) -> dict[str, Any] | None:
    """`HumanAdapter.stats_provider`-shaped binding: unpacks the seat-port
    `Prompt` (which already carries the authoritative per-seat view and the
    legal actions) into `prompt_stats`. Bound at the composition roots.

    The param is typed `Mapping` (not the strict `Prompt` TypedDict) on
    purpose: this binding only *reads* the prompt, and a read-only `Mapping`
    parameter is what makes `stats_for_prompt` assignable to the
    `StatsProvider = Callable[[Prompt, int], ...]` alias (a callable taking a
    `dict[str, Any]` is not — a TypedDict is not a `dict` subtype).

    Gated to **DISCARD** prompts (Spec 37 revision, 2026-06-12): only when the
    seat holds 14 tiles and must choose a discard is "which tile, and how far
    does each leave me?" a well-posed question. Returns ``None`` otherwise, so
    no `stats` rides a CLAIM (or any non-discard) prompt."""
    if prompt["kind"] != "DISCARD":
        return None
    return prompt_stats(prompt["view"], seat, prompt["legal_actions"], prompt["kind"])


__all__ = ["prompt_stats", "remaining_counts", "stats_for_prompt"]
