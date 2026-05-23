"""Ring-buffer fixtures (2, 3): replay in order; overflow → fresh snapshot.

Spec: docs/specs/session-mux.md § Ring buffer. Step 7.3 of CHECKLIST.md.
"""

from __future__ import annotations

import pytest

from tests.sessions.conftest import FakeSink, make_seat_session

pytestmark = pytest.mark.asyncio


# ----- Fixture 2: buffered events replay in order on reconnect -----


async def test_buffered_events_replay_in_order_then_continue() -> None:
    seat = make_seat_session(hold_seconds=60.0)
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")

    # 5 LIVE events sink to A.
    for i in range(5):
        await seat.observe({"event": "DISCARD", "seat": 1, "tile": f"W{i + 1}"})
    assert len(sink_a.by_kind("EVENT")) == 5

    # Drop.
    await seat.on_socket_dropped(sink_a)

    # 5 buffered events while HELD.
    for i in range(5):
        await seat.observe({"event": "DISCARD", "seat": 2, "tile": f"B{i + 1}"})
    assert seat.buffer_size == 5

    # Reconnect.
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok

    # First message: ATTACHED with resume_buffer_size = 5.
    attached = sink_b.by_kind("ATTACHED")[0]
    assert attached["resume_buffer_size"] == 5

    # Next 5: the buffered events in order.
    events = sink_b.by_kind("EVENT")
    assert len(events) == 5
    tiles = [e["event"]["tile"] for e in events]
    assert tiles == [f"B{i + 1}" for i in range(5)]

    # Subsequent live event goes through (no further replay).
    await seat.observe({"event": "DISCARD", "seat": 3, "tile": "T9"})
    assert sink_b.by_kind("EVENT")[-1]["event"]["tile"] == "T9"


# ----- Fixture 3: ring-buffer overflow forces a fresh snapshot -----


async def test_buffer_overflow_yields_fresh_snapshot_and_zero_replay() -> None:
    seat = make_seat_session(hold_seconds=60.0, buffer_capacity=4)
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")
    await seat.on_socket_dropped(sink_a)

    # 10 events while HELD; capacity 4 ⇒ overflow.
    for i in range(10):
        await seat.observe({"event": "DISCARD", "seat": 1, "tile": f"W{i + 1}"})
    assert seat.buffer_overflowed
    # The deque is bounded to capacity, but the flag is what matters for the
    # resume path.

    sink_b = FakeSink()
    await seat.attach(sink_b, user_id="alice")

    attached = sink_b.by_kind("ATTACHED")[0]
    assert attached["resume_buffer_size"] == 0
    # Snapshot present (our test snapshot provider returns the seat marker).
    assert attached["snapshot"] == {"public": False, "seat": 0, "concealed_len": 13}
    # No replayed events.
    assert sink_b.by_kind("EVENT") == []
