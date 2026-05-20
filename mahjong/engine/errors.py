"""Engine exception taxonomy.

Spec: docs/specs/engine-api.md § Public API > Exception types.

Three exception classes cover every engine failure path:
    EngineError       — base, never raised directly
    IllegalAction     — caller submitted an action not in legal_actions
    InvalidState      — malformed input state (caller bug or engine bug)
    RulesetError      — ruleset reference couldn't be resolved
"""
