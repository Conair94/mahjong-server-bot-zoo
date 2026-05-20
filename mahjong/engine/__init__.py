"""Rules engine: pure functions over a GameState value object.

Public surface (spec: docs/specs/engine-api.md):
    initial_state, legal_actions, apply_action,
    project, is_terminal, state_hash,
    EngineError, IllegalAction, InvalidState, RulesetError.

Pure-function discipline: no I/O, no globals, no clocks, no RNG except the
canonical DRBG. Enforced by lint (see tests/lint/).
"""
