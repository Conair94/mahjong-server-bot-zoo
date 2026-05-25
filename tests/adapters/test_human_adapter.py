"""HumanAdapter: SeatAdapter implementation backed by a SessionMux seat slot.

Spec: docs/specs/session-mux.md § The HumanAdapter. Step 7.4 of CHECKLIST.md.

These tests pair a real `SeatSession` with a `FakeSink` (rather than mocking
the session) so the adapter's translation + delegation behavior is verified
end-to-end through one outbound boundary. The session-mux primitives are
already covered by `tests/sessions/`; here we focus on the seat-port-to-mux
translation that the adapter owns.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from mahjong.adapters.base import (
    HumanIdentity,
    LeaveReason,
    Prompt,
    PromptKind,
    SeatAdapter,
    SeatContext,
    SeatError,
)
from mahjong.adapters.human import HumanAdapter
from mahjong.engine.types import Action, SeatView
from mahjong.sessions import SeatSession, SeatState
from tests.sessions.conftest import FakeSink, make_snapshot

pytestmark = pytest.mark.asyncio


# --- helpers ---


def _make_seat_session(*, seat: int = 0, hold_seconds: float = 60.0) -> SeatSession:
    return SeatSession(
        table_id=17,
        seat=seat,
        snapshot_provider=make_snapshot,
        hand_index_provider=lambda: 0,
        hold_seconds=hold_seconds,
    )


def _make_adapter(session: SeatSession) -> HumanAdapter:
    identity: HumanIdentity = {"kind": "human", "user_id": "alice", "display": "Alice"}
    return HumanAdapter(session=session, identity=identity)


def _make_seat_port_prompt(
    *,
    kind: PromptKind = "DISCARD",
    legal: list[Action] | None = None,
    default: Action | None = None,
    deadline_offset: float = 30.0,
    turn_index: int = 0,
) -> Prompt:
    if legal is None:
        legal = [
            cast(Action, {"type": "PLAY", "tile": "W3"}),
            cast(Action, {"type": "PLAY", "tile": "B5"}),
        ]
    if default is None:
        default = cast(Action, {"type": "PLAY", "tile": "W3"})
    loop = asyncio.get_event_loop()
    return {
        "kind": kind,
        "view": cast(SeatView, {}),
        "legal_actions": legal,
        "default_action": default,
        "deadline": loop.time() + deadline_offset,
        "issued_at": loop.time(),
        "context": {"turn_index": turn_index, "phase": "DISCARD"},
    }


def _make_seat_context(seat: int = 0) -> SeatContext:
    return {
        "seat": seat,
        "hand_id": "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "x"},
        "seat_deadline_ms": 1000,
        "initial_view": cast(SeatView, {}),
    }


# --- Tests ---


async def test_adapter_satisfies_seat_adapter_protocol() -> None:
    """Static + runtime: HumanAdapter is a `SeatAdapter`."""
    session = _make_seat_session()
    adapter = _make_adapter(session)
    # `SeatAdapter` is `runtime_checkable`; this catches missing methods.
    assert isinstance(adapter, SeatAdapter)


async def test_seated_observe_decide_left_round_trip() -> None:
    session = _make_seat_session()
    sink = FakeSink()
    await session.attach(sink, user_id="alice")

    adapter = _make_adapter(session)
    await adapter.seated(_make_seat_context())

    # observe: each event becomes an EVENT frame on the sink, projected for
    # this seat (DRAW from seat 0 keeps the tile; DRAW from seat 1 strips it).
    await adapter.observe({"event": "DRAW", "seat": 0, "tile": "W3"}, cast(SeatView, {}))
    await adapter.observe({"event": "DRAW", "seat": 1, "tile": "B2"}, cast(SeatView, {}))
    events = sink.by_kind("EVENT")
    assert len(events) == 2
    assert events[0]["event"].get("tile") == "W3"  # own draw, keeps tile
    assert "tile" not in events[1]["event"]  # opponent draw, projected away

    # decide: server emits PROMPT; client responds; adapter returns the action.
    decide_task = asyncio.create_task(adapter.decide(_make_seat_port_prompt(turn_index=5)))
    await asyncio.sleep(0)
    prompts = sink.by_kind("PROMPT")
    assert len(prompts) == 1
    prompt_id = prompts[0]["prompt_id"]

    chosen: Action = cast(Action, {"type": "PLAY", "tile": "B5"})
    await session.handle_action(prompt_id=prompt_id, action=cast(dict[str, Any], chosen))
    assert await decide_task == chosen

    # left("HAND_ENDED"): session stays LIVE.  The multi-hand orchestrator
    # calls begin_next_hand() to issue DETACH(hand_ended) + ATTACHED(new hand);
    # for single-hand, orch.close() drops the connection.  Tearing down to
    # UNBOUND here would make begin_next_hand() a no-op (Layer 8 fix).
    await adapter.left(cast(LeaveReason, "HAND_ENDED"))
    assert session.state is SeatState.LIVE


async def test_observe_while_held_lands_in_buffer_and_replays_on_resume() -> None:
    """Spec § Ring buffer: 'No event lost in the LIVE→HELD edge.' The adapter
    must not lose events delivered between socket-drop and reconnect."""
    session = _make_seat_session()
    sink_a = FakeSink()
    await session.attach(sink_a, user_id="alice")

    adapter = _make_adapter(session)
    await adapter.seated(_make_seat_context())

    # Two LIVE events.
    await adapter.observe({"event": "DISCARD", "seat": 1, "tile": "W1"}, cast(SeatView, {}))
    await adapter.observe({"event": "DISCARD", "seat": 1, "tile": "W2"}, cast(SeatView, {}))
    assert len(sink_a.by_kind("EVENT")) == 2

    # Drop. Three more observes — these must end up buffered.
    await session.on_socket_dropped(sink_a)
    for i in range(3):
        await adapter.observe(
            {"event": "DISCARD", "seat": 2, "tile": f"B{i + 1}"}, cast(SeatView, {})
        )
    assert session.buffer_size == 3

    # Reconnect within window; buffer replays in order on the new sink.
    sink_b = FakeSink()
    outcome = await session.attach(sink_b, user_id="alice")
    assert outcome.ok
    replayed = [m["event"]["tile"] for m in sink_b.by_kind("EVENT")]
    assert replayed == ["B1", "B2", "B3"]


async def test_decide_translates_seat_hold_expired_to_seat_error() -> None:
    """A seat-hold expiry while a prompt is outstanding raises `SeatError` at
    the adapter boundary (the seat-port type), not the internal
    `SeatHoldExpired` (the session-mux type). This is the contract the
    table manager's strike path relies on."""
    session = _make_seat_session(hold_seconds=0.05)
    sink = FakeSink()
    await session.attach(sink, user_id="alice")

    adapter = _make_adapter(session)
    await adapter.seated(_make_seat_context())

    decide_task = asyncio.create_task(adapter.decide(_make_seat_port_prompt(deadline_offset=10.0)))
    await asyncio.sleep(0)
    await session.on_socket_dropped(sink)
    await asyncio.sleep(0.15)

    with pytest.raises(SeatError):
        await decide_task


async def test_decide_resolves_to_default_action_on_prompt_deadline() -> None:
    """Prompt-deadline path (fixture 5 at the adapter layer): the default
    action surfaces back through the adapter even while HELD."""
    session = _make_seat_session(hold_seconds=60.0)
    sink = FakeSink()
    await session.attach(sink, user_id="alice")
    adapter = _make_adapter(session)
    await adapter.seated(_make_seat_context())

    decide_task = asyncio.create_task(adapter.decide(_make_seat_port_prompt(deadline_offset=0.1)))
    await asyncio.sleep(0)
    await session.on_socket_dropped(sink)
    await asyncio.sleep(0.25)

    result = await decide_task
    # The adapter returned the same default_action the prompt declared.
    assert result == {"type": "PLAY", "tile": "W3"}


async def test_illegal_action_strikes_via_callback_and_prompt_stays_outstanding() -> None:
    """Strike-counter integration (fixture 14 at the adapter layer): an
    illegal ACTION invokes the strike callback the seat session was created
    with, but the adapter's outstanding `decide()` does NOT resolve yet."""
    strikes: list[tuple[int, str]] = []
    session = SeatSession(
        table_id=17,
        seat=0,
        snapshot_provider=make_snapshot,
        hand_index_provider=lambda: 0,
        on_strike=lambda s, code: strikes.append((s, code)),
    )
    sink = FakeSink()
    await session.attach(sink, user_id="alice")
    adapter = _make_adapter(session)
    await adapter.seated(_make_seat_context())

    decide_task = asyncio.create_task(adapter.decide(_make_seat_port_prompt()))
    await asyncio.sleep(0)
    prompt_id = sink.by_kind("PROMPT")[0]["prompt_id"]

    # Illegal action: not in legal_actions.
    await session.handle_action(prompt_id=prompt_id, action={"type": "PLAY", "tile": "T9"})
    assert strikes == [(0, "illegal_action")]
    assert not decide_task.done()

    # Client retries legally; adapter resolves.
    legal: Action = cast(Action, {"type": "PLAY", "tile": "B5"})
    await session.handle_action(prompt_id=prompt_id, action=cast(dict[str, Any], legal))
    assert await decide_task == legal


async def test_left_with_table_closed_drives_session_shutdown_path() -> None:
    """`left("TABLE_CLOSED")` should drive the session into a shutdown that
    sends DETACH(server_shutdown) and closes the sink — not the silent
    hand-end teardown."""
    session = _make_seat_session()
    sink = FakeSink()
    await session.attach(sink, user_id="alice")
    adapter = _make_adapter(session)
    await adapter.seated(_make_seat_context())

    await adapter.left(cast(LeaveReason, "TABLE_CLOSED"))

    assert session.state is SeatState.UNBOUND
    assert sink.closed
    detach_msgs = sink.by_kind("DETACH")
    assert detach_msgs and detach_msgs[0]["reason"] == "table_closed"
