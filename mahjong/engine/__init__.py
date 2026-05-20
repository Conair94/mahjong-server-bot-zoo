"""Rules engine: pure functions over a GameState value object.

Public surface (spec: docs/specs/engine-api.md):
    initial_state, legal_actions, apply_action,
    project, is_terminal, state_hash,
    EngineError, IllegalAction, InvalidState, RulesetError.

Pure-function discipline: no I/O, no globals, no clocks, no RNG except the
canonical DRBG. Enforced by lint (see tests/lint/).
"""

from mahjong.engine.errors import EngineError, IllegalAction, InvalidState, RulesetError
from mahjong.engine.legality import legal_actions
from mahjong.engine.state import initial_state, is_terminal, project, state_hash
from mahjong.engine.transition import apply_action

__all__ = [
    "EngineError",
    "IllegalAction",
    "InvalidState",
    "RulesetError",
    "apply_action",
    "initial_state",
    "is_terminal",
    "legal_actions",
    "project",
    "state_hash",
]
