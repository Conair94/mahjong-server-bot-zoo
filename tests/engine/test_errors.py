"""Step 1.1 — engine exception payload completeness.

Spec: docs/specs/engine-api.md § Public API > Exception types,
      engine-api.md fixture 5 (exception payload completeness).
"""

from __future__ import annotations

import pytest

from mahjong.engine.errors import (
    EngineError,
    IllegalAction,
    InvalidState,
    RulesetError,
)


def test_illegal_action_carries_required_fields() -> None:
    """fixture 5: IllegalAction carries state_hash, seat, attempted_action, legal_actions."""
    legal = [{"type": "PASS"}, {"type": "PLAY", "tile": "B5"}]
    attempted = {"type": "CHI", "tiles": ["B4", "B5", "B6"]}
    err = IllegalAction(
        state_hash="sha256:abc",
        seat=2,
        attempted_action=attempted,
        legal_actions=legal,
    )
    assert err.state_hash == "sha256:abc"
    assert err.seat == 2
    assert err.attempted_action == attempted
    assert err.legal_actions == legal
    # Useful repr — readable from a traceback alone.
    assert "seat=2" in repr(err)


def test_illegal_action_is_engine_error() -> None:
    err = IllegalAction(
        state_hash="sha256:abc",
        seat=0,
        attempted_action={"type": "PASS"},
        legal_actions=[],
    )
    assert isinstance(err, EngineError)
    assert isinstance(err, Exception)


def test_invalid_state_carries_required_fields() -> None:
    """fixture 5: InvalidState carries state_hash, invariant_name, detail."""
    err = InvalidState(
        state_hash="sha256:def",
        invariant_name="concealed_sorted",
        detail="seat 1 concealed list out of canonical order",
    )
    assert err.state_hash == "sha256:def"
    assert err.invariant_name == "concealed_sorted"
    assert err.detail == "seat 1 concealed list out of canonical order"
    assert isinstance(err, EngineError)


def test_ruleset_error_carries_required_fields() -> None:
    err = RulesetError(
        ruleset_ref={"id": "mcr-2099", "version": 1},
        detail="unknown ruleset id",
    )
    assert err.ruleset_ref == {"id": "mcr-2099", "version": 1}
    assert err.detail == "unknown ruleset id"
    assert isinstance(err, EngineError)


def test_engine_error_is_a_real_exception() -> None:
    """Base class is catchable; sanity check inheritance."""
    with pytest.raises(EngineError):
        raise IllegalAction(
            state_hash="sha256:0",
            seat=0,
            attempted_action={"type": "PASS"},
            legal_actions=[],
        )
    with pytest.raises(EngineError):
        raise InvalidState(state_hash="sha256:0", invariant_name="x", detail="y")
    with pytest.raises(EngineError):
        raise RulesetError(ruleset_ref={"id": "x"}, detail="y")


def test_illegal_action_requires_all_payload_fields() -> None:
    """Constructor should be keyword-only — no positional ambiguity."""
    with pytest.raises(TypeError):
        IllegalAction("sha256:abc", 0, {"type": "PASS"}, [])  # type: ignore[misc]
