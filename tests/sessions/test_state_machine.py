"""Session-mux state-machine fixtures (1, 8, 9, 10, 14, 15).

Spec: docs/specs/session-mux.md § Seat state machine, § Conflict resolution,
§ Error handling. Step 7.3 of CHECKLIST.md.
"""

from __future__ import annotations

import pytest

from mahjong.sessions import SeatState
from tests.sessions.conftest import (
    FakeSink,
    make_prompt,
    make_seat_session,
)

pytestmark = pytest.mark.asyncio


# ----- Fixture 1: every transition fires exactly once per trigger -----


async def test_unbound_to_live_on_attach() -> None:
    seat = make_seat_session()
    assert seat.state is SeatState.UNBOUND
    sink = FakeSink()
    outcome = await seat.attach(sink, user_id="alice")
    assert outcome.ok
    assert seat.state is SeatState.LIVE
    assert seat.user_id == "alice"
    assert sink.kinds() == ["ATTACHED"]
    assert sink.messages[0]["resume_buffer_size"] == 0


async def test_live_to_held_on_socket_drop() -> None:
    seat = make_seat_session(hold_seconds=60.0)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    await seat.on_socket_dropped(sink)
    assert seat.state is SeatState.HELD
    assert seat.sink is None
    # User identity persists into HELD so resume can validate.
    assert seat.user_id == "alice"


async def test_live_to_unbound_on_graceful_detach() -> None:
    seat = make_seat_session()
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    await seat.graceful_detach()
    assert seat.state is SeatState.UNBOUND
    assert sink.kinds()[-1] == "DETACHED"


async def test_held_to_live_on_resume_with_same_user() -> None:
    seat = make_seat_session(hold_seconds=60.0)
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")
    await seat.on_socket_dropped(sink_a)
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok
    assert seat.state is SeatState.LIVE
    assert sink_b.kinds() == ["ATTACHED"]


async def test_held_to_unbound_on_hold_timer_fire() -> None:
    seat = make_seat_session(hold_seconds=0.05)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    await seat.on_socket_dropped(sink)
    # Wait long enough for the timer to fire.
    import asyncio

    await asyncio.sleep(0.15)
    assert seat.state is SeatState.UNBOUND


# ----- Fixture 8: same-user takeover -----


async def test_same_user_takeover_closes_old_and_swaps_to_new() -> None:
    seat = make_seat_session()
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok
    # A gets a DETACH(replaced_by_new_session) then is closed.
    assert "DETACH" in sink_a.kinds()
    detach = next(m for m in sink_a.messages if m["kind"] == "DETACH")
    assert detach["reason"] == "replaced_by_new_session"
    assert sink_a.closed
    # B is now LIVE with an ATTACHED.
    assert sink_b.kinds() == ["ATTACHED"]
    assert seat.sink is sink_b


# ----- Fixture 9: different-user rejection -----


async def test_different_user_rejected_with_seat_not_yours_when_held() -> None:
    seat = make_seat_session(hold_seconds=60.0)
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")
    await seat.on_socket_dropped(sink_a)
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="bob")
    assert not outcome.ok
    assert outcome.error_code == "seat_not_yours"
    assert sink_b.kinds() == ["ERROR"]
    assert sink_b.messages[0]["code"] == "seat_not_yours"
    # A's state preserved.
    assert seat.state is SeatState.HELD
    assert seat.user_id == "alice"


async def test_different_user_rejected_with_seat_occupied_when_live() -> None:
    seat = make_seat_session()
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="bob")
    assert outcome.error_code == "seat_occupied"
    assert sink_b.messages[0]["code"] == "seat_occupied"
    assert seat.user_id == "alice"
    assert seat.sink is sink_a


# ----- Fixture 10: hand end while HELD -----


async def test_hand_end_while_held_unbinds_seat() -> None:
    seat = make_seat_session(hold_seconds=60.0)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    await seat.on_socket_dropped(sink)
    assert seat.state is SeatState.HELD
    await seat.hand_ended(terminal={"kind": "DRAW"}, next_hand_seq=None)
    assert seat.state is SeatState.UNBOUND
    # Subsequent same-user attach attempt finds it unbound — that's not the
    # `seat_not_yours` case anymore; it's a fresh attach.
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok


# ----- Fixture 14: illegal action increments strike, doesn't transition -----


async def test_illegal_action_strikes_without_state_change() -> None:
    strikes: list[tuple[int, str]] = []
    seat = make_seat_session(on_strike=lambda s, code: strikes.append((s, code)))
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")

    import asyncio

    prompt = make_prompt(prompt_id="p1", deadline_offset=30.0)
    decide_task = asyncio.create_task(seat.decide(prompt))
    await asyncio.sleep(0)  # let decide() send the PROMPT
    assert any(m["kind"] == "PROMPT" for m in sink.messages)

    illegal = {"type": "PLAY", "tile": "T9"}  # not in legal_actions
    await seat.handle_action(prompt_id="p1", action=illegal)

    assert ("ERROR", "illegal_action") in [(m["kind"], m.get("code")) for m in sink.messages]
    assert strikes == [(0, "illegal_action")]
    assert seat.state is SeatState.LIVE
    assert seat.has_pending_prompt  # still outstanding; client can retry

    # Client retries with a legal action; prompt resolves.
    legal = {"type": "PLAY", "tile": "W3"}
    await seat.handle_action(prompt_id="p1", action=legal)
    result = await decide_task
    assert result == legal


# ----- Fixture 15: idempotent re-entry; only one hold timer at a time -----


async def test_repeated_drop_reconnect_does_not_leak_timers_or_buffer() -> None:
    seat = make_seat_session(hold_seconds=0.5)
    current_sink = FakeSink()
    await seat.attach(current_sink, user_id="alice")

    # Drop / reconnect three times. Each cycle pushes 1 event during HELD,
    # which should appear in the buffer until replay.
    for i in range(3):
        await seat.on_socket_dropped(current_sink)
        await seat.observe({"event": "DISCARD", "seat": 1, "tile": f"W{i + 1}"})
        current_sink = FakeSink()
        outcome = await seat.attach(current_sink, user_id="alice")
        assert outcome.ok
        # Buffer was drained on resume.
        assert seat.buffer_size == 0

    # No timer should still be armed (we're LIVE).
    assert not seat._hold_timer.armed  # type: ignore[attr-defined]
