"""Step 0.3 — canonical hash (fixture 5).

Spec: docs/specs/determinism.md § The canonical hash.
"""

from __future__ import annotations

import pytest

from mahjong.engine.hashing import canonical_hash
from tests.conftest import load_golden


@pytest.mark.determinism
def test_canonical_hash_golden() -> None:
    """determinism.md fixture 5 — canonical_hash output is locked across inputs."""
    for case in load_golden("canonical_hash.json"):
        assert canonical_hash(case["input"]) == case["hash"], case


def test_canonical_hash_key_order_invariance() -> None:
    """Same dict in different key orders → same hash (sort_keys does the work)."""
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_canonical_hash_distinguishes_distinct_inputs() -> None:
    a = canonical_hash({"a": 1})
    b = canonical_hash({"a": 2})
    c = canonical_hash([1])
    d = canonical_hash([1, 1])
    assert len({a, b, c, d}) == 4


def test_canonical_hash_rejects_floats() -> None:
    """Floats are a contract violation — JSON float drift would corrupt hashes."""
    with pytest.raises(TypeError):
        canonical_hash(1.5)
    with pytest.raises(TypeError):
        canonical_hash({"x": 1.0})
    with pytest.raises(TypeError):
        canonical_hash([1, 2, 3.0])
    with pytest.raises(TypeError):
        canonical_hash({"nested": {"y": [1, 2.0]}})


def test_canonical_hash_accepts_bools_and_none() -> None:
    """bool is an int subclass; ensure it's allowed (no false float trigger)."""
    canonical_hash(True)
    canonical_hash(False)
    canonical_hash(None)
    canonical_hash({"flag": True, "missing": None})


def test_canonical_hash_prefix_is_sha256() -> None:
    h = canonical_hash({})
    assert h.startswith("sha256:")
    # 64 hex chars after the prefix
    assert len(h) == len("sha256:") + 64
