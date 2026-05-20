"""Canonical deterministic RNG: rng_bytes, uniform_int, shuffled_wall.

Spec: docs/specs/determinism.md § The RNG.

This is the only randomness source the engine ever uses. `random`,
`numpy.random`, and any other RNG library are forbidden under mahjong.engine.*
(enforced by lint).
"""
