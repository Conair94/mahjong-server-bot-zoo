"""Step 0.2 — tile encoding.

Spec: docs/specs/state-schema.md § Tile encoding,
      docs/specs/determinism.md fixture 2 (canonical_tile_set golden).
"""

from __future__ import annotations

import random

import pytest

from mahjong.engine.tiles import (
    canonical_tile_set,
    tile_sort_key,
    validate_tile,
)
from tests.conftest import load_golden

# --- Validation ---

VALID_TOKENS: list[str] = (
    [f"W{r}" for r in range(1, 10)]
    + [f"B{r}" for r in range(1, 10)]
    + [f"T{r}" for r in range(1, 10)]
    + [f"F{r}" for r in range(1, 5)]
    + [f"J{r}" for r in range(1, 4)]
    + [f"H{r}" for r in range(1, 9)]
)


@pytest.mark.parametrize("token", VALID_TOKENS)
def test_validate_tile_accepts_every_legal_token(token: str) -> None:
    assert validate_tile(token) is True


@pytest.mark.parametrize(
    "token",
    [
        "",  # empty
        "X1",  # bad suit
        "W0",  # rank below range
        "W10",  # rank above range (and two-digit)
        "w1",  # lowercase
        "W",  # missing rank
        "1W",  # reversed
        "F5",  # winds only go to 4
        "J4",  # dragons only go to 3
        "H9",  # bonus tiles only go to 8
        "H0",
        "WW",
        " W1",  # whitespace
        "W1 ",
    ],
)
def test_validate_tile_rejects_invalid(token: str) -> None:
    assert validate_tile(token) is False


# --- Canonical set ---


def test_canonical_tile_set_matches_golden() -> None:
    """determinism.md fixture 2: the 144-token canonical order is locked."""
    expected = load_golden("canonical_tile_set.json")
    actual = canonical_tile_set()
    assert actual == expected
    assert len(actual) == 144


def test_canonical_tile_set_is_fresh_list_each_call() -> None:
    """Caller mutation must not corrupt the shared canonical order."""
    a = canonical_tile_set()
    a[0] = "MUTATED"
    b = canonical_tile_set()
    assert b[0] == "W1"


# --- Sort order ---


def test_canonical_sort_matches_canonical_set() -> None:
    """A shuffled copy of the canonical set sorts back to canonical order."""
    canonical = canonical_tile_set()
    shuffled = canonical[:]
    random.Random(0).shuffle(shuffled)
    assert sorted(shuffled, key=tile_sort_key) == canonical


def test_sort_key_is_section_then_rank() -> None:
    """W < B < T < F < J < H, and within a section rank is ascending."""
    sample = ["H1", "J3", "W9", "B1", "F4", "T5", "W1"]
    assert sorted(sample, key=tile_sort_key) == ["W1", "W9", "B1", "T5", "F4", "J3", "H1"]
