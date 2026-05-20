"""Step 1.2 — PyMahjongGB wrapper.

Spec: docs/specs/engine-api.md § PyMahjongGB integration boundary,
      engine-api.md fixture 4 (per-wrapper canonical example).

The wrappers exist to (a) own the conversion from our types to PyMahjongGB's
tuple-of-strings format, (b) enforce the MCR 8-fan cliff, (c) be the single
import boundary so a future PyMahjongGB version bump touches one file.

The AST lint in tests/lint/test_engine_purity.py separately enforces that
`MahjongGB` is only imported from `mahjong/engine/pymj.py`.
"""

from __future__ import annotations

import pytest

from mahjong.engine.pymj import (
    calculate_fan,
    shanten,
    shanten_specialized,
    winning_tiles,
)

pytestmark = pytest.mark.needs_pymjgb


# --- calculate_fan ---


def test_calculate_fan_four_concealed_pungs_self_drawn() -> None:
    """fixture 4: a concrete MCR-canonical winning hand.

    Hand: W1W1W1 W2W2W2 B3B3B3 T4T4T4 T5 (T5 self-drawn).
    Expected: a non-empty fan list including "Four Concealed Pungs"
    (the 64-fan headline yaku), total well above the 8-fan cliff.
    """
    fans = calculate_fan(
        hand=["W1", "W1", "W1", "W2", "W2", "W2", "B3", "B3", "B3", "T4", "T4", "T4", "T5"],
        melds=[],
        win_tile="T5",
        win_type="SELF_DRAW",
        seat_wind="F1",
        round_wind="F1",
        ruleset_config={"id": "mcr-2006"},
    )
    assert fans, "expected a non-empty fan list for a winning hand"
    names = {entry["name"] for entry in fans}
    assert "Four Concealed Pungs" in names
    total = sum(entry["value"] for entry in fans)
    assert total >= 8, "total fan must clear the MCR 8-fan cliff"


def test_calculate_fan_returns_fanentry_shape() -> None:
    """Every returned entry has the FanEntry shape: {'name': str, 'value': int}."""
    fans = calculate_fan(
        hand=["W1", "W1", "W1", "W2", "W2", "W2", "B3", "B3", "B3", "T4", "T4", "T4", "T5"],
        melds=[],
        win_tile="T5",
        win_type="SELF_DRAW",
        seat_wind="F1",
        round_wind="F1",
        ruleset_config={"id": "mcr-2006"},
    )
    for entry in fans:
        assert set(entry.keys()) == {"name", "value"}
        assert isinstance(entry["name"], str)
        assert isinstance(entry["value"], int)
        assert entry["value"] > 0


def test_calculate_fan_returns_empty_on_non_winning_input() -> None:
    """Sub-winning input → []. Engine-api.md: the cliff is the wrapper's job."""
    fans = calculate_fan(
        hand=["W1", "W3", "W5", "B2", "B4", "B6", "T7", "T9", "F1", "J1", "J2", "J3", "J3"],
        melds=[],
        win_tile="H1",
        win_type="SELF_DRAW",
        seat_wind="F1",
        round_wind="F1",
        ruleset_config={"id": "mcr-2006"},
    )
    assert fans == []


# --- shanten ---


def test_shanten_on_tenpai_hand_is_zero() -> None:
    """A 13-tile hand waiting on J1 to complete reports shanten 0."""
    hand = ["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "B1", "B2", "B3", "J1"]
    assert shanten(hand, melds=[]) == 0


def test_shanten_far_from_tenpai_is_positive() -> None:
    """Junk 13-tile hand reports a positive shanten count.

    PyMahjongGB's `MahjongShanten` requires a standing-position hand
    (len(hand) + 3*len(melds) == 13); "already won" 14-tile detection
    goes through `winning_tiles` / `calculate_fan`, not through shanten.
    """
    hand = ["W1", "W3", "W5", "B2", "B4", "B6", "T7", "T9", "F1", "J1", "J2", "J3", "J3"]
    assert shanten(hand, melds=[]) > 0


# --- shanten_specialized ---


def test_shanten_specialized_seven_pairs() -> None:
    """Seven Pairs variant shanten: a hand at 0-shanten for seven pairs."""
    # Six pairs + one single: one away from seven pairs.
    hand = ["W1", "W1", "W2", "W2", "B3", "B3", "B4", "B4", "T5", "T5", "T6", "T6", "J1"]
    assert shanten_specialized(hand, variant="SEVEN_PAIRS") == 0


def test_shanten_specialized_thirteen_orphans() -> None:
    """Thirteen Orphans: the canonical 13-terminal-and-honor singles."""
    hand = ["W1", "W9", "B1", "B9", "T1", "T9", "F1", "F2", "F3", "F4", "J1", "J2", "J3"]
    assert shanten_specialized(hand, variant="THIRTEEN_ORPHANS") == 0


def test_shanten_specialized_rejects_unknown_variant() -> None:
    hand = ["W1"] * 13
    with pytest.raises(ValueError):
        shanten_specialized(hand, variant="NOT_A_REAL_VARIANT")  # type: ignore[arg-type]


# --- winning_tiles ---


def test_winning_tiles_on_tenpai_returns_the_completing_tile() -> None:
    """A 13-tile hand waiting on exactly J1 reports J1 in its winning tiles."""
    hand = ["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "B1", "B2", "B3", "J1"]
    wts = winning_tiles(hand, melds=[])
    assert "J1" in wts
    # All returned tokens are valid tile strings.
    from mahjong.engine.tiles import validate_tile

    assert all(validate_tile(t) for t in wts)


def test_winning_tiles_empty_when_not_tenpai() -> None:
    """Not tenpai → no winning tiles."""
    hand = ["W1", "W3", "W5", "B2", "B4", "B6", "T7", "T9", "F1", "J1", "J2", "J3", "J3"]
    assert winning_tiles(hand, melds=[]) == []
