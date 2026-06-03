"""ControlPlane — the brain of the admin console.

Spec: docs/specs/admin-console.md § "Control-plane WS protocol".

Owns nothing transport-specific: it takes a supervisor, a metrics sampler, and an
async ``admin_status_fetch`` (which pulls the running server's ``/admin/status``),
and turns inbound command frames into a reply frame.  The socket layer
(``AdminWebServer``) is a thin shell that decodes JSON, calls
``handle_command``/``build_status``, and pushes ``STATUS`` on a timer.

Keeping this logic socket-free is what makes the WS message contract unit-testable
without standing up a server — the same separation the game side uses (orchestrator
vs. ``WebSocketServer``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

_logger = logging.getLogger(__name__)

AdminStatusFetch = Callable[[], Awaitable["dict[str, Any] | None"]]

_INVITE_COMMANDS = frozenset({"INVITES_LIST", "INVITE_CREATE", "INVITE_REVOKE"})
_ACCOUNT_COMMANDS = frozenset(
    {"ACCOUNTS_LIST", "ACCOUNT_CREATE", "ACCOUNT_SET_DISABLED", "ACCOUNT_SET_ROLE"}
)


class _SupervisorLike(Protocol):
    # Read-only properties so the real ServerSupervisor (which exposes these as
    # @property) satisfies the protocol; plain attributes on fakes satisfy it too.
    @property
    def state(self) -> Any: ...
    @property
    def pid(self) -> int | None: ...
    @property
    def started_at_monotonic(self) -> float | None: ...

    async def start(self) -> bool: ...
    async def stop(self) -> None: ...
    async def restart(self) -> bool: ...


class _MetricsLike(Protocol):
    @property
    def latest(self) -> Any: ...


class _TunnelLike(Protocol):
    def to_wire(self) -> dict[str, Any]: ...
    async def start(self) -> dict[str, Any]: ...
    async def stop(self) -> None: ...


class ControlPlane:
    def __init__(
        self,
        *,
        supervisor: _SupervisorLike,
        metrics: _MetricsLike,
        admin_status_fetch: AdminStatusFetch,
        server_listen_url: str,
        tunnel: _TunnelLike | None = None,
        data: Any | None = None,
        log_buffer: Any | None = None,
        health_monitor: Any | None = None,
        feedback: Any | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._metrics = metrics
        self._admin_status_fetch = admin_status_fetch
        self._server_listen_url = server_listen_url
        self._tunnel = tunnel
        # AdminDataService (invites/accounts).  Optional so socket-only tests can
        # omit it; commands that need it reply with ERROR when it's absent.
        self._data = data
        # LogRingBuffer of the supervised server's output (optional).
        self._log_buffer = log_buffer
        # HealthMonitor (DB integrity + storage), optional.
        self._health_monitor = health_monitor
        # FeedbackInbox (reads data_dir/reports/*.txt), optional.
        self._feedback = feedback

    # --- log access (consumed by AdminWebServer's per-client LOG stream) ---

    def log_recent(self, limit: int = 200) -> tuple[list[dict[str, Any]], int]:
        """Backlog: the last *limit* retained lines + the current cursor."""
        if self._log_buffer is None:
            return [], 0
        lines = self._log_buffer.recent(limit=limit)
        return [ln.to_wire() for ln in lines], self._log_buffer.last_line

    def log_since(self, cursor: int) -> tuple[list[dict[str, Any]], int]:
        """New lines with ``line > cursor`` + the advanced cursor."""
        if self._log_buffer is None:
            return [], cursor
        lines = self._log_buffer.since(cursor)
        return [ln.to_wire() for ln in lines], self._log_buffer.last_line

    # --- command dispatch ---

    async def handle_command(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Route one inbound frame; return the reply frame.

        SERVER_* commands act on the supervisor and reply with a fresh STATUS.
        Unknown kinds reply with an ERROR frame (never raise — a bad frame must
        not drop the admin socket)."""
        kind = msg.get("kind")
        if kind == "SERVER_START":
            await self._supervisor.start()
            return await self.build_status()
        if kind == "SERVER_STOP":
            await self._supervisor.stop()
            return await self.build_status()
        if kind == "SERVER_RESTART":
            await self._supervisor.restart()
            return await self.build_status()
        if kind == "TUNNEL_START":
            if self._tunnel is not None:
                await self._tunnel.start()
            return await self.build_status()
        if kind == "TUNNEL_STOP":
            if self._tunnel is not None:
                await self._tunnel.stop()
            return await self.build_status()
        if kind in _INVITE_COMMANDS or kind in _ACCOUNT_COMMANDS:
            return await self._handle_data_command(kind, msg)
        if kind == "FEEDBACK_LIST":
            if self._feedback is None:
                return {"kind": "FEEDBACK_LIST", "reports": []}
            return {"kind": "FEEDBACK_LIST", "reports": await self._feedback.list_reports()}
        return {
            "kind": "ERROR",
            "code": "unknown_command",
            "message": f"unknown command: {kind!r}",
        }

    async def _handle_data_command(
        self, kind: str, msg: dict[str, Any]
    ) -> dict[str, Any]:
        """Invite/account commands.  Each replies with a refreshed list frame."""
        if self._data is None:
            return {"kind": "ERROR", "code": "data_unavailable"}
        try:
            if kind == "INVITES_LIST":
                return {"kind": "INVITE_LIST", "invites": await self._data.list_invites()}
            if kind == "INVITE_CREATE":
                invites = await self._data.create_invite(
                    max_uses=int(msg.get("max_uses", 1)),
                    expires_days=int(msg.get("expires_days", 7)),
                )
                return {"kind": "INVITE_LIST", "invites": invites}
            if kind == "INVITE_REVOKE":
                invites = await self._data.revoke_invite(str(msg.get("code", "")))
                return {"kind": "INVITE_LIST", "invites": invites}
            if kind == "ACCOUNTS_LIST":
                return {"kind": "ACCOUNT_LIST", "accounts": await self._data.list_accounts()}
            if kind == "ACCOUNT_CREATE":
                accounts = await self._data.create_account(
                    username=str(msg.get("username", "")),
                    display_name=str(msg.get("display", "")),
                    password=str(msg.get("password", "")),
                    admin=bool(msg.get("admin", False)),
                )
                return {"kind": "ACCOUNT_LIST", "accounts": accounts}
            if kind == "ACCOUNT_SET_DISABLED":
                accounts = await self._data.set_account_disabled(
                    int(msg.get("account_id", -1)), bool(msg.get("disabled", False))
                )
                return {"kind": "ACCOUNT_LIST", "accounts": accounts}
            if kind == "ACCOUNT_SET_ROLE":
                accounts = await self._data.set_account_role(
                    int(msg.get("account_id", -1)), str(msg.get("role", "user"))
                )
                return {"kind": "ACCOUNT_LIST", "accounts": accounts}
        except Exception as exc:  # surface DB/validation errors without dropping the socket
            return {"kind": "ERROR", "code": "data_error", "message": str(exc)}
        return {"kind": "ERROR", "code": "unknown_command"}

    # --- status aggregation ---

    async def build_status(self) -> dict[str, Any]:
        """Merge supervisor + metrics + (live) admin-status + tunnel into STATUS."""
        state = self._supervisor.state
        state_name = getattr(state, "value", str(state))
        running = state_name == "RUNNING"

        admin: dict[str, Any] | None = None
        if running:
            try:
                admin = await self._admin_status_fetch()
            except Exception:  # the server may have just died; treat as unreachable
                _logger.debug("control.admin_status_fetch_failed", exc_info=True)
                admin = None

        latest = self._metrics.latest
        uptime = self._uptime_s()

        server = {
            "state": state_name,
            "pid": self._supervisor.pid,
            "uptime_s": uptime,
            "listen_url": self._server_listen_url,
            "cpu_pct": latest.cpu_pct if latest is not None else None,
            "mem_rss_bytes": latest.mem_rss_bytes if latest is not None else None,
            "players_connected": int(admin["players_connected"]) if admin else 0,
            "tables": list(admin["tables"]) if admin else [],
        }
        tunnel = self._tunnel.to_wire() if self._tunnel is not None else {
            "running": False,
            "url": None,
        }
        health: dict[str, Any] = {"admin_status_ok": admin is not None}
        if self._health_monitor is not None:
            try:
                health.update(await self._health_monitor.snapshot())
            except Exception:
                _logger.debug("control.health_snapshot_failed", exc_info=True)
        return {
            "kind": "STATUS",
            "server": server,
            "tunnel": tunnel,
            "health": health,
        }

    def _uptime_s(self) -> int | None:
        started = self._supervisor.started_at_monotonic
        if started is None:
            return None
        return int(time.monotonic() - started)


__all__ = ["AdminStatusFetch", "ControlPlane"]
