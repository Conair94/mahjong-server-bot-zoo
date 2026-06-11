"""Session multiplexer: state machine, ring buffer, spectator set.

Spec: docs/specs/session-mux.md.

`TableSessions` is the per-table object. It holds four `SeatSession` slots
(always present, mostly `UNBOUND`) and a spectator set. The wire-protocol
layer pushes inbound messages into `TableSessions.handle_inbound`; the table
manager calls `fanout_event`/`fanout_prompt` to push outbound. Two surfaces,
one coordinator.

Each `SeatSession` owns:
- the `UNBOUND ↔ LIVE ↔ HELD` state machine,
- a bounded ring buffer of *projected* wire-event payloads for the current hand,
- a single pending-prompt slot (one outstanding prompt at a time per spec),
- the seat-hold timer.

`Spectator` is intentionally minimal: a sink, a user_id, an outbound seq
counter. Spectators don't buffer, don't time out, don't see prompts. The
inter-hand boundary is transparent to them — they stay subscribed.

Privacy: events are projected at the session-mux boundary via
`project_event(event, seat)` (for players) or `project_event(event, None)` (for
spectators). The codec downstream is dumb; whatever we hand it goes on the
wire.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from mahjong.engine.state import project_event
from mahjong.sessions.timers import IdempotentTimer

DEFAULT_BUFFER_CAPACITY: int = 256
DEFAULT_HOLD_SECONDS: float = 60.0
DEFAULT_MAX_SPECTATORS: int = 32

# Record-event wrapper fields that must NOT appear in the wire HAND_END
# `terminal` payload (record-format.md vs wire-protocol.md § HAND_END).
_HAND_END_WRAPPER_FIELDS: frozenset[str] = frozenset({"event", "seq", "turn_index", "phase", "ts"})


def _terminal_from_record(record_event: Mapping[str, Any]) -> dict[str, Any]:
    """Strip record-format wrapper fields from a HAND_END record event,
    yielding the `terminal` payload the wire HAND_END frame expects."""
    return {k: v for k, v in record_event.items() if k not in _HAND_END_WRAPPER_FIELDS}


# --- Outbound sink Protocol ---


class OutboundSink(Protocol):
    """Structural Protocol satisfied by `mahjong.wire.server.Connection` and
    by test fakes. Three operations: send a wire-message dict, close, query
    closed-ness. The sink owns its own websockets framing; the mux only
    speaks dicts.

    `send` takes `dict[str, Any]` (not `Mapping`) so `Connection.send`
    structurally satisfies this Protocol without needing to widen its own
    signature. Every actual caller (`SeatSession`, `Spectator`, the
    orchestrator) constructs a fresh dict per send, so the narrower input
    type costs nothing.
    """

    async def send(self, msg: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...

    @property
    def closed(self) -> bool: ...


# --- Public data shapes ---


class SeatState(enum.Enum):
    UNBOUND = "UNBOUND"
    LIVE = "LIVE"
    HELD = "HELD"


@dataclass(frozen=True)
class SeatPrompt:
    """Input to `SeatSession.decide`.

    Field names mirror the wire PROMPT shape (`docs/specs/wire-protocol.md
    § PROMPT`) so the HumanAdapter (Step 7.4) can construct one directly from
    a seat-port `Prompt`. The `deadline` is an asyncio monotonic time
    (`loop.time()`), matching seat-port.md's deadline convention; `deadline_ms`
    is the wire-format absolute deadline (Unix epoch ms).
    """

    prompt_id: str
    phase: str  # "DISCARD" | "CLAIM"
    legal_actions: list[dict[str, Any]]
    default_action: dict[str, Any]
    deadline: float  # asyncio loop monotonic time
    deadline_ms: int  # wire-format absolute deadline
    # Spec 37: optional decision-time analysis (shanten/waits/fan/remaining).
    # None → the PROMPT frame carries no `stats` key (pre-Spec-37 shape).
    stats: dict[str, Any] | None = None


class SeatHoldExpired(Exception):
    """The seat-hold window expired with a prompt still outstanding."""


class SeatAttachError(Exception):
    """An attach was rejected. `code` is the wire error code to surface."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


# --- Attach/spectate outcomes ---


@dataclass
class AttachOutcome:
    """Result of `TableSessions.attach`. Either ok (seat is now LIVE under
    this sink), or rejected with a wire-protocol error code. The mux has
    already sent the ATTACHED frame on success; on rejection it has sent the
    ERROR frame on the new sink and made no state change."""

    ok: bool
    error_code: str | None = None


@dataclass
class SpectateOutcome:
    ok: bool
    error_code: str | None = None


# --- Internals ---


@dataclass
class _Pending:
    """One outstanding prompt per seat session.

    The `future` is what the caller of `decide()` awaits; whichever path
    resolves first wins:
    - inbound ACTION ⇒ `set_result(action)`,
    - prompt deadline timer ⇒ `set_result(default_action)`,
    - seat-hold timer ⇒ `set_exception(SeatHoldExpired)`.

    After resolution, all other paths short-circuit on `future.done()`.
    """

    prompt: SeatPrompt
    future: asyncio.Future[dict[str, Any]]
    deadline_timer: IdempotentTimer = field(default_factory=IdempotentTimer)


@dataclass
class _Outbound:
    """A sink plus its per-connection outbound `seq` counter."""

    sink: OutboundSink
    next_seq: int = 1

    def assign_seq(self) -> int:
        s = self.next_seq
        self.next_seq += 1
        return s


# --- Spectator ---


class Spectator:
    """A read-only subscription. Stateless in session-mux beyond sink+identity."""

    def __init__(
        self,
        *,
        sink: OutboundSink,
        user_id: str,
        table_id: int,
    ) -> None:
        self.sink = sink
        self.user_id = user_id
        self.table_id = table_id
        self._outbound = _Outbound(sink=sink)

    async def send_event(self, record_event: dict[str, Any], hand_index: int) -> None:
        # HAND_END is its own top-level wire frame, not EVENT-wrapped
        # (wire-protocol.md § HAND_END). Re-shape and dispatch.
        if record_event.get("event") == "HAND_END":
            await self.send_hand_end(
                hand_index=hand_index,
                terminal=_terminal_from_record(record_event),
                next_hand_seq=None,
            )
            return
        public_event = project_event(record_event, seat=None)
        msg: dict[str, Any] = {
            "kind": "EVENT",
            "seq": self._outbound.assign_seq(),
            "table_id": self.table_id,
            "hand_index": hand_index,
            "event": public_event,
        }
        await self.sink.send(msg)

    async def send_spectating(
        self,
        *,
        hand_index: int,
        snapshot: dict[str, Any],
        spectator_count: int,
    ) -> None:
        msg = {
            "kind": "SPECTATING",
            "seq": self._outbound.assign_seq(),
            "table_id": self.table_id,
            "hand_index": hand_index,
            "snapshot": snapshot,
            "spectator_count": spectator_count,
        }
        await self.sink.send(msg)

    async def send_hand_end(
        self,
        *,
        hand_index: int,
        terminal: dict[str, Any],
        next_hand_seq: int | None,
    ) -> None:
        msg = {
            "kind": "HAND_END",
            "seq": self._outbound.assign_seq(),
            "table_id": self.table_id,
            "hand_index": hand_index,
            "terminal": terminal,
            "next_hand_seq": next_hand_seq,
        }
        await self.sink.send(msg)

    async def send_detach(self, reason: str) -> None:
        msg = {
            "kind": "DETACH",
            "seq": self._outbound.assign_seq(),
            "reason": reason,
            "table_id": self.table_id,
            "seat": -1,  # not a seat; receiver ignores per spec
        }
        try:
            await self.sink.send(msg)
        except Exception:
            return

    async def send_detached(self) -> None:
        msg = {"kind": "DETACHED", "seq": self._outbound.assign_seq()}
        try:
            await self.sink.send(msg)
        except Exception:
            return


# --- SeatSession ---


SnapshotProvider = Callable[[int | None], dict[str, Any]]
HandIndexProvider = Callable[[], int]
StrikeCallback = Callable[[int, str], None]
HoldExpiryCallback = Callable[[int], Awaitable[None]]


class SeatSession:
    """One seat at one table. Lives for the table's lifetime; state moves
    between UNBOUND/LIVE/HELD as clients come and go.

    Construction is cheap: no timers armed, no buffers allocated until first
    attach. Re-attach after `hand_ended` reuses the same instance.
    """

    def __init__(
        self,
        *,
        table_id: int,
        seat: int,
        snapshot_provider: SnapshotProvider,
        hand_index_provider: HandIndexProvider,
        buffer_capacity: int = DEFAULT_BUFFER_CAPACITY,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        on_strike: StrikeCallback | None = None,
        on_hold_expired: HoldExpiryCallback | None = None,
    ) -> None:
        self.table_id = table_id
        self.seat = seat
        self._snapshot_provider = snapshot_provider
        self._hand_index_provider = hand_index_provider
        self.buffer_capacity = buffer_capacity
        self.hold_seconds = hold_seconds
        self._on_strike = on_strike
        self._on_hold_expired = on_hold_expired

        self._state: SeatState = SeatState.UNBOUND
        self._user_id: str | None = None
        self._outbound: _Outbound | None = None

        # Buffer entries are `(wire_kind, payload)` tuples so HELD HAND_END
        # replays as a HAND_END frame, not an EVENT frame:
        #   ("EVENT",    projected_event_dict)
        #   ("HAND_END", terminal_dict)
        self._buffer: deque[tuple[str, dict[str, Any]]] = deque(maxlen=buffer_capacity)
        self._buffer_overflowed: bool = False

        self._pending: _Pending | None = None
        self._hold_timer: IdempotentTimer = IdempotentTimer()
        self._hold_expiry_task: asyncio.Task[None] | None = None
        # Wall-clock (Unix epoch ms) deadline of the current hold window, set
        # on LIVE->HELD and cleared on resume/teardown. Surfaced to clients as
        # `rejoin_deadline_ms` so the lobby can show how long they have to come
        # back (reconnect-rejoin.md, FB-03).
        self._hold_deadline_ms: int | None = None

    # --- properties ---

    @property
    def state(self) -> SeatState:
        return self._state

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def sink(self) -> OutboundSink | None:
        return self._outbound.sink if self._outbound is not None else None

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    @property
    def buffer_overflowed(self) -> bool:
        return self._buffer_overflowed

    @property
    def has_pending_prompt(self) -> bool:
        return self._pending is not None and not self._pending.future.done()

    @property
    def hold_deadline_ms(self) -> int | None:
        """Wall-clock ms when the current hold window expires; None unless HELD."""
        return self._hold_deadline_ms if self._state is SeatState.HELD else None

    # --- attach / detach ---

    async def attach(self, sink: OutboundSink, *, user_id: str) -> AttachOutcome:
        """Bind `sink` to this seat. Implements the conflict-resolution table
        in spec § Conflict resolution.

        - UNBOUND + any user → LIVE under new sink.
        - LIVE + same user → takeover: old sink gets DETACH(replaced_by_new_session)+close,
          new sink takes over; buffer continuity preserved.
        - LIVE + different user → reject with `seat_occupied`.
        - HELD + same user → resume: replay buffer (or fresh snapshot if overflowed).
        - HELD + different user → reject with `seat_not_yours`.
        """
        if self._state is SeatState.UNBOUND:
            return await self._attach_from_unbound(sink, user_id)
        if self._state is SeatState.LIVE:
            if user_id == self._user_id:
                return await self._takeover(sink)
            await self._send_error(sink, "seat_occupied")
            return AttachOutcome(ok=False, error_code="seat_occupied")
        # HELD
        if user_id == self._user_id:
            return await self._resume(sink)
        await self._send_error(sink, "seat_not_yours")
        return AttachOutcome(ok=False, error_code="seat_not_yours")

    async def _attach_from_unbound(self, sink: OutboundSink, user_id: str) -> AttachOutcome:
        self._user_id = user_id
        self._outbound = _Outbound(sink=sink)
        self._buffer.clear()
        self._buffer_overflowed = False
        self._state = SeatState.LIVE
        await self._send_attached(snapshot=self._snapshot_provider(self.seat), resume_buffer_size=0)
        return AttachOutcome(ok=True)

    async def _takeover(self, new_sink: OutboundSink) -> AttachOutcome:
        # Tell A it's being replaced, close A; install B.
        assert self._outbound is not None
        old_sink = self._outbound.sink
        await self._send_detach_server(reason="replaced_by_new_session")
        with contextlib.suppress(Exception):
            await old_sink.close(code=1000, reason="replaced_by_new_session")
        self._outbound = _Outbound(sink=new_sink)
        # Buffer continuity: spec says no replay (no gap from B's POV). Keep
        # the buffer so future drops on B replay correctly.
        await self._send_attached(snapshot=self._snapshot_provider(self.seat), resume_buffer_size=0)
        return AttachOutcome(ok=True)

    async def _resume(self, new_sink: OutboundSink) -> AttachOutcome:
        self._hold_timer.cancel()
        self._hold_deadline_ms = None
        self._outbound = _Outbound(sink=new_sink)
        if self._buffer_overflowed:
            self._buffer.clear()
            self._buffer_overflowed = False
            self._state = SeatState.LIVE
            await self._send_attached(
                snapshot=self._snapshot_provider(self.seat), resume_buffer_size=0
            )
            await self._reprompt_if_pending()
            return AttachOutcome(ok=True)
        replay = list(self._buffer)
        self._buffer.clear()
        self._state = SeatState.LIVE
        await self._send_attached(
            snapshot=self._snapshot_provider(self.seat),
            resume_buffer_size=len(replay),
        )
        for kind, payload in replay:
            if kind == "EVENT":
                await self._emit_event(payload)
            elif kind == "HAND_END":
                await self._emit_hand_end(payload)
        await self._reprompt_if_pending()
        return AttachOutcome(ok=True)

    async def graceful_detach(self) -> None:
        """Client sent `DETACH {reason: "leaving"}`. Tear down regardless of
        whether a prompt is outstanding (the seat is gone; the manager will
        run the strike/replace path)."""
        if self._outbound is not None:
            await self._send_detached_ack()
        if self._pending is not None and not self._pending.future.done():
            self._pending.deadline_timer.cancel()
            self._pending.future.set_exception(SeatHoldExpired("seat_released"))
        self._teardown_to_unbound()

    async def on_socket_dropped(self, sink: OutboundSink) -> None:
        """The wire layer detected the WebSocket closed. Transition to HELD."""
        if self._outbound is None or self._outbound.sink is not sink:
            return  # stale notification; the sink was already swapped out
        if self._state is not SeatState.LIVE:
            return
        self._state = SeatState.HELD
        self._outbound = None
        self._hold_deadline_ms = int(time.time() * 1000) + int(self.hold_seconds * 1000)
        self._hold_timer.arm(self.hold_seconds, self._on_hold_timer_fired)

    def _on_hold_timer_fired(self) -> None:
        if self._state is not SeatState.HELD:
            return
        # Resolve pending prompt as SeatHoldExpired if still outstanding.
        if self._pending is not None and not self._pending.future.done():
            self._pending.deadline_timer.cancel()
            self._pending.future.set_exception(SeatHoldExpired("seat_hold_expired"))
        # External callback (table manager strike escalation, e.g.). The
        # returned task is fire-and-forget; stash it on the instance so the
        # event loop doesn't garbage-collect it mid-flight (RUF006).
        if self._on_hold_expired is not None:
            self._hold_expiry_task = asyncio.ensure_future(self._on_hold_expired(self.seat))
        self._teardown_to_unbound()

    def _teardown_to_unbound(self) -> None:
        self._state = SeatState.UNBOUND
        self._user_id = None
        self._outbound = None
        self._buffer.clear()
        self._buffer_overflowed = False
        self._pending = None
        self._hold_timer.cancel()
        self._hold_deadline_ms = None

    # --- outbound: observe ---

    async def observe(self, record_event: dict[str, Any]) -> None:
        """Project for this seat, send (LIVE) or buffer (HELD).

        HAND_END is special: per wire-protocol.md it is a top-level frame,
        not EVENT-wrapped. We re-shape the record event into a `terminal`
        payload and dispatch to the HAND_END path.
        """
        if self._state is SeatState.UNBOUND:
            return
        if record_event.get("event") == "HAND_END":
            terminal = _terminal_from_record(record_event)
            if self._state is SeatState.LIVE:
                await self._emit_hand_end(terminal)
            else:
                self._append_buffer(("HAND_END", terminal))
            return
        projected = project_event(record_event, seat=self.seat)
        if self._state is SeatState.LIVE:
            await self._emit_event(projected)
            return
        self._append_buffer(("EVENT", projected))

    def _append_buffer(self, entry: tuple[str, dict[str, Any]]) -> None:
        if len(self._buffer) >= self.buffer_capacity:
            self._buffer_overflowed = True
        self._buffer.append(entry)

    async def _emit_event(self, projected_event: dict[str, Any]) -> None:
        assert self._outbound is not None
        msg = {
            "kind": "EVENT",
            "seq": self._outbound.assign_seq(),
            "table_id": self.table_id,
            "hand_index": self._hand_index_provider(),
            "event": projected_event,
        }
        try:
            await self._outbound.sink.send(msg)
        except Exception:
            # Send failed; transport will surface a drop separately. Just
            # treat as transparent for now; the buffer path picks up on the
            # next observe once on_socket_dropped fires.
            return

    async def _emit_hand_end(self, terminal: dict[str, Any]) -> None:
        """Send the top-level HAND_END frame for a HAND_END record event
        that arrived via observe(). `next_hand_seq` is None at this layer;
        a multi-hand orchestrator (Step 7.6.ii+) may override later."""
        if self._outbound is None:
            return
        await self._send_hand_end(terminal=terminal, next_hand_seq=None)

    # --- outbound: decide ---

    async def decide(self, prompt: SeatPrompt) -> dict[str, Any]:
        """Send PROMPT (if LIVE), await ACTION/default/expiry, return the
        chosen action dict. Raises `SeatHoldExpired` if the seat-hold timer
        pre-empts.

        Per spec § Pending prompt: 'one outstanding prompt at a time'. Caller
        guarantees this; we assert.
        """
        assert self._pending is None or self._pending.future.done(), (
            "two concurrent decide() calls on the same seat"
        )
        loop = asyncio.get_event_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        pending = _Pending(prompt=prompt, future=future)
        self._pending = pending

        # Arm prompt deadline relative to the monotonic deadline.
        delay = max(0.0, prompt.deadline - loop.time())
        pending.deadline_timer.arm(delay, self._on_prompt_deadline)

        if self._state is SeatState.LIVE:
            await self._send_prompt(prompt)

        try:
            return await future
        finally:
            pending.deadline_timer.cancel()
            if self._pending is pending:
                self._pending = None

    def _on_prompt_deadline(self) -> None:
        if self._pending is None or self._pending.future.done():
            return
        self._pending.future.set_result(dict(self._pending.prompt.default_action))

    async def _send_prompt(self, prompt: SeatPrompt) -> None:
        assert self._outbound is not None
        msg = {
            "kind": "PROMPT",
            "seq": self._outbound.assign_seq(),
            "table_id": self.table_id,
            "hand_index": self._hand_index_provider(),
            "seat": self.seat,
            "phase": prompt.phase,
            "legal_actions": list(prompt.legal_actions),
            "default_action": dict(prompt.default_action),
            "deadline_ms": prompt.deadline_ms,
            "prompt_id": prompt.prompt_id,
        }
        if prompt.stats is not None:
            msg["stats"] = prompt.stats
        try:
            await self._outbound.sink.send(msg)
        except Exception:
            return

    async def _reprompt_if_pending(self) -> None:
        if self._pending is None or self._pending.future.done():
            return
        await self._send_prompt(self._pending.prompt)

    # --- inbound: ACTION dispatch ---

    async def handle_action(self, *, prompt_id: str, action: dict[str, Any]) -> None:
        """Inbound ACTION arrived. Validate and resolve, or send ERROR."""
        if self._pending is None or self._pending.future.done():
            await self._send_error(self.sink_required(), "no_outstanding_prompt")
            return
        if prompt_id != self._pending.prompt.prompt_id:
            await self._send_error(self.sink_required(), "stale_action")
            return
        if action not in self._pending.prompt.legal_actions:
            await self._send_error(self.sink_required(), "illegal_action")
            if self._on_strike is not None:
                self._on_strike(self.seat, "illegal_action")
            # Prompt stays outstanding; client may retry.
            return
        self._pending.future.set_result(dict(action))

    def sink_required(self) -> OutboundSink:
        assert self._outbound is not None, "no live sink"
        return self._outbound.sink

    # --- outbound: framing helpers ---

    async def _send_attached(self, *, snapshot: dict[str, Any], resume_buffer_size: int) -> None:
        assert self._outbound is not None
        msg = {
            "kind": "ATTACHED",
            "seq": self._outbound.assign_seq(),
            "table_id": self.table_id,
            "seat": self.seat,
            "hand_index": self._hand_index_provider(),
            "snapshot": snapshot,
            "resume_buffer_size": resume_buffer_size,
        }
        try:
            await self._outbound.sink.send(msg)
        except Exception:
            return

    async def _send_detach_server(self, *, reason: str) -> None:
        assert self._outbound is not None
        msg = {
            "kind": "DETACH",
            "seq": self._outbound.assign_seq(),
            "reason": reason,
            "table_id": self.table_id,
            "seat": self.seat,
        }
        try:
            await self._outbound.sink.send(msg)
        except Exception:
            return

    async def _send_detached_ack(self) -> None:
        assert self._outbound is not None
        msg = {"kind": "DETACHED", "seq": self._outbound.assign_seq()}
        try:
            await self._outbound.sink.send(msg)
        except Exception:
            return

    async def _send_hand_end(self, *, terminal: dict[str, Any], next_hand_seq: int | None) -> None:
        if self._outbound is None:
            return
        msg = {
            "kind": "HAND_END",
            "seq": self._outbound.assign_seq(),
            "table_id": self.table_id,
            "hand_index": self._hand_index_provider(),
            "terminal": terminal,
            "next_hand_seq": next_hand_seq,
        }
        try:
            await self._outbound.sink.send(msg)
        except Exception:
            return

    async def _send_error(self, sink: OutboundSink | None, code: str) -> None:
        if sink is None:
            return
        try:
            await sink.send({"kind": "ERROR", "code": code})
        except Exception:
            return

    # --- hand-end / shutdown ---

    async def hand_ended(self, *, terminal: dict[str, Any], next_hand_seq: int | None) -> None:
        """Called when the hand finishes. Per spec: players' HumanAdapter is
        recreated per hand, so the seat goes UNBOUND between hands. Send
        HAND_END to LIVE sink, resolve pending prompts, tear down.

        Prefer routing HAND_END record events through `observe()` for the
        single-sender invariant (see Step 7.6.i). This entry point remains
        for callers that have a pre-built `terminal` payload and want to
        attach `next_hand_seq` (e.g. a multi-hand orchestrator)."""
        if self._state is SeatState.LIVE:
            await self._send_hand_end(terminal=terminal, next_hand_seq=next_hand_seq)
        self._resolve_pending_and_teardown()

    async def unbind_after_hand_end(self) -> None:
        """Tear down without sending HAND_END. Use after `observe()` has
        already routed a HAND_END record event into the wire frame, so the
        teardown doesn't double-emit."""
        self._resolve_pending_and_teardown()

    async def begin_next_hand(self, *, snapshot: dict[str, Any]) -> None:
        """Inter-hand boundary transition.  Resets per-hand state and advances
        the seat into the next hand.

        LIVE seats: send ``DETACH { reason: 'hand_ended' }`` (per
        session-mux.md § Why spectators stay subscribed) then ``ATTACHED``
        for the new hand.  The WebSocket stays open; the seat remains LIVE.

        HELD seats: cancel the hold timer and reset per-hand buffers.  The
        seat stays HELD with ``_user_id`` preserved — when the player
        reconnects, ``_resume`` will deliver ``ATTACHED`` for the new hand
        (using the updated ``_snapshot_provider``).

        UNBOUND seats: no-op.  The client will ATTACH fresh; they receive
        ``ATTACHED`` for the new hand through the normal attach path.

        Assumes HAND_END was already sent via the ``observe()`` path (the
        single-sender invariant).  Does NOT re-send HAND_END.
        """
        # Resolve any lingering prompt (defensive — should already be done).
        if self._pending is not None and not self._pending.future.done():
            self._pending.deadline_timer.cancel()
            self._pending.future.set_exception(SeatHoldExpired("hand_ended"))
        self._pending = None

        # Reset per-hand ring buffer.
        self._buffer.clear()
        self._buffer_overflowed = False

        if self._state is SeatState.LIVE:
            assert self._outbound is not None
            # Signal old-hand boundary per wire-protocol spec.
            await self._send_detach_server(reason="hand_ended")
            # Send ATTACHED for the new hand on the same connection.
            await self._send_attached(snapshot=snapshot, resume_buffer_size=0)
            # State remains LIVE; _user_id and _outbound are unchanged.

        elif self._state is SeatState.HELD:
            # Cancel any outstanding hold timer — the new hand's hold window
            # begins fresh when they reconnect.
            self._hold_timer.cancel()
            self._hold_deadline_ms = None
            # HELD: _user_id is preserved; they will _resume() → ATTACHED.
            # (snapshot_provider already updated by the orchestrator before
            # begin_next_hand is called, so _resume picks up the new state.)

    def _resolve_pending_and_teardown(self) -> None:
        if self._pending is not None and not self._pending.future.done():
            self._pending.deadline_timer.cancel()
            self._pending.future.set_exception(SeatHoldExpired("hand_ended"))
        self._teardown_to_unbound()

    async def shutdown(self, *, reason: str = "server_shutdown") -> None:
        """Server-lifecycle drain. LIVE → send DETACH+close; HELD → cancel
        hold timer + resolve pending. Then UNBOUND."""
        if self._state is SeatState.LIVE:
            assert self._outbound is not None
            sink = self._outbound.sink
            await self._send_detach_server(reason=reason)
            with contextlib.suppress(Exception):
                await sink.close(code=1001, reason=reason)
        if self._pending is not None and not self._pending.future.done():
            self._pending.deadline_timer.cancel()
            # Per fixture 11 commentary: either default or SeatError is OK.
            # We choose default to match the prompt-deadline path's outcome
            # (table manager applies default_action on drain).
            self._pending.future.set_result(dict(self._pending.prompt.default_action))
        self._teardown_to_unbound()


# --- TableSessions ---


class TableSessions:
    """Per-table coordinator. Owns four `SeatSession`s + a spectator set.

    Outbound: `fanout_event` and `fanout_prompt`/`fanout_hand_end` are called
    by the table manager (or HumanAdapter, transitively). Inbound:
    `handle_inbound(sink, msg)` is called by the connection's read loop.
    """

    def __init__(
        self,
        *,
        table_id: int,
        snapshot_provider: SnapshotProvider,
        hand_index_provider: HandIndexProvider,
        max_spectators: int = DEFAULT_MAX_SPECTATORS,
        buffer_capacity: int = DEFAULT_BUFFER_CAPACITY,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        on_strike: StrikeCallback | None = None,
        on_hold_expired: HoldExpiryCallback | None = None,
        shutting_down: Callable[[], bool] | None = None,
    ) -> None:
        self.table_id = table_id
        self._snapshot_provider = snapshot_provider
        self._hand_index_provider = hand_index_provider
        self.max_spectators = max_spectators
        self._shutting_down = shutting_down or (lambda: False)
        self._seats: list[SeatSession] = [
            SeatSession(
                table_id=table_id,
                seat=i,
                snapshot_provider=snapshot_provider,
                hand_index_provider=hand_index_provider,
                buffer_capacity=buffer_capacity,
                hold_seconds=hold_seconds,
                on_strike=on_strike,
                on_hold_expired=on_hold_expired,
            )
            for i in range(4)
        ]
        self._spectators: dict[int, Spectator] = {}  # keyed by id(sink)

    # --- seats ---

    def seat(self, seat: int) -> SeatSession:
        return self._seats[seat]

    async def attach(self, sink: OutboundSink, *, user_id: str, seat: int) -> AttachOutcome:
        if self._shutting_down():
            await self._send_error(sink, "shutting_down")
            return AttachOutcome(ok=False, error_code="shutting_down")
        if seat < 0 or seat >= 4:
            await self._send_error(sink, "seat_not_yours")
            return AttachOutcome(ok=False, error_code="seat_not_yours")
        return await self._seats[seat].attach(sink, user_id=user_id)

    async def graceful_detach(self, sink: OutboundSink) -> None:
        owner = self._seat_owning(sink)
        if owner is not None:
            await owner.graceful_detach()

    async def on_socket_dropped(self, sink: OutboundSink) -> None:
        """Called when a connection's WS closed. Routes to whichever owner
        held the sink (seat or spectator); idempotent if neither did."""
        sink_id = id(sink)
        if sink_id in self._spectators:
            del self._spectators[sink_id]
            return
        owner = self._seat_owning(sink)
        if owner is not None:
            await owner.on_socket_dropped(sink)

    def _seat_owning(self, sink: OutboundSink) -> SeatSession | None:
        for s in self._seats:
            if s.sink is sink:
                return s
        return None

    # --- spectators ---

    async def spectate(self, sink: OutboundSink, *, user_id: str) -> SpectateOutcome:
        if self._shutting_down():
            await self._send_error(sink, "shutting_down")
            return SpectateOutcome(ok=False, error_code="shutting_down")
        if len(self._spectators) >= self.max_spectators:
            await self._send_error(sink, "spectator_limit_reached")
            return SpectateOutcome(ok=False, error_code="spectator_limit_reached")
        spectator = Spectator(sink=sink, user_id=user_id, table_id=self.table_id)
        self._spectators[id(sink)] = spectator
        await spectator.send_spectating(
            hand_index=self._hand_index_provider(),
            snapshot=self._snapshot_provider(None),
            spectator_count=len(self._spectators),
        )
        return SpectateOutcome(ok=True)

    async def stop_spectating(self, sink: OutboundSink) -> None:
        spec = self._spectators.pop(id(sink), None)
        if spec is not None:
            await spec.send_detached()

    @property
    def spectator_count(self) -> int:
        return len(self._spectators)

    # --- fanout ---

    async def fanout_event(self, record_event: dict[str, Any]) -> None:
        """Project + send to each seat session (own projection) and each
        spectator (public projection). Failure on one recipient does not
        block the others — same independence guarantee as
        manager.py:_fanout_observe.

        Prefer `fanout_event_to_spectators` when the seat fanout already
        happens through the manager's adapter path (the orchestrator case
        for Layer 7.6+) — double-firing seat observes would double-emit
        wire frames.
        """
        # Seats first, in seat order, for deterministic test assertions.
        for s in self._seats:
            await s.observe(record_event)
        await self.fanout_event_to_spectators(record_event)

    async def fanout_event_to_spectators(self, record_event: dict[str, Any]) -> None:
        """Spectator-only fanout. Use when seat fanout is driven elsewhere
        (e.g. via `HumanAdapter.observe` → `SeatSession.observe` inside
        `manager.run_hand`'s adapter loop). Spectators see the public
        projection (`project_event(event, seat=None)`); HAND_END is
        dispatched to `Spectator.send_hand_end` per wire-protocol shape."""
        for spec in list(self._spectators.values()):
            try:
                await spec.send_event(record_event, hand_index=self._hand_index_provider())
            except Exception:
                continue

    async def begin_next_hand(self) -> None:
        """Inter-hand boundary: reset all seat sessions and issue ``ATTACHED``
        for the new hand to still-connected clients.

        The orchestrator must update its ``_initial_state`` (and therefore the
        ``snapshot_provider`` it passed at construction) BEFORE calling this
        method so that each seat's ``ATTACHED`` carries the new-hand snapshot.

        Spectators are unaffected: they stay subscribed transparently across
        hand boundaries per session-mux.md § Why spectators stay subscribed.
        """
        for seat in self._seats:
            await seat.begin_next_hand(snapshot=self._snapshot_provider(seat.seat))

    async def fanout_hand_end(self, *, terminal: dict[str, Any], next_hand_seq: int | None) -> None:
        for s in self._seats:
            await s.hand_ended(terminal=terminal, next_hand_seq=next_hand_seq)
        for spec in list(self._spectators.values()):
            try:
                await spec.send_hand_end(
                    hand_index=self._hand_index_provider(),
                    terminal=terminal,
                    next_hand_seq=next_hand_seq,
                )
            except Exception:
                continue

    async def shutdown(self, *, reason: str = "server_shutdown") -> None:
        for s in self._seats:
            await s.shutdown(reason=reason)
        for spec in list(self._spectators.values()):
            await spec.send_detach(reason)
            with contextlib.suppress(Exception):
                await spec.sink.close(code=1001, reason=reason)
        self._spectators.clear()

    # --- inbound dispatch ---

    async def handle_inbound(self, sink: OutboundSink, msg: Mapping[str, Any]) -> None:
        """Route an inbound wire message to the appropriate owner.

        Recognized kinds at this layer: ACTION, DETACH (client), STOP_SPECTATING.
        Unrecognized kinds get an `unknown_kind`-style ERROR. ATTACH/SPECTATE
        are entry points and arrive via `attach`/`spectate` instead.
        """
        kind = msg.get("kind")
        if kind == "ACTION":
            owner = self._seat_owning(sink)
            if owner is None:
                await self._send_error(sink, "no_outstanding_prompt")
                return
            prompt_id = msg.get("prompt_id")
            action = msg.get("action")
            if not isinstance(prompt_id, str) or not isinstance(action, dict):
                await self._send_error(sink, "framing")
                return
            await owner.handle_action(prompt_id=prompt_id, action=action)
            return
        if kind == "DETACH":
            await self.graceful_detach(sink)
            return
        if kind == "STOP_SPECTATING":
            await self.stop_spectating(sink)
            return
        await self._send_error(sink, "unknown_kind")

    async def _send_error(self, sink: OutboundSink, code: str) -> None:
        try:
            await sink.send({"kind": "ERROR", "code": code})
        except Exception:
            return


__all__ = [
    "DEFAULT_BUFFER_CAPACITY",
    "DEFAULT_HOLD_SECONDS",
    "DEFAULT_MAX_SPECTATORS",
    "AttachOutcome",
    "OutboundSink",
    "SeatAttachError",
    "SeatHoldExpired",
    "SeatPrompt",
    "SeatSession",
    "SeatState",
    "SpectateOutcome",
    "Spectator",
    "TableSessions",
]
