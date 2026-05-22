"""Tests for `mahjong.selfplay.seeds`.

Spec: docs/specs/selfplay-harness.md § Seed management.

Pins:
  - `hand_seed(master, idx)` = int.from_bytes(SHA-256(master||idx)[:16]).
  - `rotate_bots(bots, idx)` is a deterministic cyclic shift by `idx % 4`.
"""

from __future__ import annotations

import hashlib

import pytest

from mahjong.selfplay.seeds import hand_seed, rotate_bots


def _reference_hand_seed(master: int, idx: int) -> int:
    payload = master.to_bytes(16, "big") + idx.to_bytes(16, "big")
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], "big")


@pytest.mark.parametrize("idx", [0, 1, 7, 42, 10000])
def test_hand_seed_matches_spec_formula(idx: int) -> None:
    master = 0xDEADBEEF12345678
    assert hand_seed(master, idx) == _reference_hand_seed(master, idx)


def test_hand_seed_decorrelates_shared_components() -> None:
    master = 0xDEADBEEF12345678
    seeds = {hand_seed(master, idx) for idx in range(64)}
    assert len(seeds) == 64  # all distinct
    other_master_seeds = {hand_seed(master + 1, idx) for idx in range(64)}
    # changing only the master gives a disjoint set with overwhelming probability.
    assert not (seeds & other_master_seeds)


def test_hand_seed_is_128_bits() -> None:
    s = hand_seed(0xDEADBEEF12345678, 0)
    assert 0 <= s < 2**128


def test_rotate_bots_identity_at_zero() -> None:
    bots = ["a", "b", "c", "d"]
    assert rotate_bots(bots, 0) == ["a", "b", "c", "d"]


@pytest.mark.parametrize(
    "idx,expected",
    [
        (0, ["a", "b", "c", "d"]),
        (1, ["d", "a", "b", "c"]),
        (2, ["c", "d", "a", "b"]),
        (3, ["b", "c", "d", "a"]),
        (4, ["a", "b", "c", "d"]),  # cycle
        (7, ["b", "c", "d", "a"]),
    ],
)
def test_rotate_bots_cyclic_shift(idx: int, expected: list[str]) -> None:
    assert rotate_bots(["a", "b", "c", "d"], idx) == expected


def test_rotate_bots_requires_length_four() -> None:
    with pytest.raises(ValueError):
        rotate_bots(["a", "b", "c"], 0)
