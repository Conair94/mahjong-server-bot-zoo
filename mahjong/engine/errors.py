"""Engine exception taxonomy.

Spec: docs/specs/engine-api.md § Public API > Exception types,
      engine-api.md fixture 5 (payload completeness).

Four exception classes cover every engine failure path:
    EngineError       - base, never raised directly
    IllegalAction     - caller submitted an action not in legal_actions
    InvalidState      - malformed input state (caller bug or engine bug)
    RulesetError      - ruleset reference couldn't be resolved

Each carries enough context to debug from a traceback alone - that's the
contract engine-api.md fixture 5 enforces.
"""

from __future__ import annotations

from typing import Any


class EngineError(Exception):
    """Base class. Never raised directly; catch this in callers that want
    to be defensive about any engine failure."""


class IllegalAction(EngineError):
    """Caller submitted an action not in `legal_actions(state, seat)`.

    Payload (all required, keyword-only):
        state_hash: canonical hash of the state at the time of the call.
        seat: the seat that submitted the action (0..3).
        attempted_action: the action dict the caller passed in.
        legal_actions: the list that *was* legal at that moment.
    """

    def __init__(
        self,
        *,
        state_hash: str,
        seat: int,
        attempted_action: dict[str, Any],
        legal_actions: list[dict[str, Any]],
    ) -> None:
        super().__init__(
            f"IllegalAction(seat={seat}, attempted={attempted_action!r}, "
            f"legal_count={len(legal_actions)}, state_hash={state_hash})"
        )
        self.state_hash = state_hash
        self.seat = seat
        self.attempted_action = attempted_action
        self.legal_actions = legal_actions


class InvalidState(EngineError):
    """The input state failed an invariant check.

    Should never happen for states the engine itself produced; raised when
    a caller hands back a corrupted state, or when an engine refactor
    silently violated an invariant.

    Payload:
        state_hash: canonical hash of the offending state.
        invariant_name: short identifier (e.g. "concealed_sorted").
        detail: human-readable description of the violation.
    """

    def __init__(self, *, state_hash: str, invariant_name: str, detail: str) -> None:
        super().__init__(f"InvalidState({invariant_name}: {detail}; state_hash={state_hash})")
        self.state_hash = state_hash
        self.invariant_name = invariant_name
        self.detail = detail


class RulesetError(EngineError):
    """Ruleset reference couldn't be resolved.

    Causes include: unknown id, `config_hash` mismatch between caller and
    loader, malformed config file.

    Payload:
        ruleset_ref: the RuleSetRef the caller passed in.
        detail: human-readable explanation.
    """

    def __init__(self, *, ruleset_ref: dict[str, Any], detail: str) -> None:
        super().__init__(f"RulesetError({ruleset_ref!r}: {detail})")
        self.ruleset_ref = ruleset_ref
        self.detail = detail
