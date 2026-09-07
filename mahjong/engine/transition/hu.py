"""Transition for HU (win).

Spec: docs/specs/state-schema.md § Top-level state object > terminal,
      docs/specs/engine-api.md § PyMahjongGB integration boundary.

Two cases:
  - DISCARD phase, own turn: self-draw HU on the actual `last_drawn` tile.
  - CLAIM_WINDOW phase: HU on a discard. Win tile is `last_discard.tile`.

Scoring is driven by the resolved ruleset's `conversion` block via
`scoring.score_delta` (scoring-config.md). With no block (mcr-2006) this is the
canonical MCR formula: self-draw → each non-winner pays (fan_total + 8);
discard → discarder pays (fan_total + 8), other non-winners pay 8 each. The
house ruleset substitutes its tier-lookup conversion. Zero-sum either way.
"""

from __future__ import annotations

from mahjong.engine import pymj, scoring
from mahjong.engine.rulesets import resolve_config
from mahjong.engine.transition import clone_state
from mahjong.engine.types import GameState, Terminal, WinType


def apply_hu(state: GameState, seat: int) -> GameState:
    new = clone_state(state)
    config = resolve_config(new["ruleset"])
    seat_data = new["seats"][seat]
    melds = list(seat_data["melds"])
    if new["phase"] == "CLAIM_WINDOW":
        last = new["last_discard"]
        assert last is not None
        win_tile = last["tile"]
        deal_in_seat: int | None = last["seat"]
        win_type: WinType = "DISCARD"
        hand = list(seat_data["concealed"])
        # Tile becomes part of the winning hand visually; record convention
        # leaves it implicit (the meld layout reconstructs the shape).
    else:
        # Legality requires a real draw by this seat. Never substitute a
        # different tile: that can invent Closed/Edge/Single Wait fan.
        last_drawn = new["last_drawn"]
        assert last_drawn is not None and last_drawn["seat"] == seat
        win_tile = last_drawn["tile"]
        deal_in_seat = None
        win_type = "SELF_DRAW"
        hand = list(seat_data["concealed"])
        hand.remove(win_tile)

    fans = pymj.calculate_fan(
        hand,
        melds,
        win_tile,
        win_type=win_type,
        seat_wind=seat_data["seat_wind"],
        round_wind=new["round_wind"],
        ruleset_config=config,
        flower_count=len(seat_data["flowers"]),
    )
    fan_total = sum(f["value"] for f in fans)

    score_delta = scoring.score_delta(
        seat,
        fan_total,
        win_type,
        deal_in_seat,
        conversion=config.get("conversion"),
    )
    for i in range(4):
        new["seats"][i]["score"] += score_delta[i]

    terminal: Terminal = {
        "kind": "HU",
        "winner": seat,
        "win_tile": win_tile,
        "win_type": win_type,
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
