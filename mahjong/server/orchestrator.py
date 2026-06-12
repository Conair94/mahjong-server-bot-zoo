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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.types import RuleSetRef
from mahjong.persistence import Persistence
from mahjong.persistence.auth import (
    AuthResult,
    RegisterError,
    handle_auth_request,
    handle_register,
    handle_resume,
)
from mahjong.records.reader import RecordCorruptError, read_record
from mahjong.records.replay_stream import (
    initial_snapshot_for_seat,
    projected_events_for_seat,
)
from mahjong.server.admin_status import make_admin_status_handler
from mahjong.server.health import HealthHandler, make_health_handler
from mahjong.server.ratelimit import SlidingWindowLimiter
from mahjong.server.registry import (
    ShuttingDown,
    TableHandle,
    TableNotFound,
    TableRegistry,
)
from mahjong.server.seat_bots import available_bots_wire
from mahjong.server.seats import SeatsParseError, parse_seats_from_wire
from mahjong.server.table_options import TableOptionsError, parse_table_options
from mahjong.sessions.mux import DEFAULT_HOLD_SECONDS
from mahjong.table.manager import DecideTimeouts
from mahjong.wire.feedback import SanitiseError, sanitise_report_text
from mahjong.wire.server import AdminStatusHandler, Connection, WebSocketServer

_logger = logging.getLogger(__name__)

SERVER_ID: str = "mahjong-server-web"
FIRST_FRAME_TIMEOUT_S: float = 30.0  # generous: a human picks credentials
ADMIN_FRAME_TIMEOUT_S: float = 30.0  # timeout for each pre-attach admin message

# IP-keyed rate-limit budgets (public-deployment.md § 24.3). One-hour windows.
_RATE_WINDOW_S: float = 3600.0
_LOGIN_MAX_FAILURES_PER_HOUR: int = 10  # failed AUTH_REQUESTs per IP
_REGISTER_MAX_PER_HOUR: int = 5  # REGISTER attempts per IP
_FEEDBACK_MAX_PER_HOUR: int = 5  # FEEDBACK submissions per IP


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
        decide_timeouts: DecideTimeouts | None = None,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        strike_limit: int = 3,
        max_hands: int | None = 1,
        between_hand_pause_seconds: float = 2.0,
        persistence: Persistence | None = None,
        require_auth: bool | None = None,
        bot_pacing_enabled: bool = False,
        bot_min_delay_s: float = 5.0,
        bot_max_delay_s: float = 10.0,
        trust_proxy: bool = False,
        admin_token: str | None = None,
        shutdown_timeout_s: float = 30.0,
    ) -> None:
        self._host = host
        self._port = port
        self._trust_proxy = trust_proxy
        self._admin_token = admin_token
        self._shutdown_timeout_s = shutdown_timeout_s
        # Set at start(); monotonic so uptime is immune to wall-clock changes.
        self._started_at_monotonic: float | None = None
        self._data_dir = data_dir
        self._ruleset = ruleset
        self._seed = seed
        self._server_info = server_info
        self._static_dir = static_dir
        self._identity_factory = identity_factory or _default_identity_factory
        self._admin_predicate = admin_predicate or _default_admin_predicate
        self._decide_timeout_seconds = decide_timeout_seconds
        self._decide_timeouts = decide_timeouts
        self._hold_seconds = hold_seconds
        self._strike_limit = strike_limit
        self._max_hands = max_hands
        self._between_hand_pause_seconds = between_hand_pause_seconds
        self._persistence = persistence
        self._bot_pacing_enabled = bot_pacing_enabled
        self._bot_min_delay_s = bot_min_delay_s
        self._bot_max_delay_s = bot_max_delay_s
        # auth_required defaults to: only when a persistence is supplied (so
        # existing tests without persistence keep their no-auth path).
        # Callers may force it on/off explicitly via require_auth.
        self._auth_required = require_auth if require_auth is not None else persistence is not None
        self._auth_state = _AuthState()

        # IP-keyed abuse limiters (public-deployment.md § 24.3). In-process; the
        # window resets on restart, which is fine at home scale.
        self._login_limiter = SlidingWindowLimiter(
            max_events=_LOGIN_MAX_FAILURES_PER_HOUR, window_s=_RATE_WINDOW_S
        )
        self._register_limiter = SlidingWindowLimiter(
            max_events=_REGISTER_MAX_PER_HOUR, window_s=_RATE_WINDOW_S
        )
        self._feedback_limiter = SlidingWindowLimiter(
            max_events=_FEEDBACK_MAX_PER_HOUR, window_s=_RATE_WINDOW_S
        )

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
        (self._data_dir / "reports").mkdir(parents=True, exist_ok=True)
        self._started_at_monotonic = time.monotonic()
        self._ws_server = WebSocketServer(
            host=self._host,
            port=self._port,
            handler=self._handler,
            health_handler=self._build_health_handler(),
            admin_status_handler=self._build_admin_status_handler(),
            static_dir=self._static_dir,
            trust_proxy=self._trust_proxy,
        )
        await self._ws_server.start()

    def _build_health_handler(self) -> HealthHandler | None:
        """The unauthenticated /health handler, or None when no persistence is
        configured (test orchestrators without a DB leave the route at 503)."""
        if self._persistence is None:
            return None
        assert self._started_at_monotonic is not None
        return make_health_handler(
            registry=self._registry,
            persistence=self._persistence,
            started_at_monotonic=self._started_at_monotonic,
            server_id=str(self._server_info.get("server_id", SERVER_ID)),
            shutdown_timeout_s=self._shutdown_timeout_s,
        )

    def _build_admin_status_handler(self) -> AdminStatusHandler | None:
        """The token-gated /admin/status handler, or None when no token is set
        (route stays unmounted — admin-console.md § 1)."""
        if not self._admin_token:
            return None
        assert self._started_at_monotonic is not None
        return make_admin_status_handler(
            token=self._admin_token,
            registry=self._registry,
            started_at_monotonic=self._started_at_monotonic,
            listen_addr=f"{self._host}:{self.port}",
        )

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

        Phases 1↔2 cycle (FB-14): leaving a table — a seated client's DETACH,
        or a spectator's STOP_SPECTATING — returns the connection to the
        Phase 1 lobby loop instead of requiring a socket drop, so a player can
        always get back to the menu (even out of a hung hand: this dispatch
        runs in the connection read loop, independent of the hand task).
        """
        await self._send_hello(conn)

        # Phase 0 — authentication (only when persistence is configured).
        if self._auth_required:
            authed = await self._run_auth_phase(conn)
            if not authed:
                return

        timeout = FIRST_FRAME_TIMEOUT_S
        while True:
            # Phase 1 — admin / discovery loop
            table: TableHandle | None = None
            # The only kind that legitimately ends Phase 2 for this role: a
            # seat leaves with DETACH, a spectator with STOP_SPECTATING.  The
            # mismatched kind is a no-op in the mux, and breaking out on it
            # would strand a still-subscribed connection in the lobby.
            leave_kind = "DETACH"
            try:
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

                    elif kind == "FEEDBACK":
                        await self._handle_feedback(conn, msg)

                    elif kind == "GET_PROFILE":
                        await self._handle_get_profile(conn)

                    elif kind == "GET_HISTORY":
                        await self._handle_get_history(conn, msg)

                    elif kind == "GET_REPLAY":
                        await self._handle_get_replay(conn, msg)

                    elif kind == "ATTACH":
                        table = await self._handle_attach(conn, msg)
                        if table is None:
                            return
                        leave_kind = "DETACH"
                        break  # enter inbound loop

                    elif kind == "SPECTATE":
                        table = await self._handle_spectate(conn, msg)
                        if table is None:
                            return
                        leave_kind = "STOP_SPECTATING"
                        break  # enter inbound loop

                    else:
                        with contextlib.suppress(Exception):
                            await conn.send({"kind": "ERROR", "code": "unexpected_kind"})
                        return
            except Exception:
                return

            # Phase 2 — inbound loop for attached / spectating connection
            left_table = False
            try:
                async for msg in conn:
                    kind = msg.get("kind")
                    # FEEDBACK is a connection-level concern (it writes to
                    # data_dir/reports), not a table action.  Intercept it here so
                    # the in-game feedback button still works once attached —
                    # otherwise the table session replies ERROR unknown_kind and
                    # the client's feedback modal hangs waiting for an ACK.
                    if kind == "FEEDBACK":
                        await self._handle_feedback(conn, msg)
                        continue
                    # GET_PROFILE / GET_HISTORY / GET_REPLAY are read-only account
                    # & lobby concerns, not table actions, but the profile button
                    # (and the recent-games "watch" links it exposes) are reachable
                    # from the table view.  Intercept them here too — otherwise they
                    # fall through to the table session, which replies
                    # ERROR unknown_kind, and the client hangs on "Loading…" (the
                    # same two-phase trap as FEEDBACK above).  These touch only the
                    # persistence layer and the connection, never table state, so
                    # they are safe to answer mid-hand.
                    if kind == "GET_PROFILE":
                        await self._handle_get_profile(conn)
                        continue
                    if kind == "GET_HISTORY":
                        await self._handle_get_history(conn, msg)
                        continue
                    if kind == "GET_REPLAY":
                        await self._handle_get_replay(conn, msg)
                        continue
                    await table.handle_inbound(conn, msg)
                    if kind == leave_kind:
                        # The mux has acked (DETACHED) and released the seat /
                        # spectator slot; this connection is in the lobby again.
                        left_table = True
                        break
            finally:
                if not left_table:
                    await table.on_socket_dropped(conn)
            if not left_table:
                return
            # Loop back to Phase 1 with the lobby read timeout.

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
                # Rate-limit check BEFORE the argon2 verify: a throttled IP
                # never costs the server a hash (public-deployment.md § 24.3).
                if not self._login_limiter.would_allow(conn.client_ip):
                    with contextlib.suppress(Exception):
                        await conn.send(
                            {
                                "kind": "ERROR",
                                "code": "rate_limited",
                                "message": "Too many sign-in attempts — please wait and try again.",
                            }
                        )
                    attempts += 1
                    continue
                result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    self._run_auth_request,
                    str(msg.get("username") or ""),
                    str(msg.get("password") or ""),
                )
                # Only failed logins consume the budget — a user reconnecting
                # with valid credentials is never penalised.
                if not result.ok:
                    self._login_limiter.record(conn.client_ip)
            elif kind == "RESUME":
                result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    self._run_resume,
                    str(msg.get("session_token") or ""),
                )
            elif kind == "REGISTER":
                # Every register attempt counts toward the budget.
                if not self._register_limiter.allow(conn.client_ip):
                    with contextlib.suppress(Exception):
                        await conn.send(
                            {
                                "kind": "ERROR",
                                "code": "rate_limited",
                                "message": "Too many registration attempts — please wait and try again.",
                            }
                        )
                    attempts += 1
                    continue
                try:
                    result = await asyncio.get_running_loop().run_in_executor(
                        None,
                        self._run_register,
                        msg,
                    )
                except RegisterError as exc:
                    # Rejection: generic for invite problems, specific for a
                    # taken username (public-deployment.md § 24.2). Counts
                    # against the per-connection attempt budget like a failed
                    # AUTH_REQUEST.
                    with contextlib.suppress(Exception):
                        await conn.send(
                            {
                                "kind": "ERROR",
                                "code": "register_rejected",
                                "message": exc.message,
                            }
                        )
                    attempts += 1
                    continue
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
                # Rejoin discovery (reconnect-rejoin.md, FB-03): tell the client
                # which seats this account currently holds so it can re-ATTACH
                # to a HELD seat (or take over a LIVE one) from the lobby.
                seat_holds = [h.to_wire() for h in self._registry.seat_holds_for(result.user_id)]
                auth_ok: dict[str, Any] = {
                    "kind": "AUTH_RESPONSE",
                    "seq": self._make_seq(),
                    "ok": True,
                    "user_id": result.user_id,
                    "display_name": result.display_name,
                    "session_token": result.session_token,
                    "expires_at_ms": result.expires_at_ms,
                }
                if seat_holds:
                    auth_ok["seat_holds"] = seat_holds
                with contextlib.suppress(Exception):
                    await conn.send(auth_ok)
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

    def _run_register(self, msg: dict[str, Any]) -> AuthResult:
        assert self._persistence is not None
        return handle_register(
            self._persistence._conn,
            username=str(msg.get("username") or ""),
            password=str(msg.get("password") or ""),
            display_name=str(msg.get("display_name") or ""),
            invite_code=str(msg.get("invite_code") or ""),
        )

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
            # Selectable in-process bots for the create-table picker. Additive
            # field; the registry is the single source of truth so the menu
            # never drifts from what the server can actually seat.
            "bots": available_bots_wire(),
        }
        features: list[str] = []
        if self._auth_required:
            # Signal to the client that AUTH_REQUEST is expected before any
            # table operations.  Additive field — unknown features are tolerated
            # by clients that haven't been updated (wire-protocol.md § HELLO).
            features.append("auth")
        if self._persistence is not None:
            # The profile home page (GET_PROFILE) needs persisted hand history.
            features.append("profile")
        if features:
            hello["features"] = features
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

        # Per-table creation options (§22.6 Part A) override the server
        # defaults for this table only. Resolve against the server defaults.
        default_timeouts = self._decide_timeouts or DecideTimeouts.uniform(
            self._decide_timeout_seconds
        )
        try:
            opts = parse_table_options(
                msg.get("options"),
                default_pacing_enabled=self._bot_pacing_enabled,
                default_min_delay_s=self._bot_min_delay_s,
                default_max_delay_s=self._bot_max_delay_s,
                default_decide_timeouts=default_timeouts,
            )
        except TableOptionsError as exc:
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
                decide_timeouts=opts.decide_timeouts,
                hold_seconds=self._hold_seconds,
                strike_limit=self._strike_limit,
                max_hands=self._max_hands,
                between_hand_pause_seconds=self._between_hand_pause_seconds,
                seats=seats,
                bot_pacing_enabled=opts.bot_pacing_enabled,
                bot_min_delay_s=opts.bot_min_delay_s,
                bot_max_delay_s=opts.bot_max_delay_s,
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

    # --- feedback ---

    async def _handle_feedback(self, conn: Connection, msg: dict[str, Any]) -> None:
        """Handle FEEDBACK: sanitise, write to data_dir/reports/, send FEEDBACK_ACK."""
        # Reinstated public-exposure limit (public-deployment.md § 24.4). Uses
        # the `feedback_error` code so the existing feedback modal surfaces it.
        if not self._feedback_limiter.allow(conn.client_ip):
            with contextlib.suppress(Exception):
                await conn.send(
                    {
                        "kind": "ERROR",
                        "code": "feedback_error",
                        "message": "Too many reports — please try again later.",
                    }
                )
            return

        report_type = msg.get("type")
        if report_type not in ("bug", "feature"):
            with contextlib.suppress(Exception):
                await conn.send(
                    {"kind": "ERROR", "code": "feedback_error", "message": "invalid type"}
                )
            return

        raw_text = msg.get("text")
        if not isinstance(raw_text, str):
            with contextlib.suppress(Exception):
                await conn.send(
                    {"kind": "ERROR", "code": "feedback_error", "message": "text must be a string"}
                )
            return

        try:
            clean_text = sanitise_report_text(raw_text)
        except SanitiseError as exc:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "feedback_error", "message": str(exc)})
            return

        auth = self._auth_state.get(conn)
        submitter = auth["display_name"] if auth else "anonymous"

        reports_dir = self._data_dir / "reports"
        await asyncio.get_running_loop().run_in_executor(
            None, self._write_report, reports_dir, report_type, submitter, clean_text
        )
        with contextlib.suppress(Exception):
            await conn.send({"kind": "FEEDBACK_ACK"})

    @staticmethod
    def _write_report(reports_dir: Path, report_type: str, submitter: str, text: str) -> None:
        import datetime

        now = datetime.datetime.now(datetime.UTC)
        stem = now.strftime("%Y%m%d_%H%M%S") + f"_{report_type}"
        for attempt in range(10):
            suffix = "" if attempt == 0 else f"_{attempt}"
            path = reports_dir / f"{stem}{suffix}.txt"
            if not path.exists():
                break
        header = f"type: {report_type}\nsubmitted: {now.isoformat(timespec='seconds')}\nsubmitter: {submitter}\n---\n"
        path.write_text(header + text, encoding="utf-8")

    # --- profile ---

    async def _handle_get_profile(self, conn: Connection) -> None:
        """Reply with a PROFILE for the connection's authenticated account.

        Profile is a lobby concern, so this lives only in the admin/discovery
        loop (not the in-game inbound loop).  DB reads are synchronous, so they
        are off-loaded to a thread (sync-DB / run_in_executor rule).
        """
        auth = self._auth_state.get(conn)
        if auth is None or self._persistence is None:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "not_authenticated"})
            return

        account_id = int(auth["account_id"])
        try:
            payload = await asyncio.get_running_loop().run_in_executor(
                None, self._build_profile_payload, account_id
            )
        except Exception:
            _logger.exception("profile.build_failed", extra={"account_id": account_id})
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "profile_error"})
            return

        payload["seq"] = self._make_seq()
        with contextlib.suppress(Exception):
            await conn.send(payload)

    def _build_profile_payload(self, account_id: int) -> dict[str, Any]:
        """Synchronous: gather stats + recent history + score series into a
        PROFILE message body (no ``seq`` — the caller stamps it).

        Runs on a worker thread; touches only the persistence layer.
        """
        assert self._persistence is not None
        p = self._persistence
        stats = p.account_stats(account_id)
        recent_hands = p.find_hands_by_account(account_id, limit=20)
        series = p.account_score_series(account_id, limit=200)
        acct = p.get_account_by_id(account_id)

        recent: list[dict[str, Any]] = []
        for h in recent_hands:
            # find_hands_by_account does not populate participants; fetch the
            # full row to locate this account's seat + score delta.
            full = p.get_hand(h.hand_id)
            seat: int | None = None
            delta: int | None = None
            if full is not None:
                for part in full.participants:
                    if part.account_id == account_id:
                        seat = part.seat
                        delta = part.final_score_delta
                        break
            won = h.terminal_kind == "HU" and h.winner_seat == seat
            recent.append(
                {
                    "hand_id": h.hand_id,
                    "match_id": h.match_id,
                    "started_at_ms": h.started_at_ms,
                    "ended_at_ms": h.ended_at_ms,
                    "terminal_kind": h.terminal_kind,
                    "won": won,
                    "score_delta": delta,
                    "fan_total": h.fan_total if won else None,
                    "seat": seat,
                }
            )

        return {
            "kind": "PROFILE",
            "account": {
                "account_id": account_id,
                "username": acct.username if acct else "",
                "display_name": acct.display_name if acct else "",
            },
            "stats": {
                "hands_played": stats.hands_played,
                "hands_won": stats.hands_won,
                "draws": stats.draws,
                "total_score": stats.total_score,
                "total_win_points": stats.total_win_points,
                "best_win_fan": stats.best_win_fan,
                "first_played_ms": stats.first_played_ms,
                "last_played_ms": stats.last_played_ms,
            },
            "recent": recent,
            "series": [
                {"ended_at_ms": pt.ended_at_ms, "cumulative": pt.cumulative} for pt in series
            ],
            # Spec 39: derive-at-read, additive field (old clients ignore it).
            "achievements": p.account_achievements(account_id),
        }

    # --- history + replay (account-records-replay.md, FB-04) -----------------

    async def _handle_get_history(self, conn: Connection, msg: dict[str, Any]) -> None:
        """Paginated "my games" list for the connection's account. Lobby/profile
        concern — admin loop only. DB reads are off-loaded to a thread."""
        auth = self._auth_state.get(conn)
        if auth is None or self._persistence is None:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "not_authenticated"})
            return
        account_id = int(auth["account_id"])
        before = msg.get("before_hand_id")
        before_hand_id = before if isinstance(before, str) else None
        raw_limit = msg.get("limit")
        limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 100 else 50
        try:
            payload = await asyncio.get_running_loop().run_in_executor(
                None, self._build_history_payload, account_id, before_hand_id, limit
            )
        except Exception:
            _logger.exception("history.build_failed", extra={"account_id": account_id})
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "history_error"})
            return
        payload["seq"] = self._make_seq()
        with contextlib.suppress(Exception):
            await conn.send(payload)

    def _build_history_payload(
        self, account_id: int, before_hand_id: str | None, limit: int
    ) -> dict[str, Any]:
        """Synchronous: keyset-paginated history rows for *account_id*."""
        assert self._persistence is not None
        p = self._persistence
        hands = p.find_hands_by_account(account_id, limit=limit, before_hand_id=before_hand_id)
        rows = [self._history_row(account_id, h) for h in hands]
        # A full page implies there may be more; hand the last id back as the
        # keyset cursor. A short page is the end of history.
        next_before = hands[-1].hand_id if len(hands) == limit and hands else None
        return {"kind": "HISTORY", "hands": rows, "next_before_hand_id": next_before}

    def _history_row(self, account_id: int, hand: Any) -> dict[str, Any]:
        """One "my games" row: outcome from this account's seat. ``find_hands_by_account``
        omits participants, so fetch the full row to locate the seat + delta."""
        assert self._persistence is not None
        full = self._persistence.get_hand(hand.hand_id)
        seat: int | None = None
        delta: int | None = None
        if full is not None:
            for part in full.participants:
                if part.account_id == account_id:
                    seat = part.seat
                    delta = part.final_score_delta
                    break
        won = hand.terminal_kind == "HU" and hand.winner_seat == seat
        return {
            "hand_id": hand.hand_id,
            "match_id": hand.match_id,
            "started_at_ms": hand.started_at_ms,
            "ended_at_ms": hand.ended_at_ms,
            "terminal_kind": hand.terminal_kind,
            "won": won,
            "score_delta": delta,
            "fan_total": hand.fan_total if won else None,
            "seat": seat,
        }

    async def _handle_get_replay(self, conn: Connection, msg: dict[str, Any]) -> None:
        """Fetch one finished hand's projected event stream for playback.

        Authorization: a participant replays from their own seat; an admin gets
        the public (seat=None) view; everyone else is refused. The record read +
        projection are off-loaded to a thread."""
        auth = self._auth_state.get(conn)
        if auth is None or self._persistence is None:
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "not_authenticated"})
            return
        hand_id = msg.get("hand_id")
        if not isinstance(hand_id, str):
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "framing"})
            return
        account_id = int(auth["account_id"])
        role = str(auth.get("role") or "user")
        try:
            status, payload = await asyncio.get_running_loop().run_in_executor(
                None, self._build_replay_payload, account_id, role, hand_id
            )
        except Exception:
            _logger.exception("replay.build_failed", extra={"hand_id": hand_id})
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": "replay_unavailable"})
            return
        if status != "ok":
            with contextlib.suppress(Exception):
                await conn.send({"kind": "ERROR", "code": status})
            return
        payload["seq"] = self._make_seq()
        with contextlib.suppress(Exception):
            await conn.send(payload)

    def _build_replay_payload(
        self, account_id: int, role: str, hand_id: str
    ) -> tuple[str, dict[str, Any]]:
        """Synchronous. Returns ``("ok", payload)`` or ``(error_code, {})``.

        error_code ∈ {hand_not_found, not_authorized, replay_unavailable}.
        """
        assert self._persistence is not None
        hand = self._persistence.get_hand(hand_id)
        if hand is None:
            return ("hand_not_found", {})

        # Authorize + pick the viewing seat (participant → own; admin → public).
        view_seat: int | None = None
        is_participant = False
        for part in hand.participants:
            if part.account_id == account_id:
                view_seat = part.seat
                is_participant = True
                break
        if not is_participant:
            if role == "admin":
                view_seat = None  # public projection
            else:
                return ("not_authorized", {})

        try:
            events = read_record(Path(hand.record_path))
        except (RecordCorruptError, OSError):
            return ("replay_unavailable", {})

        snapshot = initial_snapshot_for_seat(events, seat=view_seat)
        proj = projected_events_for_seat(events, seat=view_seat)
        return (
            "ok",
            {
                "kind": "REPLAY",
                "hand_id": hand_id,
                "seat": view_seat if view_seat is not None else -1,
                "snapshot": snapshot,
                "events": proj,
                "meta": {
                    "ruleset_id": hand.ruleset_id,
                    "winner_seat": hand.winner_seat,
                    "fan_total": hand.fan_total,
                },
            },
        )

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
