"""HAND_END routing through observe()/send_event() (Step 7.6.i).

Spec: docs/specs/wire-protocol.md § HAND_END — HAND_END is its own top-level
wire frame with a `terminal` payload, NOT an EVENT-wrapped record event. The
record-format HAND_END event must therefore be intercepted at the mux/spectator
boundary and re-shaped, not blindly EVENT-wrapped.

Pre-7.6.i bug: `SeatSession.observe()` and `Spectator.send_event()` both wrap
*every* record event as `{"kind": "EVENT", "event": <projected>}`. When the
engine emits a HAND_END record event, the wire receiver gets an EVENT frame
whose `event.event == "HAND_END"` — wrong shape. Then if anyone subsequently
calls `SeatSession.hand_ended(...)` (HumanAdapter.left does this today), a
SECOND HAND_END frame goes out. Double-emit.

These tests pin the fixed behavior:
  - observe(HAND_END_record_event) → single top-level HAND_END frame.
  - HumanAdapter.left("HAND_ENDED") → no additional HAND_END frame.
  - Spectator path symmetric.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from mahjong.adapters.base import HumanIdentity, LeaveReason, SeatContext
from mahjong.adapters.human import HumanAdapter
from mahjong.engine.types import SeatView
from mahjong.sessions import SeatState
from tests.sessions.conftest import FakeSink, make_seat_session, make_table_sessions

pytestmark = pytest.mark.asyncio


def _hand_end_record(**overrides: Any) -> dict[str, Any]:
    """A representative HAND_END record event (record-format.md § HAND_END).

    Defaults: HU on a discard from seat 1, seat 2 wins for 14 fan."""
    base: dict[str, Any] = {
        "event": "HAND_END",
        "seq": 42,
        "turn_index": 18,
        "phase": "TERMINAL",
        "ts": "2026-05-19T22:36:02.118Z",
        "kind": "HU",
        "winner": [2],
        "win_tile": "T8",
        "win_type": "DISCARD",
        "deal_in_seat": 1,
        "fan": [{"name": "Mixed Shifted Chows", "value": 6}],
        "fan_total": 6,
        "score_delta": [-8, -22, 38, -8],
        "final_hands": [],
        "state_hash": "sha256:fed987",
    }
    base.update(overrides)
    return base


def _make_seat_context(seat: int = 0) -> SeatContext:
    return {
        "seat": seat,
        "hand_id": "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "x"},
        "seat_deadline_ms": 1000,
        "initial_view": cast(SeatView, {}),
    }


# ----- SeatSession.observe(HAND_END) -----


async def test_observe_hand_end_live_sends_top_level_hand_end_frame() -> None:
    """HAND_END record event → one top-level HAND_END wire frame, NOT an
    EVENT-wrapped frame."""
    seat = make_seat_session(seat=0)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    # Clear ATTACHED so we only inspect post-attach traffic.
    sink.messages.clear()

    await seat.observe(_hand_end_record())

    assert sink.kinds() == ["HAND_END"]
    msg = sink.by_kind("HAND_END")[0]
    assert msg["table_id"] == 17
    assert msg["hand_index"] == 0
    assert msg["next_hand_seq"] is None
    # Terminal payload is the record event minus wrapper fields.
    terminal = msg["terminal"]
    assert terminal["kind"] == "HU"
    assert terminal["winner"] == [2]
    assert terminal["win_tile"] == "T8"
    assert terminal["fan_total"] == 6
    # Wrapper fields are NOT in terminal.
    for stripped in ("event", "seq", "turn_index", "phase", "ts"):
        assert stripped not in terminal


async def test_observe_hand_end_does_not_emit_event_frame() -> None:
    """Defense-in-depth: no EVENT frame should be emitted for a HAND_END
    record event, even if it has fields that would otherwise project cleanly."""
    seat = make_seat_session(seat=0)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    sink.messages.clear()

    await seat.observe(_hand_end_record())

    assert sink.by_kind("EVENT") == []


async def test_observe_hand_end_while_held_buffers_and_replays_as_hand_end() -> None:
    """HAND_END arriving during HELD state must replay as a HAND_END frame
    on reconnect, not as an EVENT frame."""
    seat = make_seat_session(seat=0, hold_seconds=60.0)
    sink_a = FakeSink()
    await seat.attach(sink_a, user_id="alice")

    # Drop, then a HAND_END arrives while HELD.
    await seat.on_socket_dropped(sink_a)
    assert seat.state is SeatState.HELD
    await seat.observe(_hand_end_record())
    assert seat.buffer_size == 1

    # Reconnect within window — buffered HAND_END replays as HAND_END frame.
    sink_b = FakeSink()
    outcome = await seat.attach(sink_b, user_id="alice")
    assert outcome.ok
    kinds = sink_b.kinds()
    assert "HAND_END" in kinds
    assert "EVENT" not in kinds  # nothing else buffered
    he = sink_b.by_kind("HAND_END")[0]
    assert he["terminal"]["kind"] == "HU"


# ----- Spectator.send_event(HAND_END) -----


async def test_spectator_receives_hand_end_as_top_level_frame() -> None:
    """fanout_event with a HAND_END record → spectator gets a HAND_END frame,
    not an EVENT-wrapped one. Public projection of HAND_END is HAND_END
    itself (per state-schema), so spectators see the same terminal payload."""
    sessions = make_table_sessions()
    spec_sink = FakeSink()
    await sessions.spectate(spec_sink, user_id="watcher")
    spec_sink.messages.clear()

    await sessions.fanout_event(_hand_end_record())

    assert spec_sink.kinds() == ["HAND_END"]
    he = spec_sink.by_kind("HAND_END")[0]
    assert he["terminal"]["kind"] == "HU"
    assert he["next_hand_seq"] is None


# ----- HumanAdapter.left("HAND_ENDED") no longer double-sends -----


async def test_human_adapter_left_hand_ended_does_not_double_emit() -> None:
    """After observe() has already sent the HAND_END frame, left("HAND_ENDED")
    must NOT send a second HAND_END.

    Layer-8 contract: left("HAND_ENDED") is a no-op — the session stays LIVE
    so the multi-hand orchestrator can call begin_next_hand() to issue
    DETACH(hand_ended) + ATTACHED for the new hand.  For single-hand the
    session stays LIVE until orch.close() drops the WS server.
    """
    seat = make_seat_session(seat=0)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    identity: HumanIdentity = {"kind": "human", "user_id": "alice", "display": "Alice"}
    adapter = HumanAdapter(session=seat, identity=identity)
    await adapter.seated(_make_seat_context())

    # Engine emits HAND_END via observe — sink gets ONE HAND_END frame.
    await adapter.observe(_hand_end_record(), cast(SeatView, {}))
    assert len(sink.by_kind("HAND_END")) == 1

    # Manager calls left("HAND_ENDED") next — must NOT add another HAND_END,
    # and session stays LIVE (no teardown — begin_next_hand() handles that).
    await adapter.left(cast(LeaveReason, "HAND_ENDED"))
    assert len(sink.by_kind("HAND_END")) == 1
    assert seat.state is SeatState.LIVE


async def test_human_adapter_left_hand_ended_is_noop_session_stays_live() -> None:
    """left("HAND_ENDED") is a pure no-op: no HAND_END sent, session stays LIVE.

    The multi-hand orchestrator calls TableSessions.begin_next_hand() after
    run_hand() returns; that method sends DETACH(hand_ended) + ATTACHED for
    the new hand.  Leaving the session LIVE here is what makes that possible.
    (Replaces the old test that asserted UNBOUND, which was the single-hand
    Layer-7 behaviour before the Layer-8 multi-hand refactor.)
    """
    seat = make_seat_session(seat=0)
    sink = FakeSink()
    await seat.attach(sink, user_id="alice")
    identity: HumanIdentity = {"kind": "human", "user_id": "alice", "display": "Alice"}
    adapter = HumanAdapter(session=seat, identity=identity)
    await adapter.seated(_make_seat_context())

    await adapter.left(cast(LeaveReason, "HAND_ENDED"))
    # No HAND_END sent (no prior observe), no teardown.
    assert len(sink.by_kind("HAND_END")) == 0
    assert seat.state is SeatState.LIVE
