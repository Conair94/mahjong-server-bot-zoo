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
from mahjong.persistence import Persistence
from mahjong.persistence.auth import (
    AuthResult,
    handle_auth_request,
    handle_resume,
)
from mahjong.server.registry import (
    ShuttingDown,
    TableHandle,
    TableNotFound,
    TableRegistry,
)
from mahjong.server.seats import SeatsParseError, parse_seats_from_wire
from mahjong.sessions.mux import DEFAULT_HOLD_SECONDS
from mahjong.wire.server import Connection, WebSocketServer

_logger = logging.getLogger(__name__)

SERVER_ID: str = "mahjong-server-web"
FIRST_FRAME_TIMEOUT_S: float = 30.0  # generous: a human picks credentials
ADMIN_FRAME_TIMEOUT_S: float = 30.0  # timeout for each pre-attach admin message


IdentityFactory = Callable[[Connection], HumanIdentity]
AdminPredicate = Callable[[Connection], bool]


def _default_identity_factory(conn: Connection) -> HumanIdentity:
    suffix = str(conn.connection_id)
    return {"kind": "human", "user_id": f"u_{suffix}", "display": f"player-{suffix}"}


def _default_admin_predicate(conn: Connection) -> bool:
    """All connections are admin in S2 (before auth is wired in Step 8.5)."""
    return True


# Per-connection identity store — populated by the AUTH phase, consulted by
# admin_predicate and the seat-attach identity factory.  Keyed on connection_id
# rather than the Connection object to avoid lifecycle pitfalls.
class _AuthState:
    """Auth state shared between the auth phase and downstream handlers."""

    __slots__ = ("by_conn_id",)

    def __init__(self) -> None:
        self.by_conn_id: dict[int, dict[str, Any]] = {}

    def set(self, conn: Connection, account_id: int, display_name: str, role: str) -> None:
        self.by_conn_id[conn.connection_id] = {
            "account_id": account_id,
            "display_name": display_name,
            "role": role,
        }

    def get(self, conn: Connection) -> dict[str, Any] | None:
        return self.by_conn_id.get(conn.connection_id)

    def clear(self, conn: Connection) -> None:
        self.by_conn_id.pop(conn.connection_id, None)


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
        persistence: Persistence | None = None,
        require_auth: bool | None = None,
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
        self._persistence = persistence
        # auth_required defaults to: only when a persistence is supplied (so
        # existing tests without persistence keep their no-auth path).
        # Callers may force it on/off explicitly via require_auth.
        self._auth_required = require_auth if require_auth is not None else persistence is not None
        self._auth_state = _AuthState()

        self._registry: TableRegistry = TableRegistry(persistence=persistence)
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

        Phase 0 (auth):       client sends AUTH_REQUEST or RESUME (if required).
        Phase 1 (pre-attach): loop reading admin / discovery messages.
        Phase 2 (attached):   forward inbound to the table's SessionMux.
        """
        await self._send_hello(conn)

        # Phase 0 — authentication (only when persistence is configured).
        if self._auth_required:
            authed = await self._run_auth_phase(conn)
            if not authed:
                return

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

    # --- auth phase ---

    async def _run_auth_phase(self, conn: Connection) -> bool:
        """Block until the client sends a successful AUTH_REQUEST or RESUME.

        Returns True on success (auth_state populated), False on failure or
        timeout (connection should be closed by caller via return).  At most
        three failed attempts are allowed before the server hangs up.
        """
        assert self._persistence is not None
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            try:
                msg = await asyncio.wait_for(conn.recv(), timeout=FIRST_FRAME_TIMEOUT_S)
            except (TimeoutError, Exception):
                return False

            kind = msg.get("kind")
            result: AuthResult | None = None
            if kind == "AUTH_REQUEST":
                result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    self._run_auth_request,
                    str(msg.get("username") or ""),
                    str(msg.get("password") or ""),
                )
            elif kind == "RESUME":
                result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    self._run_resume,
                    str(msg.get("session_token") or ""),
                )
            else:
                with contextlib.suppress(Exception):
                    await conn.send({"kind": "ERROR", "code": "auth_required"})
                attempts += 1
                continue

            if result.ok:
                assert result.user_id is not None
                account_id = int(result.user_id.removeprefix("u_"))
                role = self._lookup_role(account_id)
                self._auth_state.set(
                    conn,
                    account_id=account_id,
                    display_name=result.display_name or "",
                    role=role,
                )
                with contextlib.suppress(Exception):
                    await conn.send(
                        {
                            "kind": "AUTH_RESPONSE",
                            "seq": self._make_seq(),
                            "ok": True,
                            "user_id": result.user_id,
                            "display_name": result.display_name,
                            "session_token": result.session_token,
                            "expires_at_ms": result.expires_at_ms,
                        }
                    )
                return True

            with contextlib.suppress(Exception):
                await conn.send(
                    {
                        "kind": "AUTH_RESPONSE",
                        "seq": self._make_seq(),
                        "ok": False,
                    }
                )
            attempts += 1

        return False

    def _run_auth_request(self, username: str, password: str) -> AuthResult:
        assert self._persistence is not None
        return handle_auth_request(self._persistence._conn, username, password)

    def _run_resume(self, token: str) -> AuthResult:
        assert self._persistence is not None
        return handle_resume(self._persistence._conn, token)

    def _lookup_role(self, account_id: int) -> str:
        assert self._persistence is not None
        acct = self._persistence.get_account_by_id(account_id)
        return acct.role if acct is not None else "user"

    # --- admin handlers ---

    async def _send_hello(self, conn: Connection) -> None:
        hello: dict[str, Any] = {
            "kind": "HELLO",
            "seq": self._hello_seq,
            "protocol_version": 1,
            "server_id": SERVER_ID,
        }
        if self._auth_required:
            # Signal to the client that AUTH_REQUEST is expected before any
            # table operations.  Additive field — unknown features are tolerated
            # by clients that haven't been updated (wire-protocol.md § HELLO).
            hello["features"] = ["auth"]
        await conn.send(hello)
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

    async def _handle_create_table(self, conn: Connection, msg: dict[str, Any]) -> bool:
        """Handle CREATE_TABLE.  Returns True if a table was created, False on error."""
        if not self._registry.accepting_new:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "shutting_down"})
            return False

        try:
            seats = parse_seats_from_wire(msg.get("seats"))
        except SeatsParseError as exc:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "framing", "message": str(exc)})
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
                seats=seats,
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

    async def _handle_close_table(self, conn: Connection, msg: dict[str, Any]) -> None:
        """Handle CLOSE_TABLE.  Admin-only."""
        if not self._is_admin(conn):
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

    async def _handle_attach(self, conn: Connection, msg: dict[str, Any]) -> TableHandle | None:
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

        identity = self._identity_for(conn)
        ok = await handle.attach(conn, identity=identity, seat=seat)  # type: ignore[arg-type]
        if not ok:
            return None
        return handle

    def _identity_for(self, conn: Connection) -> HumanIdentity:
        """Prefer the authenticated identity; fall back to the injected factory."""
        auth = self._auth_state.get(conn)
        if auth is not None:
            return {
                "kind": "human",
                "user_id": f"u_{auth['account_id']}",
                "display": auth["display_name"],
            }
        return self._identity_factory(conn)

    def _is_admin(self, conn: Connection) -> bool:
        """True if the connection is admin-privileged.

        When auth is required, derives from the authenticated account's role.
        Otherwise falls back to the injected admin_predicate.
        """
        auth = self._auth_state.get(conn)
        if auth is not None:
            return bool(auth["role"] == "admin")
        return self._admin_predicate(conn)

    async def _handle_spectate(self, conn: Connection, msg: dict[str, Any]) -> TableHandle | None:
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
