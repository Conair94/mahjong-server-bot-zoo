"""Step 1.1 — engine type shapes and invariants.

Spec: docs/specs/state-schema.md § Top-level state object,
      state-schema.md fixture 2 (canonical-form invariance).

These tests pin the contract that the canonical hash is stable across
equivalent constructions of the same state — the property that lets
records be byte-deterministic across machines.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.engine.errors import InvalidState
from mahjong.engine.hashing import canonical_hash
from mahjong.engine.types import validate_state_invariants


def _minimal_state() -> dict[str, Any]:
    """A small but structurally complete GameState used across tests.

    Not a *legal* mahjong state (no 13-tile hand, no real config_hash) —
    just shaped enough that the validator runs end-to-end.
    """
    return {
        "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "sha256:abc"},
        "round_wind": "F1",
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 0,
        "wall": {"remaining": [], "drawn_count": 0, "total": 144},
        "seats": [
            {
                "seat": i,
                "seat_wind": f"F{i + 1}",
                "concealed": [],
                "melds": [],
                "discards": [],
                "flowers": [],
                "score": 0,
            }
            for i in range(4)
        ],
        "last_discard": None,
        "pending_claims": [],
        "phase": "DEAL",
        "current_actor": 0,
        "terminal": None,
        "rng": {"seed": "12345", "cursor": 0},
    }


# --- Hash stability under permuted construction ---


def test_state_hash_is_stable_for_equal_states() -> None:
    """Same state, constructed twice, same hash."""
    assert canonical_hash(_minimal_state()) == canonical_hash(_minimal_state())


def test_state_hash_invariant_under_dict_key_order() -> None:
    """state-schema.md fixture 2 — canonical-form invariance.

    Two states differing only in dict key insertion order must serialize
    to byte-identical JSON (json.dumps sort_keys=True does the work).
    """
    a = _minimal_state()
    # Construct b with reversed key order at the top level.
    b = {k: a[k] for k in reversed(list(a.keys()))}
    # Also reorder nested ruleset dict.
    b["ruleset"] = {
        "config_hash": a["ruleset"]["config_hash"],
        "version": a["ruleset"]["version"],
        "id": a["ruleset"]["id"],
    }
    assert canonical_hash(a) == canonical_hash(b)


def test_state_hash_distinguishes_meaningful_differences() -> None:
    """A change in any observable field changes the hash."""
    a = _minimal_state()
    b = _minimal_state()
    b["turn_index"] = 1
    assert canonical_hash(a) != canonical_hash(b)

    c = _minimal_state()
    c["seats"][2]["score"] = 8
    assert canonical_hash(a) != canonical_hash(c)


# --- Concealed-sorted invariant ---


def test_validator_accepts_canonically_sorted_concealed() -> None:
    state = _minimal_state()
    state["seats"][0]["concealed"] = ["W1", "W1", "B3", "T7", "F1", "J2", "H1"]
    # Should not raise.
    validate_state_invariants(state)


def test_validator_rejects_unsorted_concealed() -> None:
    """state-schema.md: concealed is part of the canonical form; the engine
    sorts before construction. The validator enforces the invariant."""
    state = _minimal_state()
    state["seats"][1]["concealed"] = ["B3", "W1"]  # out of section order
    with pytest.raises(InvalidState) as exc:
        validate_state_invariants(state)
    assert exc.value.invariant_name == "concealed_sorted"
    assert "seat" in exc.value.detail.lower()


def test_validator_rejects_unsorted_within_section() -> None:
    state = _minimal_state()
    state["seats"][0]["concealed"] = ["W3", "W1"]
    with pytest.raises(InvalidState):
        validate_state_invariants(state)


def test_validator_rejects_invalid_tile_token_in_concealed() -> None:
    state = _minimal_state()
    state["seats"][0]["concealed"] = ["W1", "X9"]
    with pytest.raises(InvalidState):
        validate_state_invariants(state)


# --- Action grammar shape (light — heavier checks land with apply_action) ---


def test_action_typeddicts_accept_canonical_shapes() -> None:
    """Importable; mypy enforces the field shapes statically.

    Runtime check: every documented action constructs as a plain dict.
    """
    from mahjong.engine.types import Action  # noqa: F401 — import-only smoke

    actions: list[dict[str, Any]] = [
        {"type": "PASS"},
        {"type": "PLAY", "tile": "B5"},
        {"type": "PENG", "tile": "B5"},
        {"type": "CHI", "tiles": ["B4", "B5", "B6"]},
        {"type": "GANG", "tile": "B5", "kind": "EXPOSED"},
        {"type": "GANG", "tile": "B5", "kind": "CONCEALED"},
        {"type": "GANG", "tile": "B5", "kind": "ADDED"},
        {"type": "HU"},
    ]
    # Every action is hashable through canonical_hash (records will hash them).
    for a in actions:
        canonical_hash(a)
