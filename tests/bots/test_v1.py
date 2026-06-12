"""Tests for the v1 rule-based bot (`mahjong.bots.v1`).

Spec: docs/specs/v1-rule-bot.md § Verification fixtures. Hands and fan totals
were mined against the live PyMahjongGB scorer (see the probe notes in the
spec); the differential fixtures also assert what v0 does on the same view, so
each v1 behavior is pinned as a *change*, not just an output.

Key shared fixture — TWO_TENPAI (14 tiles): B123 T456 W789 B567 + T2 + J1.
Discarding J1 leaves a tenpai waiting T2 (ron 13 fan); discarding T2 leaves a
tenpai waiting J1 (ron 11 fan). v0 tie-breaks the two 0.0-distance candidates
by tile_sort_key and always discards T2; v1's accounting can flip that.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.bots import v0
from mahjong.bots.belief import remaining_counts
from mahjong.bots.v1 import (
    FOLD_DISTANCE,
    FOLD_THREAT,
    decide_action,
    discard_danger,
    effective_distance,
    opponent_threat,
    tenpai_wait_ev,
    weighted_ukeire,
    win_value,
)
from mahjong.engine.rulesets import MANIFEST

pytestmark = pytest.mark.needs_pymjgb

HOUSE_REF: dict[str, Any] = {
    "id": "mcr-house-3fan",
    "version": 1,
    "config_hash": MANIFEST["mcr-house-3fan"],
}
CONFIG_3 = {"fan_cliff": 3}
CONFIG_8 = {"fan_cliff": 8}
HOUSE_CONV = {
    "scheme": "house-table",
    "tiers": [[1, 2], [2, 4], [3, 8], [6, 16], [9, 32], [15, 64], [23, 80], [43, 160]],
}

TWO_TENPAI = ["B1", "B2", "B3", "T4", "T5", "T6", "W7", "W8", "W9", "B5", "B6", "B7", "T2", "J1"]
# 1-shanten hand for the weighted-ukeire fixture: tied min-distance discards
# {B5,B6,J1,T2,T6,T7}, all type-ukeire 4; improver sets differ (probed):
#   B5/B6 -> {T2,T5,T8,J1}   J1/T2 -> {B4,B7,T5,T8}   T6/T7 -> {B4,B7,T2,J1}
UKEIRE_HAND = ["B1", "B2", "B3", "W7", "W8", "W9", "T2", "T2", "B5", "B6", "T6", "T7", "J1", "J1"]
# Junk hand, best post-discard distance 2.0; v0 discards W5 (probed).
FOLD_HAND = ["W2", "W2", "F1", "F1", "B3", "B3", "T7", "T7", "W5", "W9", "T1", "B9", "J3", "T4"]
# PENG W5 keeps distance 2.0 but lifts live-copy ukeire 24 -> 29 (probed).
CLAIM_EQUAL_HAND = ["W5", "W5", "W6", "W7", "B2", "B3", "T6", "T7", "J2", "J2", "B7", "B8", "F3"]
CLAIM_EQUAL_TILE = "W5"
# tenpai, ron 6 / self-draw 8: ron-feasible at floor 3, not at floor 8 (v0 fixture).
SELFDRAW_ONLY = ["B1", "B2", "B3", "B4", "B5", "B6", "T2", "T3", "T4", "T6", "T7", "T8", "W5"]


def _view(
    *,
    seat: int = 0,
    concealed: list[str],
    melds: list[dict[str, Any]] | None = None,
    seat_wind: str = "F2",
    round_wind: str = "F1",
    last_discard: dict[str, Any] | None = None,
    wall_remaining: int = 100,
    opponents: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
            base: dict[str, Any] = {
                "seat": i,
                "seat_wind": f"F{i + 1}",
                "concealed": {"count": 13},
                "melds": [],
                "discards": [],
                "flowers": [],
                "score": 0,
            }
            if opponents and i in opponents:
                base.update(opponents[i])
            seats.append(base)
    return {
        "ruleset": HOUSE_REF,
        "round_wind": round_wind,
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 0,
        "wall": {
            "remaining_count": wall_remaining,
            "drawn_count": 144 - wall_remaining,
            "total": 144,
        },
        "seats": seats,
        "last_discard": last_discard,
        "pending_claims": [],
        "phase": "DISCARD",
        "current_actor": seat,
        "terminal": None,
    }


def _plays(hand: list[str]) -> list[dict[str, Any]]:
    return [{"type": "PLAY", "tile": t} for t in sorted(set(hand))]


def _peng(tile: str, suffix: str = "") -> dict[str, Any]:
    return {"type": "PENG", "tiles": [tile] * 3, "called_tile": tile, "called_from_seat": 0}


# --- Fixture 5: HU / GANG unconditional (carried v0 contract) ---------------


def test_hu_unconditional() -> None:
    view = _view(concealed=["B1"] * 13)
    legal = [{"type": "PLAY", "tile": "B1"}, {"type": "HU"}]
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "HU"}


def test_hu_beats_gang() -> None:
    view = _view(concealed=["B1"] * 13)
    gang = {"type": "GANG", "tile": "B1", "kind": "CONCEALED"}
    assert decide_action(view, [gang, {"type": "HU"}], 0, "DISCARD") == {"type": "HU"}


# --- Gated GANG (v1 refinement of v0's always-GANG) --------------------------

# Isolated J2 quad: konging keeps the tenpai (d 0.0 either way; probed).
HARMLESS_KONG_HAND = [
    "J2",
    "J2",
    "J2",
    "J2",
    "B2",
    "B3",
    "B4",
    "T6",
    "T7",
    "T8",
    "W2",
    "W3",
    "F1",
    "F1",
]
# B4 quad serves B3-B4-B5 runs: konging strands B3/B5 (d 0.0 -> 1.0; probed).
WRECK_KONG_HAND = [
    "B3",
    "B4",
    "B4",
    "B4",
    "B4",
    "B5",
    "T1",
    "T2",
    "T3",
    "W7",
    "W8",
    "W9",
    "F1",
    "J2",
]


def test_gang_taken_when_harmless() -> None:
    view = _view(concealed=HARMLESS_KONG_HAND)
    gang = {"type": "GANG", "tile": "J2", "kind": "CONCEALED"}
    legal = [*_plays(HARMLESS_KONG_HAND), gang]
    assert decide_action(view, legal, 0, "DISCARD") == gang
    assert v0.decide_action(view, legal, 0, "DISCARD") == gang


def test_gang_refused_when_it_wrecks_the_hand() -> None:
    # v0 kongs and destroys its own tenpai; v1 keeps the tenpai discard.
    view = _view(concealed=WRECK_KONG_HAND)
    gang = {"type": "GANG", "tile": "B4", "kind": "CONCEALED"}
    legal = [*_plays(WRECK_KONG_HAND), gang]
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "F1"}
    assert v0.decide_action(view, legal, 0, "DISCARD") == gang


# --- Fixtures 6 + 8: dead waits and copy-weighted wait EV -------------------


def test_dead_tenpai_reshapes_where_v0_does_not() -> None:
    # All three J1s the hand doesn't hold are in a pond: the "discard T2, wait
    # J1" tenpai is provably dead. v1 keeps the live T2 wait; v0 (tie-break by
    # tile_sort_key) sits on the dead one.
    view = _view(
        concealed=TWO_TENPAI,
        opponents={1: {"discards": ["J1", "J1", "J1"]}},
    )
    legal = _plays(TWO_TENPAI)
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "J1"}
    assert v0.decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "T2"}


def test_effective_distance_dead_wait_is_subfloor() -> None:
    narrow = [t for t in TWO_TENPAI if t != "J1"]  # waits T2
    view = _view(concealed=narrow, opponents={1: {"discards": ["T2", "T2", "T2"]}})
    remaining = remaining_counts(view, 0)
    assert (
        effective_distance(narrow, [], "F2", "F1", CONFIG_3, remaining)
        == v0.SUBFLOOR_TENPAI_DISTANCE
    )
    # Live wait: plain view, no pond.
    live_remaining = remaining_counts(_view(concealed=narrow), 0)
    assert effective_distance(narrow, [], "F2", "F1", CONFIG_3, live_remaining) == 0.0


def test_tenpai_ev_prefers_more_live_copies() -> None:
    # Two J1s visible: waiting on J1 has 1 live copy, waiting on T2 has 3.
    # Both tenpais are live, so v0's choice (T2 discard) is unchanged; v1's
    # wait EV flips to the wider-in-copies wait.
    view = _view(
        concealed=TWO_TENPAI,
        opponents={1: {"discards": ["J1", "J1"]}},
    )
    legal = _plays(TWO_TENPAI)
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "J1"}
    assert v0.decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "T2"}


def test_win_value_and_wait_ev_tiers() -> None:
    assert win_value(3, HOUSE_CONV) == 8
    assert win_value(6, HOUSE_CONV) == 16
    assert win_value(7, HOUSE_CONV) == 32
    assert win_value(8, None) == 16  # official additive: fan + 8
    # Equal copies, different tiers: the higher tier dominates.
    remaining = {"T2": 2, "J1": 2}
    cheap = tenpai_wait_ev([("T2", 3)], remaining, HOUSE_CONV)
    rich = tenpai_wait_ev([("J1", 7)], remaining, HOUSE_CONV)
    assert rich > cheap
    assert tenpai_wait_ev([("T2", 3), ("J1", 7)], remaining, HOUSE_CONV) == cheap + rich


# --- Fixture 7: copy-weighted ukeire below tenpai ---------------------------


def test_weighted_ukeire_prefers_live_improvers() -> None:
    # T5/T8 nearly exhausted: keeping the T6T7 partial is dead weight. v1
    # discards T6 (improvers B4,B7,T2,J1 all live); v0 ties on type-count
    # ukeire and discards B5.
    view = _view(
        concealed=UKEIRE_HAND,
        opponents={1: {"discards": ["T5", "T5", "T5"]}, 2: {"discards": ["T8", "T8", "T8"]}},
    )
    legal = _plays(UKEIRE_HAND)
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "T6"}
    assert v0.decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "B5"}


def test_weighted_ukeire_sums_copies() -> None:
    hand = [t for t in UKEIRE_HAND if t != "B5"]  # improvers T2,T5,T8,J1 (probed)
    remaining = {t: 0 for t in {"T2", "T5", "T8", "J1"}}
    remaining.update({"T2": 2, "T5": 1})
    full = dict.fromkeys([f"{s}{r}" for s in "WBT" for r in range(1, 10)], 4)
    full.update({f"F{r}": 4 for r in range(1, 5)})
    full.update({f"J{r}": 4 for r in range(1, 4)})
    full.update(remaining)
    assert weighted_ukeire(hand, [], full) == 3


# --- Fixture 9: threat ordering ----------------------------------------------


def test_threat_level_ordering() -> None:
    quiet = {
        "seat": 1,
        "seat_wind": "F2",
        "concealed": {"count": 13},
        "melds": [],
        "discards": [],
        "flowers": [],
        "score": 0,
    }
    two_melds = dict(quiet, melds=[_peng("W3"), _peng("T9")])
    early = _view(concealed=["B1"] * 13, wall_remaining=100)
    late = _view(concealed=["B1"] * 13, wall_remaining=30)
    endgame = _view(concealed=["B1"] * 13, wall_remaining=20)

    t_quiet = opponent_threat(quiet, early)
    t_two = opponent_threat(two_melds, early)
    t_two_late = opponent_threat(two_melds, late)
    t_two_endgame = opponent_threat(two_melds, endgame)
    assert t_quiet.level < t_two.level < t_two_late.level < t_two_endgame.level
    # Fold needs a 3-meld opponent anytime, or a 2-meld opponent in the
    # endgame — never a 2-meld opponent early (the first eval's failure mode).
    assert t_two.level < FOLD_THREAT <= t_two_endgame.level
    three_early = opponent_threat(dict(quiet, melds=[_peng("W3"), _peng("T9"), _peng("J2")]), early)
    assert three_early.level >= FOLD_THREAT
    three_endgame = opponent_threat(
        dict(quiet, melds=[_peng("W3"), _peng("T9"), _peng("J2")]), endgame
    )
    assert three_endgame.level == 1.0  # 0.9 + 0.2 capped


def test_threat_hot_suit() -> None:
    view = _view(concealed=["B1"] * 13)
    base = {
        "seat": 1,
        "seat_wind": "F2",
        "concealed": {"count": 13},
        "melds": [],
        "discards": [],
        "flowers": [],
        "score": 0,
    }
    same_suit = dict(
        base,
        melds=[
            _peng("T3"),
            {
                "type": "CHI",
                "tiles": ["T5", "T6", "T7"],
                "called_tile": "T5",
                "called_from_seat": 0,
            },
        ],
    )
    assert opponent_threat(same_suit, view).hot_suit == "T"
    mixed = dict(base, melds=[_peng("T3"), _peng("W4")])
    assert opponent_threat(mixed, view).hot_suit is None
    with_honor = dict(base, melds=[_peng("T3"), _peng("J2")])
    assert opponent_threat(with_honor, view).hot_suit == "T"
    single = dict(base, melds=[_peng("T3")])
    assert opponent_threat(single, view).hot_suit is None


# --- Fixture 10: danger ordering ---------------------------------------------


def test_danger_ordering() -> None:
    hand = ["T5", "W5", "W1", "F2", "B5"] + ["B1"] * 9
    view = _view(
        concealed=hand,
        wall_remaining=100,
        opponents={
            1: {
                "melds": [
                    _peng("T3"),
                    {
                        "type": "CHI",
                        "tiles": ["T6", "T7", "T8"],
                        "called_tile": "T6",
                        "called_from_seat": 0,
                    },
                ]
            },
            2: {"discards": ["F2", "F2", "F2"]},
        },
    )
    remaining = remaining_counts(view, 0)
    threats = {i: opponent_threat(view["seats"][i], view) for i in (1, 2, 3)}

    hot_middle = discard_danger("T5", view, 0, remaining, threats)
    plain_middle = discard_danger("W5", view, 0, remaining, threats)
    terminal = discard_danger("W1", view, 0, remaining, threats)
    dead_honor = discard_danger("F2", view, 0, remaining, threats)
    assert hot_middle > plain_middle > terminal > dead_honor == 0.0


def test_danger_their_own_discard_is_discounted() -> None:
    view = _view(
        concealed=["W5"] + ["B1"] * 13,
        opponents={1: {"melds": [_peng("T3"), _peng("T9")], "discards": ["W5"]}},
    )
    remaining = remaining_counts(view, 0)
    threats = {i: opponent_threat(view["seats"][i], view) for i in (1, 2, 3)}
    discounted = discard_danger("W5", view, 0, remaining, threats)

    view_fresh = _view(
        concealed=["W5"] + ["B1"] * 13,
        opponents={1: {"melds": [_peng("T3"), _peng("T9")]}},
    )
    remaining_f = remaining_counts(view_fresh, 0)
    threats_f = {i: opponent_threat(view_fresh["seats"][i], view_fresh) for i in (1, 2, 3)}
    fresh = discard_danger("W5", view_fresh, 0, remaining_f, threats_f)
    assert 0.0 < discounted < fresh


def test_danger_no_chance_suited_tile_is_safe() -> None:
    # All other B5s and every B4/B6 visible: no run window and no pair can use
    # a discarded B5 — provably safe despite being a middle tile.
    view = _view(
        concealed=["B5"] + ["W1"] * 13,
        opponents={
            1: {"melds": [_peng("T3"), _peng("T9")], "discards": ["B5", "B5", "B5", "B4", "B4"]},
            2: {"discards": ["B4", "B4", "B6", "B6", "B6", "B6"]},
        },
    )
    remaining = remaining_counts(view, 0)
    threats = {i: opponent_threat(view["seats"][i], view) for i in (1, 2, 3)}
    assert discard_danger("B5", view, 0, remaining, threats) == 0.0


# --- Fixtures 11 + 12: push/fold ----------------------------------------------


def test_fold_engages_against_visible_threat() -> None:
    # Hopeless hand (best distance 2.0 >= FOLD_DISTANCE), opponent with two
    # melds in the endgame (wall 20): fold to the provably-safe F1 (two held +
    # two in the pond). v0 plays the offense pick W5 on the identical view.
    assert FOLD_DISTANCE <= 2.0
    view = _view(
        concealed=FOLD_HAND,
        wall_remaining=20,
        opponents={1: {"melds": [_peng("W3"), _peng("T9")], "discards": ["F1", "F1"]}},
    )
    legal = _plays(FOLD_HAND)
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "F1"}
    assert v0.decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "W5"}


def test_fold_disengages_without_threat() -> None:
    view = _view(
        concealed=FOLD_HAND,
        wall_remaining=50,
        opponents={1: {"discards": ["F1", "F1"]}},
    )
    action = decide_action(view, _plays(FOLD_HAND), 0, "DISCARD")
    assert action != {"type": "PLAY", "tile": "F1"}  # no fold; offense rules


# --- Careful push (Spec 35, middle regime) ----------------------------------

# No floaters: every min-distance discard (T4/T5/T7/T8/W8/W9, all d=1.0) is a
# live suited tile; breaking the exhausted F2 pair costs a step (d=2.0). Probed.
CAUTION_HAND = ["B2", "B3", "B4", "W4", "W5", "W6", "T4", "T5", "T7", "T8", "F2", "F2", "W8", "W9"]


def test_careful_push_pays_a_step_for_safety() -> None:
    # 2-meld opponent at wall 40 (threat 0.7): every fastest discard is
    # dangerous, F2 is provably safe (2 held + 2 in pond) one step behind ->
    # v1 gives up the step; v0 plays W8 (probed offense pick).
    view = _view(
        concealed=CAUTION_HAND,
        wall_remaining=40,
        opponents={1: {"melds": [_peng("J2"), _peng("B9")], "discards": ["F2", "F2"]}},
    )
    legal = _plays(CAUTION_HAND)
    assert decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "F2"}
    assert v0.decide_action(view, legal, 0, "DISCARD") == {"type": "PLAY", "tile": "W8"}


def test_careful_push_needs_a_real_threat() -> None:
    # Same hand, same pond, but early (wall 100 -> threat 0.6 < CAUTION_THREAT):
    # v1 keeps pushing the fastest shape.
    view = _view(
        concealed=CAUTION_HAND,
        wall_remaining=100,
        opponents={1: {"melds": [_peng("J2"), _peng("B9")], "discards": ["F2", "F2"]}},
    )
    action = decide_action(view, _plays(CAUTION_HAND), 0, "DISCARD")
    assert action != {"type": "PLAY", "tile": "F2"}


def test_careful_push_never_breaks_live_tenpai() -> None:
    # Tenpai with a live wait against the same threat: we are racing too —
    # no caution, the tenpai-keeping discard stands.
    view = _view(
        concealed=TWO_TENPAI,
        wall_remaining=40,
        opponents={1: {"melds": [_peng("J2"), _peng("B9")], "discards": []}},
    )
    action = decide_action(view, _plays(TWO_TENPAI), 0, "DISCARD")
    assert action in ({"type": "PLAY", "tile": "T2"}, {"type": "PLAY", "tile": "J1"})


# --- Fixture 13: claim logic carried -------------------------------------------


def test_beneficial_peng_taken() -> None:
    hand = ["W5", "W5", "B2", "B3", "B4", "T6", "T7", "T8", "J2", "J2", "W1", "W9", "F3"]
    view = _view(
        concealed=hand,
        last_discard={"tile": "W5", "seat": 3, "turn_index": 10},
    )
    peng = {"type": "PENG", "tile": "W5"}
    legal = [{"type": "PASS"}, peng]
    assert decide_action(view, legal, 0, "CLAIM") == peng


def test_equal_distance_claim_still_refused() -> None:
    # PENG W5 keeps distance equal while widening live-copy ukeire (24 -> 29,
    # probed) — and v1 still passes. Claiming on equal distance was tried and
    # measured WORSE (-5 pts/hand, CRN eval r4); this pins the rejection so a
    # future "obvious improvement" doesn't sneak back in untested.
    view = _view(
        concealed=CLAIM_EQUAL_HAND,
        last_discard={"tile": CLAIM_EQUAL_TILE, "seat": 3, "turn_index": 10},
    )
    legal = [{"type": "PASS"}, {"type": "PENG", "tile": CLAIM_EQUAL_TILE}]
    assert decide_action(view, legal, 0, "CLAIM") == {"type": "PASS"}


def test_useless_chi_refused() -> None:
    tenpai = [t for t in TWO_TENPAI if t != "J1"]  # waits T2, distance 0.0
    view = _view(
        concealed=tenpai,
        last_discard={"tile": "T3", "seat": 3, "turn_index": 10},
    )
    chi = {"type": "CHI", "tiles": ["T3", "T4", "T5"]}
    legal = [{"type": "PASS"}, chi]
    assert decide_action(view, legal, 0, "CLAIM") == {"type": "PASS"}


# --- Fixture 14: floor-conditioned ---------------------------------------------


def test_floor_conditioned_feasibility() -> None:
    view = _view(concealed=SELFDRAW_ONLY)
    remaining = remaining_counts(view, 0)
    assert effective_distance(SELFDRAW_ONLY, [], "F2", "F1", CONFIG_3, remaining) == 0.0
    assert (
        effective_distance(SELFDRAW_ONLY, [], "F2", "F1", CONFIG_8, remaining)
        == v0.SUBFLOOR_TENPAI_DISTANCE
    )


# --- Fixture 15 (policy half): determinism --------------------------------------


def test_decide_action_deterministic() -> None:
    view = _view(
        concealed=UKEIRE_HAND,
        opponents={1: {"melds": [_peng("W3")], "discards": ["T5", "T5"]}},
    )
    legal = _plays(UKEIRE_HAND)
    first = decide_action(view, legal, 0, "DISCARD")
    assert all(decide_action(view, legal, 0, "DISCARD") == first for _ in range(5))
