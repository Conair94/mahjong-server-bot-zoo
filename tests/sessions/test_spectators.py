"""Spectator fixtures (16, 17, 18, 19, 20, 21).

Spec: docs/specs/session-mux.md § Spectator handling. Step 7.3 of CHECKLIST.md.
"""

from __future__ import annotations

import pytest

from tests.sessions.conftest import FakeSink, make_snapshot, make_table_sessions

pytestmark = pytest.mark.asyncio


# ----- Fixture 16: subscribe → public-projected event stream -----


async def test_spectator_subscribe_yields_public_snapshot_and_projected_events() -> None:
    sessions = make_table_sessions()
    sink = FakeSink()
    outcome = await sessions.spectate(sink, user_id="watcher1")
    assert outcome.ok

    spectating = sink.by_kind("SPECTATING")
    assert len(spectating) == 1
    assert spectating[0]["snapshot"] == make_snapshot(None)
    assert spectating[0]["spectator_count"] == 1

    # A DRAW event with a private tile is projected away for spectators.
    await sessions.fanout_event({"event": "DRAW", "seat": 0, "tile": "W3"})
    events = sink.by_kind("EVENT")
    assert len(events) == 1
    assert "tile" not in events[0]["event"]
    assert events[0]["event"]["seat"] == 0

    # No PROMPT is ever delivered to a spectator path.
    assert sink.by_kind("PROMPT") == []


# ----- Fixture 17: spectator drop is immediate -----


async def test_spectator_drop_releases_immediately_no_hold() -> None:
    sessions = make_table_sessions()
    sink = FakeSink()
    await sessions.spectate(sink, user_id="watcher1")
    assert sessions.spectator_count == 1

    await sessions.on_socket_dropped(sink)
    assert sessions.spectator_count == 0


# ----- Fixture 18: multiple spectators receive identical inner-event payloads -----


async def test_multiple_spectators_receive_identical_streams() -> None:
    sessions = make_table_sessions()
    sinks = [FakeSink(), FakeSink()]
    for sink in sinks:
        await sessions.spectate(sink, user_id="watcher")

    await sessions.fanout_event({"event": "DRAW", "seat": 1, "tile": "B2"})
    await sessions.fanout_event({"event": "DISCARD", "seat": 1, "tile": "W7"})

    inner_a = [m["event"] for m in sinks[0].by_kind("EVENT")]
    inner_b = [m["event"] for m in sinks[1].by_kind("EVENT")]
    assert inner_a == inner_b
    # Outer seq may differ (per-connection counter); pin that they reset
    # independently — each sink saw 1 SPECTATING + 2 EVENT.
    assert [m["seq"] for m in sinks[0].messages] == [1, 2, 3]
    assert [m["seq"] for m in sinks[1].messages] == [1, 2, 3]


# ----- Fixture 19: MAHJONG_MAX_SPECTATORS_PER_TABLE enforced -----


async def test_spectator_limit_enforced_and_freed_on_drop() -> None:
    sessions = make_table_sessions(max_spectators=2)
    sinks = [FakeSink(), FakeSink()]
    for sink in sinks:
        outcome = await sessions.spectate(sink, user_id="watcher")
        assert outcome.ok

    overflow_sink = FakeSink()
    outcome = await sessions.spectate(overflow_sink, user_id="watcher_overflow")
    assert outcome.error_code == "spectator_limit_reached"
    err = [m for m in overflow_sink.messages if m["kind"] == "ERROR"]
    assert err and err[0]["code"] == "spectator_limit_reached"

    # Free a slot; another spectator can now subscribe.
    await sessions.on_socket_dropped(sinks[0])
    fourth_sink = FakeSink()
    outcome = await sessions.spectate(fourth_sink, user_id="watcher_fourth")
    assert outcome.ok


# ----- Fixture 20: spectator stays subscribed across hand boundary -----


async def test_spectator_stays_subscribed_across_hand_boundary() -> None:
    sessions = make_table_sessions()
    sink = FakeSink()
    await sessions.spectate(sink, user_id="watcher")

    await sessions.fanout_hand_end(terminal={"kind": "DRAW_NO_TILES"}, next_hand_seq=42)

    # Spectator received HAND_END.
    he = sink.by_kind("HAND_END")
    assert len(he) == 1
    assert he[0]["next_hand_seq"] == 42

    # Subsequent events from the new hand still flow without re-subscribe.
    assert sessions.spectator_count == 1
    await sessions.fanout_event({"event": "DRAW", "seat": 0, "tile": "T5"})
    events = sink.by_kind("EVENT")
    assert len(events) == 1
    # And it's still public-projected.
    assert "tile" not in events[0]["event"]


# ----- Fixture 21: spectator served public projection for own-draw events -----


async def test_spectator_own_draw_projection_strips_tile_for_spectator_only() -> None:
    sessions = make_table_sessions()
    spec_sink = FakeSink()
    await sessions.spectate(spec_sink, user_id="watcher")

    # Also bind seat 1 as a player so we can compare projections.
    player_sink = FakeSink()
    await sessions.attach(player_sink, user_id="seat1_user", seat=1)

    await sessions.fanout_event({"event": "DRAW", "seat": 1, "tile": "B5"})

    # Player at seat 1 (the drawer) sees the tile.
    player_event = player_sink.by_kind("EVENT")[0]["event"]
    assert player_event.get("tile") == "B5"

    # Spectator does NOT see the tile.
    spec_event = spec_sink.by_kind("EVENT")[0]["event"]
    assert "tile" not in spec_event
