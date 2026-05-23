"""Graceful-shutdown fixture (11): drain LIVE and HELD seats on SIGTERM.

Spec: docs/specs/session-mux.md § Server lifecycle interaction. Step 7.3 of
CHECKLIST.md.
"""

from __future__ import annotations

import asyncio

import pytest

from mahjong.sessions import SeatState
from tests.sessions.conftest import FakeSink, make_prompt, make_table_sessions

pytestmark = pytest.mark.asyncio


async def test_shutdown_drains_live_held_and_spectators() -> None:
    sessions = make_table_sessions(hold_seconds=60.0)
    # 2 LIVE seats (0, 1), 1 HELD seat (2), 1 spectator.
    live_sinks = [FakeSink(), FakeSink()]
    await sessions.attach(live_sinks[0], user_id="alice", seat=0)
    await sessions.attach(live_sinks[1], user_id="bob", seat=1)

    held_sink = FakeSink()
    await sessions.attach(held_sink, user_id="carol", seat=2)
    await sessions.seat(2).on_socket_dropped(held_sink)
    assert sessions.seat(2).state is SeatState.HELD

    spec_sink = FakeSink()
    await sessions.spectate(spec_sink, user_id="dave")

    # HELD seat 2 has an outstanding prompt (no default fired yet).
    prompt = make_prompt(prompt_id="p_2", deadline_offset=10.0)
    decide_task = asyncio.create_task(sessions.seat(2).decide(prompt))
    await asyncio.sleep(0)

    # Drain.
    await sessions.shutdown(reason="server_shutdown")

    # LIVE seats received DETACH(reason=server_shutdown) and were closed.
    for sink in live_sinks:
        kinds = sink.kinds()
        assert "DETACH" in kinds
        detach = next(m for m in sink.messages if m["kind"] == "DETACH")
        assert detach["reason"] == "server_shutdown"
        assert sink.closed

    # HELD seat's outstanding decide resolved (default, per impl).
    result = await decide_task
    assert result == prompt.default_action

    # Spectator received DETACH and was closed.
    assert spec_sink.closed
    spec_detach = [m for m in spec_sink.messages if m["kind"] == "DETACH"]
    assert spec_detach and spec_detach[0]["reason"] == "server_shutdown"

    # All seats UNBOUND; no spectators remain.
    for i in range(4):
        assert sessions.seat(i).state is SeatState.UNBOUND
    assert sessions.spectator_count == 0


async def test_attach_during_shutdown_rejected_with_shutting_down() -> None:
    flag = {"down": False}
    sessions = make_table_sessions(shutting_down=lambda: flag["down"])
    flag["down"] = True

    sink = FakeSink()
    outcome = await sessions.attach(sink, user_id="alice", seat=0)
    assert outcome.error_code == "shutting_down"
    err = [m for m in sink.messages if m["kind"] == "ERROR"]
    assert err and err[0]["code"] == "shutting_down"


async def test_spectate_during_shutdown_rejected_with_shutting_down() -> None:
    flag = {"down": False}
    sessions = make_table_sessions(shutting_down=lambda: flag["down"])
    flag["down"] = True

    sink = FakeSink()
    outcome = await sessions.spectate(sink, user_id="alice")
    assert outcome.error_code == "shutting_down"
