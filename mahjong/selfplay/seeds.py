"""Per-hand seed derivation and seat rotation.

Spec: docs/specs/selfplay-harness.md § Seed management.
"""

from __future__ import annotations

import hashlib


def hand_seed(master_seed: int, hand_index: int) -> int:
    """Per-hand seed = SHA-256(master_seed_bytes || hand_index_bytes) truncated to 128 bits."""
    payload = master_seed.to_bytes(16, "big") + hand_index.to_bytes(16, "big")
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], "big")


def rotate_bots[T](bots: list[T], hand_index: int) -> list[T]:
    """Deterministic round-robin cyclic shift by `hand_index % 4`.

    Shift is *right* (hand 1 places seat 0's bot at seat 1) so the dealer
    rotates with the wind progression rather than against it.
    """
    if len(bots) != 4:
        raise ValueError(f"rotate_bots expects exactly 4 bots, got {len(bots)}")
    k = hand_index % 4
    return bots[-k:] + bots[:-k] if k else list(bots)


__all__ = ["hand_seed", "rotate_bots"]
