"""Multi-table WebSocket orchestrator.

Spec: docs/specs/server-lifecycle.md § Table registry, § Process entry point.
      docs/specs/wire-protocol.md § Server-administrative.

``MultiTableOrchestrator`` hosts a single ``WebSocketServer`` and a
``TableRegistry``.  The handler loop accepts administrative messages
(``LIST_TABLES``, ``CREATE_TABLE``, ``CLOSE_TABLE``) before routing the
connection into a seat-attach or spectator flow.

Wire flow per connection:
    HELLO sent by server
    Client sends zero or more admin messages (LIST_TABLES / CREATE_TABLE)
    Client sends ATTACH or SPECTATE  ←  transitions to inbound loop
    [inbound loop until socket closes]
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.types import RuleSetRef
from mahjong.server.registry import (
    ShuttingDown,
    TableHandle,
    TableNotFound,
    TableRegistry,
)
from mahjong.sessions.mux import DEFAULT_HOLD_SECONDS
from mahjong.wire.server import Connection, WebSocketServer

_logger = logging.getLogger(__name__)

SERVER_ID: str = "mahjong-server-web"
FIRST_FRAME_TIMEOUT_S: float = 5.0
ADMIN_FRAME_TIMEOUT_S: float = 30.0  # timeout for each pre-attach admin message


IdentityFactory = Callable[[Connection], HumanIdentity]
AdminPredicate = Callable[[Connection], bool]


def _default_identity_factory(conn: Connection) -> HumanIdentity:
    suffix = str(conn.connection_id)
    return {"kind": "human", "user_id": f"u_{suffix}", "display": f"player-{suffix}"}


def _default_admin_predicate(conn: Connection) -> bool:
    """All connections are admin in S2 (before auth is wired in Step 8.5)."""
    return True


class MultiTableOrchestrator:
    """One ``WebSocketServer`` + N tables via ``TableRegistry``.

    Clients may:
    - Send ``LIST_TABLES`` to enumerate live tables (response: ``TABLE_LIST``).
    - Send ``CREATE_TABLE`` to allocate a new table (response: ``TABLE_CREATED``).
    - Send ``CLOSE_TABLE`` (admin-only) to close a table.
    - Send ``ATTACH {table_id, seat}`` to join a seat (response: ``ATTACHED``).
    - Send ``SPECTATE {table_id}`` to watch a table (response: ``SPECTATING``).

    *admin_predicate* — callable that returns True if the connection has admin
    privileges.  Defaults to ``lambda conn: True`` (all admins in S2).  Step 8.5
    will replace this with an auth-token check.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        data_dir: Path,
        ruleset: RuleSetRef,
        seed: int,
        server_info: dict[str, Any],
        static_dir: Path | None = None,
        identity_factory: IdentityFactory | None = None,
        admin_predicate: AdminPredicate | None = None,
        decide_timeout_seconds: float = 30.0,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        strike_limit: int = 3,
        max_hands: int | None = 1,
        between_hand_pause_seconds: float = 2.0,
    ) -> None:
        self._host = host
        self._port = port
        self._data_dir = data_dir
        self._ruleset = ruleset
        self._seed = seed
        self._server_info = server_info
        self._static_dir = static_dir
        self._identity_factory = identity_factory or _default_identity_factory
        self._admin_predicate = admin_predicate or _default_admin_predicate
        self._decide_timeout_seconds = decide_timeout_seconds
        self._hold_seconds = hold_seconds
        self._strike_limit = strike_limit
        self._max_hands = max_hands
        self._between_hand_pause_seconds = between_hand_pause_seconds

        self._registry: TableRegistry = TableRegistry()
        self._ws_server: WebSocketServer | None = None
        self._hello_seq: int = 1

    # --- public properties ---

    @property
    def registry(self) -> TableRegistry:
        return self._registry

    @property
    def port(self) -> int:
        if self._ws_server is None:
            return self._port
        return self._ws_server.port

    # --- lifecycle ---

    async def start(self) -> None:
        if self._ws_server is not None:
            raise RuntimeError("MultiTableOrchestrator already started")
        self._ws_server = WebSocketServer(
            host=self._host,
            port=self._port,
            handler=self._handler,
            static_dir=self._static_dir,
        )
        await self._ws_server.start()

    async def close(self) -> None:
        # Close all tables gracefully
        for table_id in list(self._registry._tables):
            with contextlib.suppress(Exception):
                await self._registry.close_table(table_id, reason="server_shutdown")
        if self._ws_server is not None:
            await self._ws_server.close()
            self._ws_server = None

    # --- WS handler ---

    async def _handler(self, conn: Connection) -> None:
        """Per-connection handler.

        Phase 1 (pre-attach): loop reading admin / discovery messages.
        Phase 2 (attached):   forward inbound to the table's SessionMux.
        """
        await self._send_hello(conn)

        # Phase 1 — admin / discovery loop
        table: TableHandle | None = None
        try:
            timeout = FIRST_FRAME_TIMEOUT_S
            while True:
                try:
                    msg = await asyncio.wait_for(conn.recv(), timeout=timeout)
                except TimeoutError:
                    return
                except Exception:
                    return

                kind = msg.get("kind")
                timeout = ADMIN_FRAME_TIMEOUT_S  # relax after first message

                if kind == "LIST_TABLES":
                    await self._handle_list_tables(conn)

                elif kind == "CREATE_TABLE":
                    ok = await self._handle_create_table(conn, msg)
                    if not ok:
                        # Error sent inside; continue so client can retry or disconnect
                        pass

                elif kind == "CLOSE_TABLE":
                    await self._handle_close_table(conn, msg)

                elif kind == "ATTACH":
                    table = await self._handle_attach(conn, msg)
                    if table is None:
                        return
                    break  # enter inbound loop

                elif kind == "SPECTATE":
                    table = await self._handle_spectate(conn, msg)
                    if table is None:
                        return
                    break  # enter inbound loop

                else:
                    with contextlib.suppress(Exception):
                        await conn.send({"kind": "ERROR", "code": "unexpected_kind"})
                    return
        except Exception:
            return

        # Phase 2 — inbound loop for attached / spectating connection
        if table is not None:
            try:
                async for msg in conn:
                    await table.handle_inbound(conn, msg)
            finally:
                await table.on_socket_dropped(conn)

    # --- admin handlers ---

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

    def _make_seq(self) -> int:
        self._hello_seq += 1
        return self._hello_seq - 1

    async def _handle_list_tables(self, conn: Connection) -> None:
        summaries = self._registry.list_tables()
        await conn.send(
            {
                "kind": "TABLE_LIST",
                "seq": self._make_seq(),
                "tables": [s.to_wire() for s in summaries],
            }
        )

    async def _handle_create_table(
        self, conn: Connection, msg: dict[str, Any]
    ) -> bool:
        """Handle CREATE_TABLE.  Returns True if a table was created, False on error."""
        if not self._registry.accepting_new:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "shutting_down"})
            return False

        try:
            table_id = self._registry.create_table_direct(
                ruleset=self._ruleset,
                seed=self._seed,
                server_info=self._server_info,
                data_dir=self._data_dir,
                decide_timeout_seconds=self._decide_timeout_seconds,
                hold_seconds=self._hold_seconds,
                strike_limit=self._strike_limit,
                max_hands=self._max_hands,
                between_hand_pause_seconds=self._between_hand_pause_seconds,
            )
        except ShuttingDown:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "shutting_down"})
            return False
        except Exception as exc:
            _logger.exception("create_table.failed", exc_info=exc)
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "internal_error"})
            return False

        await conn.send(
            {
                "kind": "TABLE_CREATED",
                "seq": self._make_seq(),
                "table_id": int(table_id),
            }
        )
        return True

    async def _handle_close_table(
        self, conn: Connection, msg: dict[str, Any]
    ) -> None:
        """Handle CLOSE_TABLE.  Admin-only."""
        if not self._admin_predicate(conn):
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "not_authorized"})
            return

        raw_id = msg.get("table_id")
        table_id = str(raw_id) if raw_id is not None else ""
        try:
            await self._registry.close_table(table_id, reason="table_closed")
        except TableNotFound:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "table_unknown"})
            return
        except Exception as exc:
            _logger.exception("close_table.failed", exc_info=exc)
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "internal_error"})

    # --- attach / spectate ---

    async def _handle_attach(
        self, conn: Connection, msg: dict[str, Any]
    ) -> TableHandle | None:
        """Route ATTACH to the correct table.  Returns the handle if ok, else None."""
        raw_id = msg.get("table_id")
        table_id = str(raw_id) if raw_id is not None else ""
        seat = msg.get("seat")

        try:
            handle = self._registry.get_table(table_id)
        except TableNotFound:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "table_unknown"})
            return None

        identity = self._identity_factory(conn)
        ok = await handle.attach(conn, identity=identity, seat=seat)  # type: ignore[arg-type]
        if not ok:
            return None
        return handle

    async def _handle_spectate(
        self, conn: Connection, msg: dict[str, Any]
    ) -> TableHandle | None:
        """Route SPECTATE to the correct table."""
        raw_id = msg.get("table_id")
        table_id = str(raw_id) if raw_id is not None else ""

        try:
            handle = self._registry.get_table(table_id)
        except TableNotFound:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "table_unknown"})
            return None

        user_id = f"spec_{conn.connection_id}"
        ok = await handle.spectate(conn, user_id=user_id)
        if not ok:
            return None
        return handle


__all__ = ["MultiTableOrchestrator"]
