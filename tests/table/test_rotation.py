"""Tests for `mahjong.table.rotation.next_dealer` — config-driven renchan.

Spec: docs/specs/scoring-config.md § Renchan, § Verification fixtures (8).

Pins the single shared next-dealer decision so the registry and web match
loops can't drift.
"""

from __future__ import annotations

from typing import Any

from mahjong.engine.types import Terminal
from mahjong.table.rotation import next_dealer

RENCHAN: dict[str, Any] = {"dealer_repeat_on_win": True}
NO_RENCHAN: dict[str, Any] = {}  # absent flag == official MCR (always rotate)


def _hu(winner: int) -> Terminal:
    return {
        "kind": "HU",
        "winner": winner,
        "win_tile": "B9",
        "win_type": "SELF_DRAW",
        "deal_in_seat": None,
        "fan": [],
        "fan_total": 6,
        "score_delta": [0, 0, 0, 0],
    }


def _draw() -> Terminal:
    return {
        "kind": "DRAW",
        "winner": None,
        "win_tile": None,
        "win_type": None,
        "deal_in_seat": None,
        "fan": [],
        "fan_total": 0,
        "score_delta": [0, 0, 0, 0],
    }


# --- renchan enabled ---


def test_renchan_dealer_win_repeats() -> None:
    assert next_dealer(2, _hu(winner=2), RENCHAN) == 2


def test_renchan_non_dealer_win_rotates() -> None:
    assert next_dealer(2, _hu(winner=0), RENCHAN) == 3


def test_renchan_draw_rotates() -> None:
    """An exhaustive draw is not a dealer win → rotate (default house behaviour)."""
    assert next_dealer(3, _draw(), RENCHAN) == 0  # wraps 3 -> 0


# --- renchan disabled / absent (mcr-2006) ---


def test_no_renchan_always_rotates_even_on_dealer_win() -> None:
    assert next_dealer(1, _hu(winner=1), NO_RENCHAN) == 2


def test_no_renchan_wraps() -> None:
    assert next_dealer(3, _hu(winner=0), NO_RENCHAN) == 0


def test_none_terminal_rotates() -> None:
    """Defensive: a missing terminal never triggers renchan."""
    assert next_dealer(0, None, RENCHAN) == 1
