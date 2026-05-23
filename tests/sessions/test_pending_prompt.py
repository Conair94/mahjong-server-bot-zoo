"""Pending-prompt fixtures (4, 5, 6, 7, 12, 13).

Spec: docs/specs/session-mux.md § Pending prompt across reconnect, § Error
handling. Step 7.3 of CHECKLIST.md.
"""

from __future__ import annotations

import asyncio

import pytest

from mahjong.sessions import SeatHoldExpired
from tests.sessions.conftest import FakeSink, make_prompt, make_seat_session

pytestmark = pytest.mark.asyncio


# ----- Fixture 4: pending prompt survives reconnect -----


async def test_pending_prompt_replays_after_reconnect() -> None:
    seat = make_seat_session(hold_seconds=60.0)
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")

    prompt = make_prompt(prompt_id="p_42", deadline_offset=10.0)
    decide_task = asyncio.create_task(seat.decide(prompt))
    await asyncio.sleep(0)  # let decide() send the PROMPT
    assert any(m["kind"] == "PROMPT" and m["prompt_id"] == "p_42" for m in sink_a.messages)

    # Drop A.
    await seat.on_socket_dropped(sink_a)
    await asyncio.sleep(0)
    assert not decide_task.done()  # future is parked

    # Reconnect B within window.
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok

    # After the ATTACHED frame, B sees the SAME prompt_id replayed.
    prompt_msgs = sink_b.by_kind("PROMPT")
    assert len(prompt_msgs) == 1
    assert prompt_msgs[0]["prompt_id"] == "p_42"

    # Client answers; decide() resolves.
    legal = {"type": "PLAY", "tile": "B5"}
    await seat.handle_action(prompt_id="p_42", action=legal)
    result = await decide_task
    assert result == legal


# ----- Fixture 5: prompt deadline fires while HELD → default action -----


async def test_prompt_deadline_fires_while_held_resolves_to_default() -> None:
    seat = make_seat_session(hold_seconds=60.0)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")

    prompt = make_prompt(prompt_id="p_5", deadline_offset=0.1)
    decide_task = asyncio.create_task(seat.decide(prompt))
    await asyncio.sleep(0)  # PROMPT sent

    # Drop, then wait past the prompt deadline.
    await seat.on_socket_dropped(sink)
    await asyncio.sleep(0.25)

    assert decide_task.done()
    result = await decide_task
    assert result == prompt.default_action
    # Seat-hold timer is still armed; seat still HELD.
    from mahjong.sessions import SeatState

    assert seat.state is SeatState.HELD


# ----- Fixture 6: seat-hold expiry without pending prompt -----


async def test_seat_hold_expiry_with_no_pending_drops_seat_quietly() -> None:
    seat = make_seat_session(hold_seconds=0.05)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    await seat.on_socket_dropped(sink)

    await asyncio.sleep(0.15)

    from mahjong.sessions import SeatState

    assert seat.state is SeatState.UNBOUND
    # Subsequent attach as the same user is a fresh attach, not a resume.
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok
    assert sink_b.by_kind("ATTACHED")[0]["resume_buffer_size"] == 0


# ----- Fixture 7: seat-hold expiry with pending prompt → SeatHoldExpired -----


async def test_seat_hold_expiry_preempts_long_prompt_with_seat_error() -> None:
    seat = make_seat_session(hold_seconds=0.05)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")

    prompt = make_prompt(prompt_id="p_7", deadline_offset=10.0)
    decide_task = asyncio.create_task(seat.decide(prompt))
    await asyncio.sleep(0)

    await seat.on_socket_dropped(sink)
    await asyncio.sleep(0.15)

    with pytest.raises(SeatHoldExpired):
        await decide_task


# ----- Fixture 12: ACTION with no outstanding prompt -----


async def test_inbound_action_without_prompt_emits_no_outstanding_prompt() -> None:
    seat = make_seat_session()
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")

    await seat.handle_action(prompt_id="bogus", action={"type": "PASS"})

    err = [m for m in sink.messages if m["kind"] == "ERROR"]
    assert err and err[0]["code"] == "no_outstanding_prompt"


# ----- Fixture 13: ACTION with stale prompt_id -----


async def test_inbound_action_with_stale_prompt_id_emits_stale_action() -> None:
    seat = make_seat_session()
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")

    prompt = make_prompt(prompt_id="p_current", deadline_offset=10.0)
    decide_task = asyncio.create_task(seat.decide(prompt))
    await asyncio.sleep(0)

    await seat.handle_action(prompt_id="p_other", action={"type": "PLAY", "tile": "W3"})
    err = [m for m in sink.messages if m["kind"] == "ERROR"]
    assert err and err[0]["code"] == "stale_action"
    # Original prompt still outstanding.
    assert seat.has_pending_prompt

    # Resolve cleanly.
    await seat.handle_action(prompt_id="p_current", action={"type": "PLAY", "tile": "W3"})
    result = await decide_task
    assert result == {"type": "PLAY", "tile": "W3"}
