"""Tests for config-driven scoring: the fan floor comes from the ruleset, not a
constant, and the house ruleset ships with a frozen hash.

Spec: docs/specs/scoring-config.md § Goals, § The two shipped rulesets,
      § Verification fixtures (1, 6, 7, 10).

The load-bearing property: one hand, two rulesets, opposite legality. A 7-fan
self-draw is illegal under mcr-2006 (cliff 8) and legal under mcr-house-3fan
(cliff 3) — proving the floor is read from config, not hard-coded.
"""

from __future__ import annotations

from typing import Any

from mahjong.engine import pymj, scoring
from mahjong.engine.hashing import canonical_hash
from mahjong.engine.legality import legal_actions
from mahjong.engine.rulesets import MANIFEST, load_ruleset, resolve_config
from mahjong.engine.tiles import Tile, tile_sort_key
from mahjong.engine.transition import apply_action

# A concrete low-fan winning hand (found empirically against the raw calculator):
#   W2W2W2 / B3B4B5 / T6T7T8 / W7W8W9 + pair B9, won on B9.
#   natural self-draw total = 6 fan (Fully Concealed 4 + No Honors 1 + Single Wait 1).
#
# The legality layer maximizes fan over *every* tile-as-win choice, and an
# interior tile earns a wait fan (Closed/Edge Wait, +1). So the load-bearing
# property is the *range* over win tiles, not one decomposition: for this hand
# min=5, max=6 — every choice clears the house floor (3) and none reaches the
# mcr-2006 floor (8). (An all-chows hand can spuriously hit 8 via a wait fan;
# this pung-based hand stays safely sub-8.)
LOW_FAN_HAND13: list[Tile] = ["W2", "W2", "W2", "B3", "B4", "B5", "T6", "T7", "T8", "W7", "W8", "W9", "B9"]
LOW_FAN_WIN: Tile = "B9"

MCR_REF: dict[str, Any] = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
HOUSE_REF: dict[str, Any] = {"id": "mcr-house-3fan", "version": 1}


# --- fixture 1: the fan cliff is read from config, not the MCR_FAN_CLIFF constant ---


def test_calculate_fan_cliff_from_config() -> None:
    """Same 6-fan hand: empty under cliff 8, non-empty under cliff 3."""
    kw: dict[str, Any] = dict(
        hand=LOW_FAN_HAND13, melds=[], win_tile=LOW_FAN_WIN,
        win_type="SELF_DRAW", seat_wind="F1", round_wind="F1",
    )
    assert pymj.calculate_fan(**kw, ruleset_config={"fan_cliff": 8}) == []
    assert pymj.calculate_fan(**kw, ruleset_config={"fan_cliff": 3}), "6 fan clears a 3-fan floor"


def test_calculate_fan_defaults_to_8_when_cliff_absent() -> None:
    """No fan_cliff key => default 8 (backwards-compatible with existing callers)."""
    fans = pymj.calculate_fan(
        hand=LOW_FAN_HAND13, melds=[], win_tile=LOW_FAN_WIN,
        win_type="SELF_DRAW", seat_wind="F1", round_wind="F1",
        ruleset_config={},
    )
    assert fans == []


# --- fixture 6: the house ruleset loads and its hash is frozen in MANIFEST ---


def test_house_ruleset_loads_and_hash_matches_manifest() -> None:
    config = load_ruleset({"id": "mcr-house-3fan"})
    assert canonical_hash(config) == MANIFEST["mcr-house-3fan"]
    assert config["fan_cliff"] == 3
    assert config["conversion"]["scheme"] == "house-table"
    assert config["dealer_repeat_on_win"] is True


def test_mcr_2006_hash_unchanged() -> None:
    """Receipt that mcr-2006.json was NOT touched (its goldens stay valid)."""
    assert canonical_hash(load_ruleset({"id": "mcr-2006"})) == MANIFEST["mcr-2006"]


# --- fixture 10: resolution is memoized but behaviour-neutral ---


def test_resolve_config_is_cached_and_equal() -> None:
    a = resolve_config({"id": "mcr-house-3fan", "version": 1, "config_hash": MANIFEST["mcr-house-3fan"]})
    b = resolve_config({"id": "mcr-house-3fan"})
    assert a == b
    assert canonical_hash(a) == canonical_hash(b)


# --- fixture 7: HU legality at the 3-fan floor, end-to-end via legal_actions ---


def _state_with_self_draw_hand(ruleset_ref: dict[str, Any]) -> dict[str, Any]:
    """Minimal DISCARD-phase state where seat 0 holds the 14-tile low-fan hand."""
    concealed0 = sorted([*LOW_FAN_HAND13, LOW_FAN_WIN], key=tile_sort_key)
    seats = [
        {
            "seat": i,
            "seat_wind": f"F{i + 1}",
            "concealed": concealed0 if i == 0 else sorted(["W1"] * 13, key=tile_sort_key),
            "melds": [],
            "discards": [],
            "flowers": [],
            "score": 0,
        }
        for i in range(4)
    ]
    return {
        "ruleset": ruleset_ref,
        "round_wind": "F1",
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 0,
        "wall": {"remaining": [], "drawn_count": 144, "total": 144},
        "seats": seats,
        "last_discard": None,
        "last_drawn": {"seat": 0, "tile": LOW_FAN_WIN},
        "pending_claims": [],
        "phase": "DISCARD",
        "current_actor": 0,
        "terminal": None,
        "rng": {"seed": "0", "cursor": 0},
    }


def test_hu_illegal_at_8fan_floor() -> None:
    state = _state_with_self_draw_hand(MCR_REF)
    assert {"type": "HU"} not in legal_actions(state, 0)  # type: ignore[arg-type]


def test_hu_legal_at_3fan_floor() -> None:
    state = _state_with_self_draw_hand(HOUSE_REF)
    assert {"type": "HU"} in legal_actions(state, 0)  # type: ignore[arg-type]


# --- the reward contract end-to-end: apply_hu under the house ruleset pays the
#     house conversion, not the official formula ---


def test_apply_hu_uses_house_conversion() -> None:
    """The engine's terminal transition under mcr-house-3fan settles via the
    house table (winner +6X self-draw), proving the scorer is config-driven
    through the real apply_action path — this is the RL reward contract."""
    state = _state_with_self_draw_hand(HOUSE_REF)
    new = apply_action(state, 0, {"type": "HU"})  # type: ignore[arg-type]
    term = new["terminal"]
    assert term is not None and term["kind"] == "HU" and term["winner"] == 0

    house_conversion = resolve_config({"id": "mcr-house-3fan"})["conversion"]
    expected = scoring.score_delta(0, term["fan_total"], "SELF_DRAW", None, house_conversion)
    assert term["score_delta"] == expected
    assert sum(term["score_delta"]) == 0

    # And it is genuinely the house payout, not the official additive one.
    official = scoring.score_delta(0, term["fan_total"], "SELF_DRAW", None, None)
    assert term["score_delta"] != official
    # Self-draw winner credit is 6X for the tier matching this hand's fan.
    x = scoring.lookup_x(term["fan_total"], house_conversion["tiers"])
    assert term["score_delta"][0] == 6 * x
