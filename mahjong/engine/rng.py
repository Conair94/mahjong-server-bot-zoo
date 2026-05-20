"""Canonical deterministic RNG: rng_bytes, uniform_int, shuffled_wall.

Spec: docs/specs/determinism.md § The RNG.

SHA-256 counter DRBG. This is the only randomness source the engine ever
uses. `random`, `numpy.random`, `time`, `datetime`, `logging` are forbidden
under mahjong.engine.* (enforced by the AST lint in tests/lint/).
"""

from __future__ import annotations

import hashlib

from mahjong.engine.tiles import Tile, canonical_tile_set

_BLOCK_SIZE = 32  # SHA-256 output is 32 bytes
_SEED_BYTES = 16  # 128-bit seed
_COUNTER_BYTES = 16  # 128-bit block index


def rng_bytes(seed: int, cursor: int, n: int) -> bytes:
    """Return `n` bytes of the deterministic stream at `cursor`.

    Pure function: caller advances the cursor. Spec pseudocode in
    determinism.md § The RNG is the source of truth for this construction;
    any deviation is a contract break.
    """
    if seed < 0 or seed >= 1 << (_SEED_BYTES * 8):
        raise ValueError("seed must be a 128-bit unsigned integer")
    if cursor < 0:
        raise ValueError("cursor must be non-negative")
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return b""

    block_index = cursor // _BLOCK_SIZE
    byte_offset_in_block = cursor % _BLOCK_SIZE
    out = bytearray()
    seed_prefix = seed.to_bytes(_SEED_BYTES, "big", signed=False)
    while len(out) < n + byte_offset_in_block:
        block_input = seed_prefix + block_index.to_bytes(_COUNTER_BYTES, "big", signed=False)
        out.extend(hashlib.sha256(block_input).digest())
        block_index += 1
    return bytes(out[byte_offset_in_block : byte_offset_in_block + n])


def uniform_int(seed: int, cursor: int, upper_inclusive: int) -> tuple[int, int]:
    """Sample uniformly from `[0, upper_inclusive]`. Returns `(value, cursor_after)`.

    Rejection sampling on the minimum number of whole bytes needed.
    Variable byte consumption per call is fine — the cursor records actual
    consumption, so resumption stays exact.
    """
    if upper_inclusive < 0:
        raise ValueError("upper_inclusive must be >= 0")
    n = upper_inclusive + 1
    if n <= 1:
        return 0, cursor
    bits = (n - 1).bit_length()
    bytes_needed = (bits + 7) // 8
    pow2 = 1 << (bytes_needed * 8)
    threshold = pow2 - (pow2 % n)
    while True:
        chunk = rng_bytes(seed, cursor, bytes_needed)
        cursor += bytes_needed
        value = int.from_bytes(chunk, "big")
        if value < threshold:
            return value % n, cursor


def shuffled_wall(seed: int) -> tuple[list[Tile], int]:
    """Return `(wall, cursor_after)` for an initial deal.

    Fisher-Yates on the canonical 144-tile set; uses `uniform_int` for each
    swap. `cursor_after` is the byte offset to store in
    `GameState.rng.cursor` after the deal.
    """
    tiles = canonical_tile_set()
    cursor = 0
    for i in range(len(tiles) - 1, 0, -1):
        j, cursor = uniform_int(seed, cursor, upper_inclusive=i)
        tiles[i], tiles[j] = tiles[j], tiles[i]
    return tiles, cursor
