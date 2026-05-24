"""Web orchestrator: production WS+static server that drives one hand.

Spec: docs/specs/tui-client.md fixture 18 (S2 exit gate), CHECKLIST Step 7.6.ii.

v1 scope: one table, four seats. Seat 0 is reserved for a human connecting
over WebSocket; seats 1-3 are `CannedAdapter`s configured at construction
time. After the first successful seat-0 ATTACH, `manager.run_hand` runs
once in a background task; the record is written; the server stays up
until `close()` is called.

Multi-hand orchestration, multiple human seats, multiple tables, and
authentication land in Layer 8. Spectator support lands in 7.6.iv.
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
    """Single-hand WS orchestrator.

    Holds a `WebSocketServer` (transport), a `TableSessions` (per-table
    state), and the `manager.run_hand` background task. The hand kicks off
    the first time a client successfully ATTACHes to the human seat; the
    orchestrator forwards subsequent inbound (ACTION / DETACH / SPECTATE-
    side messages) into `TableSessions.handle_inbound`.
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

        # Pre-build the initial state so ATTACHED carries a correct snapshot
        # before `run_hand` runs. Same seed → byte-identical state inside
        # `run_hand`, so there's no drift between attach-time and hand-time.
        self._initial_state: GameState = initial_state(ruleset, seed=seed)

        # CannedAdapters fill the non-human seats. Empty action lists ⇒ each
        # `decide` returns `prompt.default_action`. The orchestrator owns
        # them; tests inject scripts via `canned_seat_actions`.
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
            hand_index_provider=lambda: 0,
        )
        self._ws_server: WebSocketServer | None = None
        self._hand_task: asyncio.Task[GameState] | None = None
        self._hand_done: asyncio.Event = asyncio.Event()
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
        if timeout is None:
            await self._hand_done.wait()
            return
        await asyncio.wait_for(self._hand_done.wait(), timeout=timeout)

    # --- providers for TableSessions ---

    def _snapshot_provider(self, seat: int | None) -> dict[str, Any]:
        return cast(dict[str, Any], project_state(self._initial_state, seat))

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
                self._hand_task = asyncio.create_task(self._run_hand(identity))
        return True

    async def _handle_spectate(self, conn: Connection) -> bool:
        outcome = await self._sessions.spectate(conn, user_id=f"spec_{conn.connection_id}")
        return outcome.ok

    async def _run_hand(self, human_identity: HumanIdentity) -> GameState:
        try:
            human_session = self._sessions.seat(HUMAN_SEAT)
            human = HumanAdapter(session=human_session, identity=human_identity)
            adapters: list[SeatAdapter] = [
                cast(SeatAdapter, human),
                cast(SeatAdapter, self._canned_adapters[1]),
                cast(SeatAdapter, self._canned_adapters[2]),
                cast(SeatAdapter, self._canned_adapters[3]),
            ]
            return await mgr.run_hand(
                adapters=adapters,
                ruleset=self._ruleset,
                seed=self._seed,
                hand_id=self._hand_id,
                record_path=self._record_path,
                server_info=self._server_info,
                decide_timeout_seconds=self._decide_timeout_seconds,
            )
        finally:
            self._hand_done.set()


__all__ = ["HUMAN_SEAT", "IdentityFactory", "WebOrchestrator"]
