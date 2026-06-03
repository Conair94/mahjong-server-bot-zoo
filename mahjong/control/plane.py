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


class ControlPlane:
    def __init__(
        self,
        *,
        supervisor: _SupervisorLike,
        metrics: _MetricsLike,
        admin_status_fetch: AdminStatusFetch,
        server_listen_url: str,
        tunnel: _TunnelLike | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._metrics = metrics
        self._admin_status_fetch = admin_status_fetch
        self._server_listen_url = server_listen_url
        self._tunnel = tunnel

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
        return {
            "kind": "ERROR",
            "code": "unknown_command",
            "message": f"unknown command: {kind!r}",
        }

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
        return {
            "kind": "STATUS",
            "server": server,
            "tunnel": tunnel,
            "health": {"admin_status_ok": admin is not None},
        }

    def _uptime_s(self) -> int | None:
        started = self._supervisor.started_at_monotonic
        if started is None:
            return None
        return int(time.monotonic() - started)


__all__ = ["AdminStatusFetch", "ControlPlane"]
