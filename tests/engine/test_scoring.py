"""Tests for `mahjong.engine.scoring` — the config-driven fan->score-delta scorer.

Spec: docs/specs/scoring-config.md § The config schema, § Verification fixtures.

This is the RL reward contract (CLAUDE.md: "Reward shape is a tested contract").
Every (winner, fan_total, win_type, deal_in_seat, conversion) -> score_delta is
pinned here *before* anything consumes it. The scorer is pure arithmetic over
`fan_total`; no real hand is needed.

Two schemes:
  - `mcr-official`: additive `fan + base`. The default; reproduces the formula
    the HU transition hard-coded before this change.
  - `house-table`: tier lookup -> per-loser multiplier. Winner delta is derived
    as -sum(losers), so zero-sum is structural.
"""

from __future__ import annotations

from mahjong.engine.scoring import lookup_x, score_delta

# The canonical house fan->X table (scoring-config.md § house-table).
HOUSE_TIERS = [
    [1, 2],
    [2, 4],
    [3, 8],
    [6, 16],
    [9, 32],
    [15, 64],
    [23, 80],
    [43, 160],
    [63, 240],
    [87, 360],
    [88, 500],
]
HOUSE_CONVERSION = {
    "scheme": "house-table",
    "tiers": HOUSE_TIERS,
    "self_draw": {"each_mult": 2},
    "discard": {"dealer_in_mult": 2, "other_mult": 1},
}


# --- fixture 2: mcr-official reproduces the pre-change formula ---


def test_official_self_draw_matches_legacy_formula() -> None:
    """Self-draw: each of three losers pays (fan + 8); winner receives the sum."""
    delta = score_delta(winner=0, fan_total=10, win_type="SELF_DRAW", deal_in_seat=None)
    assert delta == [54, -18, -18, -18]  # 3*(10+8)=54
    assert sum(delta) == 0


def test_official_discard_matches_legacy_formula() -> None:
    """Discard: dealer-in pays (fan + 24); other two pay a flat 8."""
    delta = score_delta(winner=0, fan_total=10, win_type="DISCARD", deal_in_seat=2)
    assert delta == [50, -8, -34, -8]  # (10+24) + 8 + 8 = 50
    assert sum(delta) == 0


def test_official_is_the_default_when_conversion_absent() -> None:
    """No conversion block => mcr-official. This is what keeps mcr-2006 byte-identical."""
    explicit = {"scheme": "mcr-official"}
    assert score_delta(0, 10, "SELF_DRAW", None) == score_delta(
        0, 10, "SELF_DRAW", None, conversion=explicit
    )


def test_official_winner_seat_independence() -> None:
    """A non-zero winner seat lands the credit on that seat, still zero-sum."""
    delta = score_delta(winner=3, fan_total=8, win_type="DISCARD", deal_in_seat=0)
    assert delta[3] == 8 + 24 + 8 + 8  # dealer-in (32) + two flats (8 each)
    assert delta[0] == -(8 + 24)
    assert delta[1] == -8 and delta[2] == -8
    assert sum(delta) == 0


# --- fixture 3: house tier lookup, including the over-88 clamp ---


def test_house_lookup_x_tier_edges() -> None:
    """X(fan) at every tier boundary and one over-cap fan."""
    expected = {
        1: 2,
        2: 4,
        3: 8,
        4: 16,
        6: 16,
        7: 32,
        9: 32,
        10: 64,
        15: 64,
        16: 80,
        23: 80,
        24: 160,
        43: 160,
        44: 240,
        63: 240,
        64: 360,
        87: 360,
        88: 500,
        120: 500,  # 120 > 88 clamps to the top tier
    }
    for fan, x in expected.items():
        assert lookup_x(fan, HOUSE_TIERS) == x, f"fan={fan}"


# --- fixtures 4 & 5: house payouts, zero-sum, self-draw == 1.5x discard ---


def test_house_discard_payout_zero_sum() -> None:
    """Discard win at 3 fan (X=8): winner +4X, dealer-in -2X, each other -X."""
    delta = score_delta(0, 3, "DISCARD", deal_in_seat=1, conversion=HOUSE_CONVERSION)
    assert delta == [32, -16, -8, -8]  # +4*8, -2*8, -8, -8
    assert sum(delta) == 0


def test_house_self_draw_payout_zero_sum() -> None:
    """Self-draw at 3 fan (X=8): winner +6X, each loser -2X."""
    delta = score_delta(0, 3, "SELF_DRAW", deal_in_seat=None, conversion=HOUSE_CONVERSION)
    assert delta == [48, -16, -16, -16]  # +6*8, -2*8 each
    assert sum(delta) == 0


def test_house_self_draw_is_one_and_a_half_discard() -> None:
    """The deliberate house lever: self-draw pays 1.5x a discard win at equal X."""
    for fan in (3, 9, 24, 88):
        discard = score_delta(0, fan, "DISCARD", 1, conversion=HOUSE_CONVERSION)
        self_draw = score_delta(0, fan, "SELF_DRAW", None, conversion=HOUSE_CONVERSION)
        assert self_draw[0] == discard[0] * 3 // 2, f"fan={fan}"


def test_house_high_fan_uses_clamped_x() -> None:
    """A 100-fan self-draw uses the clamped top X (500): winner +6*500=3000."""
    delta = score_delta(0, 100, "SELF_DRAW", None, conversion=HOUSE_CONVERSION)
    assert delta == [3000, -1000, -1000, -1000]
    assert sum(delta) == 0
