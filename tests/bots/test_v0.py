"""Tests for the v0 offense bot decision policy (`mahjong.bots.v0`).

Spec: docs/specs/v0-offense-bot.md § Verification fixtures. Pure-policy
fixtures (no async); hands were mined against the live PyMahjongGB scorer so
their fan totals are ground truth, not guesses.

Convention: `decide_action` takes `legal_actions` as a parameter, so these
tests hand-craft legal actions rather than driving the engine — the policy is
tested in isolation from legality generation. The adapter/wiring/rollout
fixtures live in tests/adapters/test_v0_adapter.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.bots.v0 import (
    SUBFLOOR_TENPAI_DISTANCE,
    decide_action,
    fan_aware_distance,
    fan_feasible_ukeire,
)
from mahjong.engine.rulesets import MANIFEST

pytestmark = pytest.mark.needs_pymjgb

MCR_REF: dict[str, Any] = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
HOUSE_REF: dict[str, Any] = {
    "id": "mcr-house-3fan",
    "version": 1,
    "config_hash": MANIFEST["mcr-house-3fan"],
}
CONFIG_8 = {"fan_cliff": 8}
CONFIG_3 = {"fan_cliff": 3}

# --- Verified building-block hands (totals confirmed via pymj) ---

# tenpai, best ron = 13 fan → ron-feasible at any floor.
RON_FEASIBLE = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "B7", "W1"]
# tenpai, ron = 6 / self-draw = 8 → ron-feasible at floor 3, self-draw-only at floor 8.
SELFDRAW_ONLY = ["B1", "B2", "B3", "B4", "B5", "B6", "T2", "T3", "T4", "T6", "T7", "T8", "W5"]
# 1-shanten.
ONESHANTEN = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "W1", "J2"]
# both tenpai; WIDE has a 2-tile wait, NARROW a 1-tile wait.
WIDE = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "T2", "T2"]
NARROW = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B7", "B8", "B9", "T2"]


def _view(
    *,
    ruleset: dict[str, Any],
    seat: int,
    concealed: list[str],
    melds: list[dict[str, Any]] | None = None,
    seat_wind: str = "F2",
    round_wind: str = "F1",
    last_discard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Minimal SeatView for the self seat; opponents are count-only stubs."""
    seats: list[dict[str, Any]] = []
    for i in range(4):
        if i == seat:
            seats.append(
                {
                    "seat": i,
                    "seat_wind": seat_wind,
                    "concealed": list(concealed),
                    "melds": melds or [],
                    "discards": [],
                    "flowers": [],
                    "score": 0,
                }
            )
        else:
            seats.append(
                {
                    "seat": i,
                    "seat_wind": f"F{i + 1}",
                    "concealed": {},
                    "melds": [],
                    "discards": [],
                    "flowers": [],
                    "score": 0,
                }
            )
    return {
        "ruleset": ruleset,
        "round_wind": round_wind,
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 0,
        "wall": {"remaining_count": 50, "drawn_count": 94, "total": 144},
        "seats": seats,
        "last_discard": last_discard,
        "pending_claims": [],
        "phase": "DISCARD",
        "current_actor": seat,
        "terminal": None,
    }


# --- Fixture 1: HU is unconditional ---------------------------------------


def test_hu_taken_unconditionally() -> None:
    view = _view(ruleset=MCR_REF, seat=0, concealed=["B1"] * 13)
    legal = [{"type": "PLAY", "tile": "B1"}, {"type": "HU"}]
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "HU"}


def test_hu_beats_gang() -> None:
    view = _view(ruleset=MCR_REF, seat=0, concealed=["B1"] * 13)
    legal = [{"type": "GANG", "tile": "B1", "kind": "CONCEALED"}, {"type": "HU"}]
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "HU"}


# --- Fixture 2: GANG is unconditional (after HU) --------------------------


def test_gang_taken_over_play() -> None:
    view = _view(ruleset=MCR_REF, seat=0, concealed=["W1"] * 4 + ["B2"] * 9)
    legal = [
        {"type": "PLAY", "tile": "W1"},
        {"type": "PLAY", "tile": "B2"},
        {"type": "GANG", "tile": "W1", "kind": "CONCEALED"},
    ]
    assert decide_action(view, legal, 0, "DISCARD") == {
        "type": "GANG",
        "tile": "W1",
        "kind": "CONCEALED",
    }


def test_gang_taken_over_claim() -> None:
    """An exposed kong off a discard beats PENG/CHI/PASS."""
    view = _view(
        ruleset=MCR_REF,
        seat=2,
        concealed=["W1"] * 3 + ["B2"] * 10,
        last_discard={"tile": "W1", "seat": 1, "turn_index": 5},
    )
    legal = [
        {"type": "PASS"},
        {"type": "PENG", "tile": "W1"},
        {"type": "GANG", "tile": "W1", "kind": "EXPOSED"},
    ]
    assert decide_action(view, legal, 2, "CLAIM") == {
        "type": "GANG",
        "tile": "W1",
        "kind": "EXPOSED",
    }


def test_gang_multiple_deterministic() -> None:
    """Several legal kongs → the (tile_sort_key, kind)-minimal one (W < B)."""
    view = _view(ruleset=MCR_REF, seat=0, concealed=["W1"] * 4 + ["B2"] * 4 + ["T3"])
    legal = [
        {"type": "PLAY", "tile": "T3"},
        {"type": "GANG", "tile": "B2", "kind": "CONCEALED"},
        {"type": "GANG", "tile": "W1", "kind": "CONCEALED"},
    ]
    assert decide_action(view, legal, 0, "DISCARD") == {
        "type": "GANG",
        "tile": "W1",
        "kind": "CONCEALED",
    }


# --- Fixtures 3 & 4: fan-aware distance ordering & self-draw-only ----------


def test_distance_ordering_at_floor_8() -> None:
    feasible = fan_aware_distance(RON_FEASIBLE, [], "F2", "F1", CONFIG_8)
    subfloor = fan_aware_distance(SELFDRAW_ONLY, [], "F2", "F1", CONFIG_8)
    oneshanten = fan_aware_distance(ONESHANTEN, [], "F2", "F1", CONFIG_8)
    assert feasible == 0.0
    assert subfloor == SUBFLOOR_TENPAI_DISTANCE
    assert oneshanten == 1.0
    assert feasible < subfloor < oneshanten


def test_self_draw_only_tenpai_is_penalized() -> None:
    """SELFDRAW_ONLY rons for 6 / self-draws for 8. At floor 8 it is NOT
    ron-feasible → penalized (the DISCARD-probe correction); at floor 3 it
    rons for 6 ≥ 3 → feasible. Same hand, opposite verdict from the floor."""
    assert fan_aware_distance(SELFDRAW_ONLY, [], "F2", "F1", CONFIG_8) == SUBFLOOR_TENPAI_DISTANCE
    assert fan_aware_distance(SELFDRAW_ONLY, [], "F2", "F1", CONFIG_3) == 0.0


# --- Fixture 5: greedy discard --------------------------------------------


def test_decide_discard_keeps_feasible_tenpai() -> None:
    """A ron-feasible tenpai + one junk tile: discard reaches distance 0.0
    (the bot does not wreck the tenpai)."""
    hand14 = [*RON_FEASIBLE, "J3"]
    view = _view(ruleset=MCR_REF, seat=0, concealed=hand14)
    legal = [{"type": "PLAY", "tile": t} for t in sorted(set(hand14))]
    action = decide_action(view, legal, 0, "DISCARD")
    assert action["type"] == "PLAY"
    rem = list(hand14)
    rem.remove(action["tile"])
    assert fan_aware_distance(rem, [], "F2", "F1", CONFIG_8) == 0.0


# --- Fixture 6: ukeire tie-break + determinism ----------------------------


def test_fan_feasible_ukeire_prefers_wider_wait() -> None:
    assert fan_feasible_ukeire(WIDE, [], "F2", "F1", CONFIG_3) > fan_feasible_ukeire(
        NARROW, [], "F2", "F1", CONFIG_3
    )


def test_discard_breaks_distance_ties_by_ukeire_then_sort() -> None:
    """All discards here reach a ron-feasible tenpai (distance 0.0); the policy
    picks the max-ukeire discard, then the tile_sort_key-minimal among those.
    Verified ground truth: PLAY W9 (ukeire 2; W sorts before B before T)."""
    hand14 = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "B7", "T2", "T2"]
    view = _view(ruleset=HOUSE_REF, seat=0, concealed=hand14)
    legal = [{"type": "PLAY", "tile": t} for t in sorted(set(hand14))]
    action = decide_action(view, legal, 0, "DISCARD")
    assert action == {"type": "PLAY", "tile": "W9"}


def test_decide_action_is_deterministic() -> None:
    hand14 = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "B7", "T2", "T2"]
    view = _view(ruleset=HOUSE_REF, seat=0, concealed=hand14)
    legal = [{"type": "PLAY", "tile": t} for t in sorted(set(hand14))]
    first = decide_action(view, legal, 0, "DISCARD")
    second = decide_action(view, legal, 0, "DISCARD")
    assert first == second


# --- Fixtures 7 & 8: claims -----------------------------------------------


def test_beneficial_peng_taken() -> None:
    """Non-tenpai hand (1-shanten) where PENG J1 drops distance 1.0 → 0.0."""
    bot13 = ["B1", "B2", "B3", "T4", "T5", "T6", "W1", "W1", "B7", "B8", "J1", "J1", "J3"]
    view = _view(
        ruleset=HOUSE_REF,
        seat=2,
        concealed=bot13,
        last_discard={"tile": "J1", "seat": 1, "turn_index": 7},
    )
    legal = [{"type": "PASS"}, {"type": "PENG", "tile": "J1"}]
    assert decide_action(view, legal, 2, "CLAIM") == {"type": "PENG", "tile": "J1"}


def test_beneficial_chi_taken() -> None:
    """All-chows-straight 1-shanten where CHI B5B6B7 drops distance 1.0 → 0.0.
    (Per user: straight shapes make natural CHI fixtures.)"""
    bot13 = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "J1", "J2"]
    # Left neighbour (seat 1) discards B7; seat 2 may CHI.
    view = _view(
        ruleset=HOUSE_REF,
        seat=2,
        concealed=bot13,
        last_discard={"tile": "B7", "seat": 1, "turn_index": 9},
    )
    legal = [{"type": "PASS"}, {"type": "CHI", "tiles": ["B5", "B6", "B7"]}]
    assert decide_action(view, legal, 2, "CLAIM") == {
        "type": "CHI",
        "tiles": ["B5", "B6", "B7"],
    }


def test_useless_claim_passes() -> None:
    """Already-tenpai hand: PENG of the pair reaches an equal-distance tenpai,
    not a strictly better one → PASS (keep the hand concealed)."""
    bot13 = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "W1", "W1"]
    view = _view(
        ruleset=HOUSE_REF,
        seat=2,
        concealed=bot13,
        last_discard={"tile": "W1", "seat": 1, "turn_index": 7},
    )
    legal = [{"type": "PASS"}, {"type": "PENG", "tile": "W1"}]
    assert decide_action(view, legal, 2, "CLAIM") == {"type": "PASS"}
