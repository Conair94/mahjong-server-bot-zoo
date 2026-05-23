"""Session multiplexer: per-table binding of WebSocket sinks to seat slots.

Spec: docs/specs/session-mux.md.

Public surface:

- `TableSessions` — per-table coordinator (seats + spectator set).
- `SeatSession` — per-seat state machine (UNBOUND/LIVE/HELD), ring buffer,
  pending-prompt slot.
- `Spectator` — stateless-in-session-mux subscription record.
- `OutboundSink` — Protocol every connection wrapper must satisfy.
- `SeatPrompt` — input shape for `SeatSession.decide`.
- Exceptions: `SeatHoldExpired`, `SeatAttachError`.
"""

from __future__ import annotations

from mahjong.sessions.mux import (
    DEFAULT_BUFFER_CAPACITY,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_MAX_SPECTATORS,
    AttachOutcome,
    OutboundSink,
    SeatAttachError,
    SeatHoldExpired,
    SeatPrompt,
    SeatSession,
    SeatState,
    SpectateOutcome,
    Spectator,
    TableSessions,
)
from mahjong.sessions.timers import IdempotentTimer

__all__ = [
    "DEFAULT_BUFFER_CAPACITY",
    "DEFAULT_HOLD_SECONDS",
    "DEFAULT_MAX_SPECTATORS",
    "AttachOutcome",
    "IdempotentTimer",
    "OutboundSink",
    "SeatAttachError",
    "SeatHoldExpired",
    "SeatPrompt",
    "SeatSession",
    "SeatState",
    "SpectateOutcome",
    "Spectator",
    "TableSessions",
]
