"""Ring-buffer fixtures (2, 3): resume → fresh current snapshot (FB-17);
overflow → fresh snapshot.

Spec: docs/specs/session-mux.md § Ring buffer. Step 7.3 of CHECKLIST.md;
fixture 2 revised 2026-06-12 (FB-17 — resume no longer replays EVENTs).
"""

from __future__ import annotations

import pytest

from tests.sessions.conftest import FakeSink, make_seat_session

pytestmark = pytest.mark.asyncio


# ----- Fixture 2 (FB-17 revision): resume sends a fresh *current* snapshot,
# never an EVENT replay.
#
# The snapshot provider queries live GameState (spec § Ring buffer, fixture 3
# pins `project(state, seat)` as "current"), so the snapshot a resume carries
# already includes every event that happened while HELD. Replaying those
# buffered EVENTs on top would double-apply them in the client reducer — the
# FB-17 phantom-tile desync. Resume therefore behaves exactly like the
# overflow path: fresh snapshot, `resume_buffer_size = 0`, no EVENT frames.
# (HAND_END is still re-delivered — see test_hand_end_routing — because the
# summary frame is idempotent and carries settlement data.)


async def test_resume_sends_fresh_current_snapshot_and_no_event_replay() -> None:
    versions = {"n": 0}

    def live_provider(seat: int | None) -> dict:
        versions["n"] += 1
        return {"snapshot_version": versions["n"], "seat": seat}

    seat = make_seat_session(hold_seconds=60.0, snapshot_provider=live_provider)
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")
    assert sink_a.by_kind("ATTACHED")[0]["snapshot"]["snapshot_version"] == 1

    # 5 LIVE events sink to A.
    for i in range(5):
        await seat.observe({"event": "DISCARD", "seat": 1, "tile": f"W{i + 1}"})
    assert len(sink_a.by_kind("EVENT")) == 5

    # Drop, then 5 events arrive while HELD.
    await seat.on_socket_dropped(sink_a)
    for i in range(5):
        await seat.observe({"event": "DISCARD", "seat": 2, "tile": f"B{i + 1}"})

    # Reconnect: the snapshot must be re-queried *at resume time* (version 2),
    # with no EVENT replay and resume_buffer_size 0.
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok
    attached = sink_b.by_kind("ATTACHED")[0]
    assert attached["snapshot"]["snapshot_version"] == 2
    assert attached["resume_buffer_size"] == 0
    assert sink_b.by_kind("EVENT") == []

    # Subsequent live event goes through normally.
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
