"""WebSocket wire protocol — codec, errors, message types.

Spec: docs/specs/wire-protocol.md.

The codec is a pure JSON encode/decode layer with a validated `kind`
enumeration. Privacy projection of EVENT payloads is the caller's
responsibility (see `mahjong.engine.state.project_event`); the codec is the
last serialisation step, not the projection step.
"""

from mahjong.wire import codec, errors

__all__ = ["codec", "errors"]
