"""WebSocket transport for the mahjong wire protocol.

Spec: docs/specs/wire-protocol.md § Transport.

Wraps the `websockets` library (v16+, `websockets.asyncio.server`) and
surfaces a small, mahjong-shaped API:

- `WebSocketServer` binds a host:port, enforces the `mahjong-v1` subprotocol,
  serves HTTP routes (`/health`, plus optional `/` and `/static/<path>` for the
  web client per `tui-client.md`) on the same listener, and dispatches each
  accepted WebSocket to a caller-supplied handler.
- `Connection` is the per-client object the handler receives. It is an async
  iterator of decoded wire-message dicts, with a `send()` for outbound and a
  `close()` for explicit teardown. The codec sits inside `recv()` so the
  handler never sees raw frames.

What lives elsewhere:

- *Privacy projection* — applied by the caller (session-mux in Step 7.3)
  before `send()`. The transport is dumb.
- *Per-message validation* — only the envelope (`kind` in `KNOWN_KINDS`) is
  checked here, via the codec.
- */health body* — composed by `server-lifecycle.md`'s health module (Step
  8.5). This file only exposes the `health_handler` hook.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from mahjong.wire import codec
from mahjong.wire.errors import WireError

SUBPROTOCOL: str = "mahjong-v1"
DEFAULT_MAX_SIZE: int = 16 * 1024  # 16 KiB, per wire-protocol §Rate limiting.

HealthHandler = Callable[[], "tuple[int, bytes]"]
# Receives the request's Authorization header value (or None) and returns
# (status, body).  The token check lives in the handler, not the transport
# (admin-console.md § 1); the transport stays dumb.
AdminStatusHandler = Callable[["str | None"], "tuple[int, bytes]"]
ConnectionHandler = Callable[["Connection"], Awaitable[None]]

# Static-asset content-type map. Extensions not in this map are served as
# application/octet-stream. Kept small on purpose; tui-client.md §Server-side
# static asset serving documents the v1 set.
_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

_logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_UNKNOWN_IP = "unknown"


def resolve_client_ip(
    *,
    peer_host: str | None,
    forwarded_for: str | None,
    trust_proxy: bool,
) -> str:
    """Resolve the real client IP for rate-limiting (public-deployment.md § 24.1).

    Trust the proxy's ``CF-Connecting-IP`` header **only** when ``trust_proxy``
    is on AND the TCP peer is loopback — i.e. the request actually came through
    the local ``cloudflared``. A direct (non-tunnel) connection from a remote
    peer can't set this header to anything we'll honour, and it can't even reach
    a loopback-bound listener; the loopback-peer check is what makes the header
    unspoofable. With ``trust_proxy`` off we always return the peer, preserving
    the local/LAN/Tailscale and test behaviour.
    """
    if trust_proxy and peer_host in _LOOPBACK_HOSTS and forwarded_for:
        return forwarded_for.strip()
    return peer_host or _UNKNOWN_IP


class Connection:
    """One accepted WebSocket, decoded as wire-protocol messages.

    Owned by the handler `WebSocketServer` dispatches to. Use as an async
    iterator to consume inbound frames; call `send()` to push outbound.
    """

    def __init__(
        self,
        connection_id: int,
        ws: ServerConnection,
        *,
        client_ip: str = _UNKNOWN_IP,
    ) -> None:
        self.connection_id = connection_id
        self._ws = ws
        # Real client IP (proxy-aware; see resolve_client_ip). Consumed by the
        # rate limiter; loopback/"unknown" for direct local connections.
        self.client_ip = client_ip

    @property
    def subprotocol(self) -> str:
        return self._ws.subprotocol or ""

    @property
    def remote_address(self) -> tuple[Any, ...] | None:
        addr: tuple[Any, ...] | None = self._ws.remote_address
        return addr

    async def send(self, msg: dict[str, Any]) -> None:
        """Encode and send a wire message as a text frame."""
        await self._ws.send(codec.encode(msg).decode("utf-8"))

    async def recv(self) -> dict[str, Any]:
        """Receive one decoded wire message.

        Raises `WireError` (and closes the connection with the documented
        code) on a binary frame or a framing-level decode error.
        """
        raw = await self._ws.recv()
        if isinstance(raw, bytes):
            await self._ws.close(code=1003, reason="binary frame")
            raise WireError("binary frame received on text-only protocol")
        return codec.decode(raw.encode("utf-8"))

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            try:
                yield await self.recv()
            except ConnectionClosed:
                return
            except WireError:
                # The connection has already been closed by `recv()` with the
                # appropriate code; just unwind iteration.
                return

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._ws.close(code=code, reason=reason)

    @property
    def closed(self) -> bool:
        return self._ws.state.name == "CLOSED"


class WebSocketServer:
    """A `mahjong-v1` WebSocket listener with a `/health` HTTP route.

    Lifecycle:
    - `start()` binds and begins accepting.
    - `stop_accepting()` closes the listener but leaves existing connections
      alive (the drain phase in `server-lifecycle.md`).
    - `close()` closes the listener and all attached connections.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        handler: ConnectionHandler,
        health_handler: HealthHandler | None = None,
        admin_status_handler: AdminStatusHandler | None = None,
        static_dir: Path | None = None,
        max_size: int = DEFAULT_MAX_SIZE,
        subprotocol: str = SUBPROTOCOL,
        trust_proxy: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._handler = handler
        self._health_handler = health_handler
        self._admin_status_handler = admin_status_handler
        self._trust_proxy = trust_proxy
        # Resolve eagerly so the traversal check in `_serve_static` can compare
        # against a canonical path.
        self._static_dir: Path | None = static_dir.resolve() if static_dir else None
        self._max_size = max_size
        self._subprotocol = subprotocol
        self._server: Server | None = None
        self._next_conn_id = 0

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("WebSocketServer is already started")
        self._server = await serve(
            self._ws_handler,
            self._host,
            self._port,
            subprotocols=[self._subprotocol],  # type: ignore[list-item]
            process_request=self._process_request,
            max_size=self._max_size,
            # The wire-protocol uses an application-level HEARTBEAT; rely on
            # the library's pings as a second line of defence. Default values
            # are fine for v1.
        )

    @property
    def port(self) -> int:
        """The bound TCP port. Only meaningful after `start()`."""
        if self._server is None:
            return self._port
        sockets = self._server.sockets
        if not sockets:
            raise RuntimeError("server has no bound sockets")
        port: int = sockets[0].getsockname()[1]
        return port

    async def stop_accepting(self) -> None:
        """Close the listener; leave existing connections alive.

        This does NOT block on existing connections — that's `close()`'s job.
        `Server.close(close_connections=False)` is fire-and-forget at this
        layer; the listener stops accepting immediately.
        """
        if self._server is None:
            return
        self._server.close(close_connections=False)

    async def close(self) -> None:
        """Close the listener and all attached connections."""
        if self._server is None:
            return
        self._server.close(close_connections=True)
        await self._server.wait_closed()
        self._server = None

    # --- internals ---

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        # Short-circuit HTTP-only routes so they don't attempt the WS upgrade.
        path = request.path.split("?", 1)[0]
        if path == "/health":
            if self._health_handler is None:
                return connection.respond(503, "service unhealthy or unconfigured\n")
            status, body = self._health_handler()
            return connection.respond(status, body.decode("utf-8", errors="replace"))
        if path == "/admin/status":
            # Absent handler → route not mounted (404), so a hand-started server
            # exposes no admin surface at all (admin-console.md § 1).
            if self._admin_status_handler is None:
                return connection.respond(404, "not found\n")
            authorization = request.headers.get("Authorization")
            status, body = self._admin_status_handler(authorization)
            return connection.respond(status, body.decode("utf-8", errors="replace"))
        # Static assets served at `/` and `/static/<path>` when a static dir
        # is configured. A WS upgrade attempt carries a `Sec-WebSocket-Protocol`
        # header; plain browser GETs do not, so we only try the static path
        # when the subprotocol header is absent.
        offered = request.headers.get_all("Sec-WebSocket-Protocol")
        if not offered and self._static_dir is not None:
            static_response = self._serve_static(connection, path)
            if static_response is not None:
                return static_response
        # Reject the WS upgrade unless `mahjong-v1` was offered. This is the
        # single enforcement point for the subprotocol; the library's own
        # negotiation is permissive (it accepts handshakes without a matching
        # subprotocol), which is not what the spec wants.
        if not offered or self._subprotocol not in _flatten_subprotocols(offered):
            return connection.respond(400, "subprotocol mahjong-v1 required\n")
        return None  # proceed with the WS upgrade

    def _serve_static(self, connection: ServerConnection, path: str) -> Response | None:
        assert self._static_dir is not None
        if path == "/":
            target = self._static_dir / "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/") :]
            # `resolve` collapses `..`; the `is_relative_to` check then rejects
            # anything that escaped the static root.
            target = (self._static_dir / rel).resolve()
            if not target.is_relative_to(self._static_dir):
                return connection.respond(404, "not found\n")
        else:
            return None  # not a static path; let WS-upgrade path handle it
        if not target.is_file():
            return connection.respond(404, "not found\n")
        body = target.read_bytes()
        ctype = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        headers = Headers()
        headers["Content-Type"] = ctype
        headers["Content-Length"] = str(len(body))
        return Response(200, "OK", headers, body)

    def _resolve_peer_ip(self, ws: ServerConnection) -> str:
        """Extract the real client IP from the handshake (proxy-aware)."""
        peer = ws.remote_address
        peer_host = peer[0] if peer else None
        forwarded: str | None = None
        request = getattr(ws, "request", None)
        if request is not None:
            # Header lookup is case-insensitive; Cloudflare sends CF-Connecting-IP.
            forwarded = request.headers.get("CF-Connecting-IP")
        return resolve_client_ip(
            peer_host=peer_host,
            forwarded_for=forwarded,
            trust_proxy=self._trust_proxy,
        )

    async def _ws_handler(self, ws: ServerConnection) -> None:
        # Defense-in-depth: the library should have rejected mismatches via
        # `_select_subprotocol`, but if it didn't, close immediately.
        if ws.subprotocol != self._subprotocol:
            await ws.close(code=1002, reason="subprotocol")
            return
        conn_id = self._next_conn_id
        self._next_conn_id += 1
        conn = Connection(
            conn_id, ws, client_ip=self._resolve_peer_ip(ws)
        )
        try:
            await self._handler(conn)
        except ConnectionClosed:
            pass
        except Exception:
            _logger.exception("connection handler crashed", extra={"conn_id": conn_id})
            with contextlib.suppress(ConnectionClosed):
                await ws.close(code=1011, reason="internal error")


def _flatten_subprotocols(header_values: list[str]) -> list[str]:
    """`Sec-WebSocket-Protocol` may be a comma-separated list per header line."""
    out: list[str] = []
    for v in header_values:
        for part in v.split(","):
            stripped = part.strip()
            if stripped:
                out.append(stripped)
    return out


__all__ = [
    "DEFAULT_MAX_SIZE",
    "SUBPROTOCOL",
    "AdminStatusHandler",
    "Connection",
    "ConnectionHandler",
    "HealthHandler",
    "WebSocketServer",
    "resolve_client_ip",
]
