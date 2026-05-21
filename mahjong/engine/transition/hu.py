"""Transition for HU (win).

Spec: docs/specs/state-schema.md § Top-level state object > terminal,
      docs/specs/engine-api.md § PyMahjongGB integration boundary.

Two cases:
  - DISCARD phase, own turn: self-draw HU. Win tile is selected as the
    smallest tile in `concealed` whose removal yields a fan-bearing
    decomposition (deterministic when multiple choices work).
  - CLAIM_WINDOW phase: HU on a discard. Win tile is `last_discard.tile`.

Scoring is MCR canonical:
  - self-draw: each of the three non-winners pays (fan_total + 8).
  - discard:   discarder pays (fan_total + 24), other non-winners pay 8 each.
  Zero-sum by construction.
"""

from __future__ import annotations

from mahjong.engine import pymj
from mahjong.engine.tiles import tile_sort_key
from mahjong.engine.transition import clone_state
from mahjong.engine.types import FanEntry, GameState, Meld, Terminal


def apply_hu(state: GameState, seat: int) -> GameState:
    new = clone_state(state)
    seat_data = new["seats"][seat]
    melds = list(seat_data["melds"])
    if new["phase"] == "CLAIM_WINDOW":
        last = new["last_discard"]
        assert last is not None
        win_tile = last["tile"]
        deal_in_seat: int | None = last["seat"]
        win_type = "DISCARD"
        hand = list(seat_data["concealed"])
        # Tile becomes part of the winning hand visually; record convention
        # leaves it implicit (the meld layout reconstructs the shape).
    else:
        # Prefer the actually-just-drawn tile (engine has tracked it on
        # state.last_drawn since the last_drawn schema field was added).
        last_drawn = new["last_drawn"]
        hint = last_drawn["tile"] if last_drawn is not None and last_drawn["seat"] == seat else None
        win_tile = _pick_self_draw_win_tile(
            list(seat_data["concealed"]),
            melds,
            seat_data["seat_wind"],
            new["round_wind"],
            hint=hint,
        )
        deal_in_seat = None
        win_type = "SELF_DRAW"
        hand = list(seat_data["concealed"])
        hand.remove(win_tile)

    fans = pymj.calculate_fan(
        hand,
        melds,
        win_tile,
        win_type=win_type,  # type: ignore[arg-type]
        seat_wind=seat_data["seat_wind"],
        round_wind=new["round_wind"],
        ruleset_config={},
    )
    fan_total = sum(f["value"] for f in fans)

    score_delta = _score_delta(seat, fan_total, win_type, deal_in_seat)
    for i in range(4):
        new["seats"][i]["score"] += score_delta[i]

    terminal: Terminal = {
        "kind": "HU",
        "winner": seat,
        "win_tile": win_tile,
        "win_type": win_type,  # type: ignore[typeddict-item]
        "deal_in_seat": deal_in_seat,
        "fan": list(fans),
        "fan_total": fan_total,
        "score_delta": score_delta,
    }
    new["terminal"] = terminal
    new["phase"] = "TERMINAL"
    new["last_discard"] = None
    new["last_drawn"] = None
    new["pending_claims"] = []
    return new


def _pick_self_draw_win_tile(
    concealed: list[str],
    melds: list[Meld],
    seat_wind: str,
    round_wind: str,
    *,
    hint: str | None = None,
) -> str:
    """Pick the win tile for a self-draw HU.

    If `hint` (the engine's `state.last_drawn.tile`) is in `concealed` and
    yields a fan-bearing decomposition, return it — that's the *actually*
    drawn tile and the physically correct answer. Otherwise fall back to
    the smallest tile (canonical order) whose removal decomposes.
    """
    candidates: list[str] = []
    if hint is not None and hint in concealed:
        candidates.append(hint)
    for tile in sorted(set(concealed), key=tile_sort_key):
        if tile != hint:
            candidates.append(tile)
    for tile in candidates:
        hand = list(concealed)
        hand.remove(tile)
        fans = pymj.calculate_fan(
            hand,
            melds,
            tile,
            win_type="SELF_DRAW",
            seat_wind=seat_wind,
            round_wind=round_wind,
            ruleset_config={},
        )
        if fans:
            return tile
    raise AssertionError("self-draw HU legal but no win tile decomposes — engine/legality drift")


def _score_delta(winner: int, fan_total: int, win_type: str, deal_in_seat: int | None) -> list[int]:
    delta = [0, 0, 0, 0]
    if win_type == "SELF_DRAW":
        for i in range(4):
            if i == winner:
                continue
            delta[i] = -(fan_total + 8)
            delta[winner] += fan_total + 8
    else:  # DISCARD
        assert deal_in_seat is not None
        delta[deal_in_seat] = -(fan_total + 24)
        delta[winner] += fan_total + 24
        for i in range(4):
            if i in (winner, deal_in_seat):
                continue
            delta[i] = -8
            delta[winner] += 8
    return delta


# Suppress unused-import lint on FanEntry — the type is part of this module's
# contract surface via Terminal["fan"].
_ = FanEntry
