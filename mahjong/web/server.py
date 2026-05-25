"""Web orchestrator: production WS+static server that drives one or more hands.

Spec: docs/specs/tui-client.md fixture 18 (S2 exit gate), CHECKLIST Steps 7.6.ii
      and 8.1 (multi-hand orchestration).

v1 scope: one table, four seats.  Seat 0 is reserved for a human connecting
over WebSocket; seats 1-3 are ``CannedAdapter``s configured at construction
time.  After the first successful seat-0 ATTACH, the orchestrator runs a hand
loop in a background task:

    For each hand in range(max_hands):
        1. ``manager.run_hand`` runs the hand to completion.
        2. Between hands: sleep ``between_hand_pause_seconds``, rotate dealer,
           compute new ``_initial_state``, call
           ``sessions.begin_next_hand()`` which issues
           ``DETACH { reason: 'hand_ended' }`` + ``ATTACHED`` for the next
           hand to still-connected clients.

Multiple tables, authentication, and score persistence land in later Layer 8
steps.  Spectator support landed in 7.6.iv.

Layer-8 changes vs 7.6:
  - ``max_hands`` (default 1) and ``between_hand_pause_seconds`` constructor
    params.
  - ``_hand_index`` / ``_dealer_seat`` mutable instance state (between-hand
    accounting).
  - ``_run_hand`` renamed ``_run_hand_loop``; it loops over hands.
  - Per-hand record path: hand 0 uses ``record_path`` as-is; hand N > 0 uses
    ``{stem}_{N}{suffix}`` (so existing single-hand tests need no path changes).
  - ``initial_state`` called with ``dealer_seat`` and ``hand_index`` kwargs
    (Layer-8 engine amendment).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from mahjong.adapters.base import HumanIdentity, SeatAdapter
from mahjong.adapters.canned import CannedAdapter
from mahjong.adapters.human import HumanAdapter
from mahjong.engine import initial_state
from mahjong.engine.state import project as project_state
from mahjong.engine.types import Action, GameState, RuleSetRef
from mahjong.sessions import TableSessions
from mahjong.sessions.mux import DEFAULT_HOLD_SECONDS
from mahjong.table import manager as mgr
from mahjong.wire.server import Connection, WebSocketServer

_logger = logging.getLogger(__name__)

DEFAULT_TABLE_ID: int = 1
HUMAN_SEAT: int = 0
FIRST_FRAME_TIMEOUT_S: float = 5.0
SERVER_ID: str = "mahjong-server-web"


IdentityFactory = Callable[[Connection], HumanIdentity]


def _default_identity_factory(conn: Connection) -> HumanIdentity:
    suffix = str(conn.connection_id)
    return {"kind": "human", "user_id": f"u_{suffix}", "display": f"player-{suffix}"}


class WebOrchestrator:
    """Multi-hand WS orchestrator (one table).

    Holds a ``WebSocketServer`` (transport), a ``TableSessions`` (per-table
    state), and a ``_hand_loop`` background task.  The loop kicks off the
    first time a client successfully ATTACHes to the human seat; subsequent
    hands start automatically after the between-hand pause.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        ruleset: RuleSetRef,
        seed: int,
        hand_id: str,
        record_path: Path,
        server_info: dict[str, Any],
        canned_seat_actions: dict[int, list[Action]] | None = None,
        identity_factory: IdentityFactory | None = None,
        static_dir: Path | None = None,
        table_id: int = DEFAULT_TABLE_ID,
        decide_timeout_seconds: float = 30.0,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        strike_limit: int = 3,
        max_hands: int | None = 1,
        between_hand_pause_seconds: float = 2.0,
    ) -> None:
        self._host = host
        self._port = port
        self._ruleset = ruleset
        self._seed = seed
        self._hand_id = hand_id
        self._record_path = record_path
        self._server_info = server_info
        self._identity_factory = identity_factory or _default_identity_factory
        self._static_dir = static_dir
        self._table_id = table_id
        self._decide_timeout_seconds = decide_timeout_seconds
        self._hold_seconds = hold_seconds
        self._strike_limit = strike_limit
        self._max_hands = max_hands
        self._between_hand_pause_seconds = between_hand_pause_seconds

        # Between-hand mutable state
        self._hand_index: int = 0
        self._dealer_seat: int = 0

        # Pre-build the initial state for hand 0 so ATTACHED carries a correct
        # snapshot before ``run_hand`` runs.  Same seed → byte-identical state
        # inside ``run_hand``, so there's no drift between attach-time and hand-time.
        self._initial_state: GameState = initial_state(
            ruleset, seed=seed, dealer_seat=0, hand_index=0
        )

        # CannedAdapters fill the non-human seats.  Empty action lists ⇒ each
        # ``decide`` returns ``prompt.default_action``.  The orchestrator owns
        # them; tests inject scripts via ``canned_seat_actions``.
        actions_by_seat = canned_seat_actions or {}
        self._canned_adapters: dict[int, CannedAdapter] = {
            seat: CannedAdapter(
                identity={"kind": "canned", "script": "pass"},
                actions=list(actions_by_seat.get(seat, [])),
            )
            for seat in range(4)
            if seat != HUMAN_SEAT
        }

        self._sessions: TableSessions = TableSessions(
            table_id=self._table_id,
            snapshot_provider=self._snapshot_provider,
            hand_index_provider=lambda: self._hand_index,
            hold_seconds=self._hold_seconds,
        )
        self._ws_server: WebSocketServer | None = None
        self._hand_task: asyncio.Task[None] | None = None
        self._match_done: asyncio.Event = asyncio.Event()
        self._start_hand_lock = asyncio.Lock()
        self._hello_seq: int = 1

    # --- lifecycle ---

    async def start(self) -> None:
        if self._ws_server is not None:
            raise RuntimeError("WebOrchestrator already started")
        self._ws_server = WebSocketServer(
            host=self._host,
            port=self._port,
            handler=self._handler,
            static_dir=self._static_dir,
        )
        await self._ws_server.start()

    async def close(self) -> None:
        if self._hand_task is not None and not self._hand_task.done():
            self._hand_task.cancel()
            with contextlib.suppress(BaseException):
                await self._hand_task
        if self._ws_server is not None:
            await self._ws_server.close()
            self._ws_server = None

    @property
    def port(self) -> int:
        if self._ws_server is None:
            return self._port
        return self._ws_server.port

    async def wait_hand_complete(self, *, timeout: float | None = None) -> None:
        """Wait until the match (all hands) has completed.

        Named ``wait_hand_complete`` for backwards compatibility with callers
        that ran a single-hand orchestrator.  For single-hand (``max_hands=1``,
        the default), this is equivalent to waiting for that one hand.
        """
        if timeout is None:
            await self._match_done.wait()
            return
        await asyncio.wait_for(self._match_done.wait(), timeout=timeout)

    # --- providers for TableSessions ---

    def _snapshot_provider(self, seat: int | None) -> dict[str, Any]:
        return cast(dict[str, Any], project_state(self._initial_state, seat))

    def _record_path_for_hand(self, hand_index: int) -> Path:
        """Per-hand record path.  Hand 0 uses ``_record_path`` as-is so that
        single-hand callers (and the S2 byte-identical fixture test) need no
        changes.  Hand N > 0 uses ``{stem}_{N}{suffix}``."""
        if hand_index == 0:
            return self._record_path
        return self._record_path.parent / (
            f"{self._record_path.stem}_{hand_index}{self._record_path.suffix}"
        )

    def _hand_id_for_hand(self, hand_index: int) -> str:
        """Per-hand hand_id.  Hand 0 uses the original ``_hand_id`` so the S2
        byte-identical fixture record is unchanged."""
        if hand_index == 0:
            return self._hand_id
        return f"{self._hand_id}_{hand_index}"

    # --- WS handler ---

    async def _handler(self, conn: Connection) -> None:
        """Per-connection: HELLO → first inbound (ATTACH / SPECTATE) →
        forward subsequent inbound to TableSessions until socket drops."""
        await self._send_hello(conn)
        try:
            first = await asyncio.wait_for(conn.recv(), timeout=FIRST_FRAME_TIMEOUT_S)
        except (TimeoutError, Exception):
            return

        kind = first.get("kind")
        if kind == "ATTACH":
            if not await self._handle_attach(conn, first):
                return
        elif kind == "SPECTATE":
            if not await self._handle_spectate(conn):
                return
        else:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "unexpected_kind"})
            return

        try:
            async for msg in conn:
                await self._sessions.handle_inbound(conn, msg)
        finally:
            await self._sessions.on_socket_dropped(conn)

    async def _send_hello(self, conn: Connection) -> None:
        await conn.send(
            {
                "kind": "HELLO",
                "seq": self._hello_seq,
                "protocol_version": 1,
                "server_id": SERVER_ID,
            }
        )
        self._hello_seq += 1

    async def _handle_attach(self, conn: Connection, msg: dict[str, Any]) -> bool:
        """Handle ATTACH. Returns True if attach succeeded and the inbound
        loop should continue; False if the connection should close."""
        seat = msg.get("seat")
        if seat != HUMAN_SEAT:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "seat_not_yours"})
            return False
        identity = self._identity_factory(conn)
        outcome = await self._sessions.attach(conn, user_id=identity["user_id"], seat=seat)
        if not outcome.ok:
            return False
        async with self._start_hand_lock:
            if self._hand_task is None:
                self._hand_task = asyncio.create_task(self._run_hand_loop(identity))
        return True

    async def _handle_spectate(self, conn: Connection) -> bool:
        outcome = await self._sessions.spectate(conn, user_id=f"spec_{conn.connection_id}")
        return outcome.ok

    async def _run_hand_loop(self, human_identity: HumanIdentity) -> None:
        """Background task: run hands in a loop until ``max_hands`` is reached
        (or indefinitely if ``max_hands`` is None).

        Between hands: sleep, rotate dealer, compute new ``_initial_state``,
        then call ``sessions.begin_next_hand()`` which sends
        ``DETACH { reason: 'hand_ended' }`` + ``ATTACHED`` for the new hand
        to still-connected clients.

        Spectators stay subscribed transparently across hand boundaries per
        session-mux.md § Why spectators stay subscribed.
        """
        try:
            while True:
                hand_seed = self._seed + self._hand_index
                human_session = self._sessions.seat(HUMAN_SEAT)
                human = HumanAdapter(session=human_session, identity=human_identity)
                adapters: list[SeatAdapter] = [
                    cast(SeatAdapter, human),
                    cast(SeatAdapter, self._canned_adapters[1]),
                    cast(SeatAdapter, self._canned_adapters[2]),
                    cast(SeatAdapter, self._canned_adapters[3]),
                ]

                await mgr.run_hand(
                    adapters=adapters,
                    ruleset=self._ruleset,
                    seed=hand_seed,
                    hand_id=self._hand_id_for_hand(self._hand_index),
                    record_path=self._record_path_for_hand(self._hand_index),
                    server_info=self._server_info,
                    decide_timeout_seconds=self._decide_timeout_seconds,
                    strike_limit=self._strike_limit,
                    event_callback=self._sessions.fanout_event_to_spectators,
                )

                # Check match-end condition before sleeping/rotating.
                next_hand_index = self._hand_index + 1
                if self._max_hands is not None and next_hand_index >= self._max_hands:
                    break

                # Between-hand pause — gives clients time to show a result screen.
                await asyncio.sleep(self._between_hand_pause_seconds)

                # Rotate dealer (simple sequential rotation for now; MCR dealer-
                # repeats-on-win rule is a follow-up when scoring is tracked).
                self._dealer_seat = (self._dealer_seat + 1) % 4
                self._hand_index = next_hand_index

                # Recompute initial state for the new hand.  The snapshot_provider
                # is a bound method over ``self._initial_state``, so updating this
                # field automatically propagates to all SeatSession and Spectator
                # callers — no provider re-registration needed.
                self._initial_state = initial_state(
                    self._ruleset,
                    seed=self._seed + self._hand_index,
                    dealer_seat=self._dealer_seat,
                    hand_index=self._hand_index,
                )

                # Issue DETACH(hand_ended) + ATTACHED(new hand) to all seats.
                await self._sessions.begin_next_hand()
        finally:
            self._match_done.set()


__all__ = ["HUMAN_SEAT", "IdentityFactory", "WebOrchestrator"]
