"""Table chat (Spec 38): CHAT inbound -> CHAT_MESSAGE broadcast.

Spec: docs/specs/table-chat.md § Verification fixtures 2–3.
"""

from __future__ import annotations

import pytest

from tests.sessions.conftest import FakeSink, make_table_sessions

pytestmark = pytest.mark.asyncio


async def _seated_table():
    """Two LIVE seats (alice seat 0, bob seat 1) + one spectator."""
    sessions = make_table_sessions()
    alice, bob, spec = FakeSink(), FakeSink(), FakeSink()
    assert (await sessions.attach(alice, seat=0, user_id="alice")).ok
    assert (await sessions.attach(bob, seat=1, user_id="bob")).ok
    assert (await sessions.spectate(spec, user_id="carol")).ok
    return sessions, alice, bob, spec


async def test_chat_broadcasts_to_all_live_seats_and_spectators() -> None:
    sessions, alice, bob, spec = await _seated_table()

    await sessions.handle_inbound(alice, {"kind": "CHAT", "text": "nice kong"})

    for sink in (alice, bob, spec):  # sender included — render truth is the echo
        msgs = sink.by_kind("CHAT_MESSAGE")
        assert len(msgs) == 1
        m = msgs[0]
        assert m["seat"] == 0
        assert m["text"] == "nice kong"
        assert m["table_id"] == 17
        assert isinstance(m["seq"], int)
        assert isinstance(m["ts"], str) and m["ts"].endswith("Z")


async def test_chat_strips_whitespace_and_control_chars() -> None:
    sessions, alice, bob, _spec = await _seated_table()

    await sessions.handle_inbound(alice, {"kind": "CHAT", "text": "  hi\x07 there\n  "})

    m = bob.by_kind("CHAT_MESSAGE")[0]
    assert m["text"] == "hi there"


async def test_chat_invalid_text_rejected_no_broadcast() -> None:
    sessions, alice, bob, spec = await _seated_table()

    for bad in ("", "   ", 42, None, "x" * 501):
        await sessions.handle_inbound(alice, {"kind": "CHAT", "text": bad})

    errors = [m for m in alice.by_kind("ERROR") if m.get("code") == "chat_invalid"]
    assert len(errors) == 5
    assert not bob.by_kind("CHAT_MESSAGE")
    assert not spec.by_kind("CHAT_MESSAGE")


async def test_chat_from_spectator_rejected() -> None:
    sessions, _alice, bob, spec = await _seated_table()

    await sessions.handle_inbound(spec, {"kind": "CHAT", "text": "hello players"})

    errors = [m for m in spec.by_kind("ERROR") if m.get("code") == "chat_not_seated"]
    assert len(errors) == 1
    assert not bob.by_kind("CHAT_MESSAGE")


async def test_chat_skips_held_seat_and_does_not_buffer() -> None:
    """Fixture 2 (HELD leg): chat is table talk, not an event — a dropped
    client misses it entirely (no ring-buffer replay on resume)."""
    sessions, alice, bob, _spec = await _seated_table()

    await sessions.on_socket_dropped(bob)  # bob -> HELD
    await sessions.handle_inbound(alice, {"kind": "CHAT", "text": "you there?"})

    assert not bob.by_kind("CHAT_MESSAGE")

    # Bob resumes: the replay buffer must not contain chat.
    bob2 = FakeSink()
    assert (await sessions.attach(bob2, seat=1, user_id="bob")).ok
    assert not bob2.by_kind("CHAT_MESSAGE")


async def test_chat_dead_sink_does_not_break_broadcast() -> None:
    """A dead recipient socket must not prevent others receiving the line
    (same tolerance as event fanout)."""
    sessions, alice, bob, spec = await _seated_table()
    bob.fail_send = True

    await sessions.handle_inbound(alice, {"kind": "CHAT", "text": "still here"})

    assert len(spec.by_kind("CHAT_MESSAGE")) == 1
    assert len(alice.by_kind("CHAT_MESSAGE")) == 1
