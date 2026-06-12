"""Shared fixtures for session-mux tests.

Step 7.3 of CHECKLIST.md. The 21 verification fixtures from
docs/specs/session-mux.md are spread across this directory; this file holds
the bits they share: a `FakeSink` that records sent messages without real I/O,
plus `make_table_sessions` / `make_prompt` factories.

The file-level `pytestmark = pytest.mark.asyncio` convention is documented in
[feedback memory: pytest-asyncio mode quirk].
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from mahjong.sessions import (
    SeatPrompt,
    SeatSession,
    TableSessions,
)


class FakeSink:
    """A test double for `OutboundSink`. Captures every `send()` payload in
    `messages` so tests can assert on outbound traffic without involving
    websockets. `close()` marks the sink `closed`. Send-after-close raises
    `ConnectionError` to mimic a dead WebSocket — the session-mux is meant
    to tolerate this transparently."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.fail_send: bool = False  # set True to simulate a dead socket

    async def send(self, msg: Mapping[str, Any]) -> None:
        if self._closed or self.fail_send:
            raise ConnectionError("sink closed")
        self.messages.append(dict(msg))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self._closed = True
        self.close_code = code
        self.close_reason = reason

    @property
    def closed(self) -> bool:
        return self._closed

    # --- test helpers ---

    def kinds(self) -> list[str]:
        return [m["kind"] for m in self.messages]

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["kind"] == kind]


def make_snapshot(seat: int | None) -> dict[str, Any]:
    """Tiny deterministic snapshot the test mux returns. Real production
    code injects a callable that queries the real GameState; here we just
    need a marker the assertions can recognize."""
    if seat is None:
        return {"public": True, "concealed_counts": [13, 13, 13, 13]}
    return {"public": False, "seat": seat, "concealed_len": 13}


def make_table_sessions(
    *,
    table_id: int = 17,
    hand_index: int = 0,
    buffer_capacity: int = 256,
    hold_seconds: float = 60.0,
    max_spectators: int = 32,
    snapshot_provider=make_snapshot,
    on_strike=None,
    shutting_down=None,
) -> TableSessions:
    return TableSessions(
        table_id=table_id,
        snapshot_provider=snapshot_provider,
        hand_index_provider=lambda: hand_index,
        max_spectators=max_spectators,
        buffer_capacity=buffer_capacity,
        hold_seconds=hold_seconds,
        on_strike=on_strike,
        shutting_down=shutting_down,
    )


def make_seat_session(
    *,
    table_id: int = 17,
    seat: int = 0,
    hand_index: int = 0,
    buffer_capacity: int = 256,
    hold_seconds: float = 60.0,
    on_strike=None,
    snapshot_provider=make_snapshot,
) -> SeatSession:
    return SeatSession(
        table_id=table_id,
        seat=seat,
        snapshot_provider=snapshot_provider,
        hand_index_provider=lambda: hand_index,
        buffer_capacity=buffer_capacity,
        hold_seconds=hold_seconds,
        on_strike=on_strike,
    )


def make_prompt(
    *,
    prompt_id: str = "p_test_1",
    phase: str = "DISCARD",
    legal_actions: list[dict[str, Any]] | None = None,
    default_action: dict[str, Any] | None = None,
    deadline_offset: float = 30.0,
) -> SeatPrompt:
    """Build a SeatPrompt with deadline `deadline_offset` seconds in the future.

    Uses `asyncio.get_event_loop().time()` for monotonic deadline; the wire
    `deadline_ms` is a wall-clock placeholder (the mux doesn't enforce it).
    """
    if legal_actions is None:
        legal_actions = [
            {"type": "PLAY", "tile": "W3"},
            {"type": "PLAY", "tile": "B5"},
        ]
    if default_action is None:
        default_action = {"type": "PLAY", "tile": "W3"}
    loop = asyncio.get_event_loop()
    return SeatPrompt(
        prompt_id=prompt_id,
        phase=phase,
        legal_actions=legal_actions,
        default_action=default_action,
        deadline=loop.time() + deadline_offset,
        deadline_ms=0,
    )


# Discovery hook for pytest-asyncio: this file is shared, so individual test
# modules apply the file-level mark themselves (see feedback_pytest_asyncio_
# mode_quirk.md).
__all__ = ["FakeSink", "make_prompt", "make_seat_session", "make_snapshot", "make_table_sessions"]


# Silence unused-import warning while keeping the module importable.
_pytest = pytest
