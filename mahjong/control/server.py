"""AdminWebServer — serves the admin UI + the control WS on one listener.

Spec: docs/specs/admin-console.md § 3 (Admin web UI), § "Control-plane WS protocol".

Same single-listener shape as the game server (``mahjong.wire.server``): static
assets over plain HTTP via ``process_request``, and a WebSocket (subprotocol
``mahjong-admin-v1``) carrying JSON command/STATUS/LOG frames.  This is thin glue
over ``ControlPlane`` — all the logic lives there.

Loopback bind is the v1 security boundary (no admin login yet); see the spec's
Non-goals.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from mahjong.control.plane import ControlPlane

_logger = logging.getLogger(__name__)

SUBPROTOCOL = "mahjong-admin-v1"

_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class AdminWebServer:
    def __init__(
        self,
        *,
        plane: ControlPlane,
        host: str = "127.0.0.1",
        port: int = 8500,
        static_dir: Path | None = None,
        status_interval_s: float = 2.0,
    ) -> None:
        self._plane = plane
        self._host = host
        self._port = port
        self._static_dir = static_dir.resolve() if static_dir else None
        self._status_interval_s = status_interval_s
        self._server: Server | None = None
        self._clients: set[ServerConnection] = set()
        self._broadcast_task: asyncio.Task[None] | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            return self._port
        socks = self._server.sockets
        if not socks:
            raise RuntimeError("server has no bound sockets")
        return int(socks[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("AdminWebServer already started")
        self._server = await serve(
            self._handle_ws,
            self._host,
            self._port,
            subprotocols=[SUBPROTOCOL],  # type: ignore[list-item]
            process_request=self._process_request,
        )
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def close(self) -> None:
        if self._broadcast_task is not None:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task
            self._broadcast_task = None
        if self._server is not None:
            self._server.close(close_connections=True)
            await self._server.wait_closed()
            self._server = None

    # --- HTTP (static assets) ---

    def _process_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        offered = request.headers.get_all("Sec-WebSocket-Protocol")
        if offered:
            return None  # let the WS upgrade proceed
        path = request.path.split("?", 1)[0]
        if self._static_dir is None:
            return connection.respond(404, "admin UI not bundled\n")
        return self._serve_static(connection, path)

    def _serve_static(self, connection: ServerConnection, path: str) -> Response:
        assert self._static_dir is not None
        if path == "/":
            target = self._static_dir / "index.html"
        elif path.startswith("/static/"):
            target = (self._static_dir / path[len("/static/") :]).resolve()
            if not target.is_relative_to(self._static_dir):
                return connection.respond(404, "not found\n")
        else:
            return connection.respond(404, "not found\n")
        if not target.is_file():
            return connection.respond(404, "not found\n")
        body = target.read_bytes()
        headers = Headers()
        headers["Content-Type"] = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        headers["Content-Length"] = str(len(body))
        return Response(200, "OK", headers, body)

    # --- WebSocket (control channel) ---

    async def _handle_ws(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        try:
            await self._send(ws, await self._plane.build_status())
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send(ws, {"kind": "ERROR", "code": "bad_json"})
                    continue
                reply = await self._plane.handle_command(msg)
                await self._send(ws, reply)
                # A command may have changed server state; nudge everyone.
                if reply.get("kind") == "STATUS":
                    await self._broadcast(reply)
        except Exception:
            _logger.debug("admin.ws_handler_closed", exc_info=True)
        finally:
            self._clients.discard(ws)

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(self._status_interval_s)
            if self._clients:
                with contextlib.suppress(Exception):
                    await self._broadcast(await self._plane.build_status())

    async def _broadcast(self, frame: dict[str, Any]) -> None:
        for ws in list(self._clients):
            await self._send(ws, frame)

    @staticmethod
    async def _send(ws: ServerConnection, frame: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await ws.send(json.dumps(frame))


__all__ = ["SUBPROTOCOL", "AdminWebServer"]
