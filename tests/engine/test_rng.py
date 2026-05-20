"""Step 0.3 — determinism primitives (RNG side).

Spec: docs/specs/determinism.md § The RNG (fixtures 1, 3, 4).

The goldens here are the **load-bearing** determinism contract: a change
to any of them invalidates every record ever written. If a test fails,
read the diff before touching the golden — see determinism.md § Refactor
protocol.
"""

from __future__ import annotations

from collections import Counter

import pytest

from mahjong.engine.rng import rng_bytes, shuffled_wall, uniform_int
from mahjong.engine.tiles import canonical_tile_set
from tests.conftest import load_golden

# --- rng_bytes (fixture 1) ---


@pytest.mark.determinism
def test_rng_bytes_golden() -> None:
    """determinism.md fixture 1 — SHA-256 counter DRBG byte stream is locked."""
    for case in load_golden("rng_bytes.json"):
        actual = rng_bytes(case["seed"], case["cursor"], case["n"]).hex()
        assert actual == case["hex"], case


def test_rng_bytes_zero_length() -> None:
    assert rng_bytes(0, 0, 0) == b""
    assert rng_bytes(12345, 99, 0) == b""


def test_rng_bytes_block_boundary_is_seamless() -> None:
    """Two reads spanning a block boundary concatenate to one larger read."""
    a = rng_bytes(7, 30, 2)
    b = rng_bytes(7, 32, 30)
    one_shot = rng_bytes(7, 30, 32)
    assert a + b == one_shot


def test_rng_bytes_rejects_negative_inputs() -> None:
    with pytest.raises(ValueError):
        rng_bytes(0, -1, 4)
    with pytest.raises(ValueError):
        rng_bytes(0, 0, -1)
    with pytest.raises(ValueError):
        rng_bytes(-1, 0, 4)
    with pytest.raises(ValueError):
        rng_bytes(1 << 128, 0, 4)  # seed must fit in 128 bits


# --- uniform_int (fixture 3) ---


@pytest.mark.determinism
def test_uniform_int_golden() -> None:
    """determinism.md fixture 3 — rejection-sampling table."""
    for case in load_golden("uniform_int.json"):
        value, cursor_after = uniform_int(case["seed"], case["cursor"], case["upper"])
        assert value == case["value"], case
        assert cursor_after == case["cursor_after"], case


def test_uniform_int_n1_consumes_no_bytes() -> None:
    """upper_inclusive=0 means n=1 — no draw needed, cursor unchanged."""
    assert uniform_int(0, 0, 0) == (0, 0)
    assert uniform_int(12345, 50, 0) == (0, 50)


def test_uniform_int_range_membership() -> None:
    """A few draws across small ranges land within [0, upper]."""
    for upper in (1, 7, 33, 143):
        cursor = 0
        for _ in range(20):
            value, cursor = uniform_int(42, cursor, upper)
            assert 0 <= value <= upper


# --- shuffled_wall (fixture 4) ---


@pytest.mark.determinism
def test_shuffled_wall_seed_12345_golden() -> None:
    """determinism.md fixture 4 — *the* load-bearing fixture.

    Any change here invalidates every prior record. Read the diff before
    updating this golden.
    """
    golden = load_golden("shuffled_wall_12345.json")
    wall, cursor_after = shuffled_wall(12345)
    assert wall == golden["wall"]
    assert cursor_after == golden["cursor_after"]


def test_shuffled_wall_preserves_multiset() -> None:
    """Fisher-Yates is a permutation — the multiset of tiles is unchanged."""
    wall, _ = shuffled_wall(12345)
    assert Counter(wall) == Counter(canonical_tile_set())
    assert len(wall) == 144


def test_shuffled_wall_is_deterministic() -> None:
    """Same seed → same wall + same cursor_after, across calls."""
    a = shuffled_wall(12345)
    b = shuffled_wall(12345)
    assert a == b


def test_shuffled_wall_different_seeds_diverge() -> None:
    """Different seeds produce different walls (sanity, not a strict guarantee)."""
    a, _ = shuffled_wall(1)
    b, _ = shuffled_wall(2)
    assert a != b
