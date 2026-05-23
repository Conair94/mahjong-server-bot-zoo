"""Tests for `mahjong.wire.server.WebSocketServer`.

Spec: docs/specs/wire-protocol.md § Transport.

Step 7.2 of CHECKLIST.md. Tests written before the implementation.

Each test starts a real `WebSocketServer` on `127.0.0.1` with port 0 (kernel
picks a free port) and connects with the `websockets` client library against
that loopback listener. No mocks of the transport — these are real-socket
integration tests, gated by an asyncio file-level mark per repo convention
(see feedback_pytest_asyncio_mode_quirk.md).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import websockets
from websockets.exceptions import ConnectionClosedError, InvalidHandshake, InvalidStatus

from mahjong.wire import codec
from mahjong.wire.server import Connection, WebSocketServer

pytestmark = pytest.mark.asyncio


# --- helpers ---


@asynccontextmanager
async def _running_server(
    handler: Any,
    *,
    health_handler: Any = None,
    static_dir: Any = None,
    max_size: int = 16 * 1024,
) -> AsyncIterator[WebSocketServer]:
    """Start a server on a free port, yield it, then close cleanly."""
    server = WebSocketServer(
        host="127.0.0.1",
        port=0,
        handler=handler,
        health_handler=health_handler,
        static_dir=static_dir,
        max_size=max_size,
    )
    await server.start()
    try:
        yield server
    finally:
        await server.close()


# --- connect / hello / close ---


async def test_connect_receives_hello_and_closes() -> None:
    """Server-side handler sends HELLO; client receives it and closes cleanly."""
    hello_payload: dict[str, Any] = {
        "kind": "HELLO",
        "seq": 1,
        "protocol_version": 1,
        "server_id": "mahjong-server-test",
    }

    async def handler(conn: Connection) -> None:
        await conn.send(hello_payload)
        # Wait until the client disconnects so the server side doesn't tear
        # down before the test reads the frame.
        try:
            async for _ in conn:
                pass
        except ConnectionClosedError:
            pass

    async with _running_server(handler) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            assert ws.subprotocol == "mahjong-v1"
            raw = await ws.recv()
            assert isinstance(raw, str)
            assert json.loads(raw) == hello_payload


async def test_inbound_frame_surfaces_as_dict() -> None:
    """A client text frame is delivered to the handler as a decoded dict."""
    received: list[dict[str, Any]] = []

    async def handler(conn: Connection) -> None:
        async for msg in conn:
            received.append(msg)

    async with _running_server(handler) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            payload = {"kind": "HELLO", "protocol_version": 1, "client_id": "test-client"}
            await ws.send(codec.encode(payload).decode("utf-8"))
            # Give the server task a moment to drain.
            await asyncio.sleep(0.05)

    assert received == [{"kind": "HELLO", "protocol_version": 1, "client_id": "test-client"}]


# --- subprotocol enforcement ---


async def test_subprotocol_mismatch_refused() -> None:
    """Client requesting a non-`mahjong-v1` subprotocol is rejected at handshake."""

    async def handler(conn: Connection) -> None:  # pragma: no cover — should never run
        raise AssertionError("handler must not be reached on a bad subprotocol")

    async with _running_server(handler) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        # The websockets library raises InvalidHandshake / InvalidStatus when
        # the server refuses the requested subprotocol.
        with pytest.raises((InvalidHandshake, InvalidStatus, ConnectionClosedError)):
            async with websockets.connect(url, subprotocols=["mahjong-v2"]):
                pass


async def test_no_subprotocol_refused() -> None:
    """Client offering no subprotocol at all is rejected."""

    async def handler(conn: Connection) -> None:  # pragma: no cover
        raise AssertionError("handler must not be reached without subprotocol")

    async with _running_server(handler) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        with pytest.raises((InvalidHandshake, InvalidStatus, ConnectionClosedError)):
            async with websockets.connect(url):
                pass


# --- binary frame rejection ---


async def test_binary_frame_closes_with_1003() -> None:
    """Spec §Transport: binary frame → WS close code 1003 ('unsupported data')."""
    close_code_holder: list[int] = []

    async def handler(conn: Connection) -> None:
        try:
            async for _ in conn:
                pass
        except ConnectionClosedError as exc:
            close_code_holder.append(exc.code)

    async with _running_server(handler) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.send(b"\x00\x01\x02")  # bytes → binary frame
            # Wait for the server to close.
            try:
                await ws.recv()
            except ConnectionClosedError as exc:
                assert exc.rcvd is not None and exc.rcvd.code == 1003


# --- frame size enforcement ---


async def test_oversized_frame_closes_with_1009() -> None:
    """A frame larger than `max_size` triggers a WS close (code 1009).

    The websockets library enforces this at the protocol layer.
    """

    async def handler(conn: Connection) -> None:
        try:
            async for _ in conn:
                pass
        except ConnectionClosedError:
            pass

    # Use a tight cap so the test is cheap.
    async with _running_server(handler, max_size=128) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"], max_size=None) as ws:
            big = json.dumps({"kind": "HELLO", "pad": "x" * 1024})
            await ws.send(big)
            with pytest.raises(ConnectionClosedError) as exc_info:
                await ws.recv()
            rcvd = exc_info.value.rcvd
            assert rcvd is not None and rcvd.code in (1009, 1011)


# --- ping/pong keepalive ---


async def test_ping_pong_keeps_connection_open() -> None:
    """The websockets library transparently answers ping with pong; an idle
    connection stays open while pings round-trip."""

    async def handler(conn: Connection) -> None:
        try:
            async for _ in conn:
                pass
        except ConnectionClosedError:
            pass

    async with _running_server(handler) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"], ping_interval=None) as ws:
            # Send an explicit ping; await the pong future.
            pong_waiter = await ws.ping(b"keepalive")
            await asyncio.wait_for(pong_waiter, timeout=1.0)
            # Connection survives.
            assert ws.state.name in ("OPEN", "CONNECTING")


# --- /health hook ---


async def test_health_handler_returns_provided_status() -> None:
    """An HTTP GET /health on the listener invokes the optional `health_handler`."""

    def health_handler() -> tuple[int, bytes]:
        return 200, b'{"status": "ok"}'

    async def ws_handler(conn: Connection) -> None:  # pragma: no cover
        return

    async with _running_server(ws_handler, health_handler=health_handler) as server:
        url = f"http://127.0.0.1:{server.port}/health"
        # Run urllib in a thread so we don't block the event loop.
        loop = asyncio.get_running_loop()
        body, status = await loop.run_in_executor(None, _fetch, url)
        assert status == 200
        assert body == b'{"status": "ok"}'


async def test_health_default_returns_503_when_no_handler() -> None:
    """Without a health handler, /health returns 503 — wired but unconfigured."""

    async def ws_handler(conn: Connection) -> None:  # pragma: no cover
        return

    async with _running_server(ws_handler) as server:
        url = f"http://127.0.0.1:{server.port}/health"
        loop = asyncio.get_running_loop()
        _body, status = await loop.run_in_executor(None, _fetch, url)
        assert status == 503


def _fetch(url: str) -> tuple[bytes, int]:
    """Synchronous urlopen helper. Returns (body, status). Treats HTTPError as
    a real response (we want to see 503s)."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        return exc.read(), exc.code


# --- lifecycle ---


async def test_stop_accepting_refuses_new_connections() -> None:
    """After `stop_accepting()`, new connections fail; existing ones live on
    until `close()` is called."""

    async def handler(conn: Connection) -> None:
        # Keep the connection alive until the test closes us.
        try:
            async for _ in conn:
                pass
        except ConnectionClosedError:
            pass

    server = WebSocketServer(host="127.0.0.1", port=0, handler=handler)
    await server.start()
    try:
        url = f"ws://127.0.0.1:{server.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as alive:
            await server.stop_accepting()
            # New connection should fail.
            with pytest.raises(OSError):
                async with websockets.connect(url, subprotocols=["mahjong-v1"], open_timeout=1.0):
                    pass
            # Existing connection is still usable.
            assert alive.state.name == "OPEN"
    finally:
        await server.close()


async def test_port_is_bound_after_start() -> None:
    async def handler(conn: Connection) -> None:  # pragma: no cover
        return

    server = WebSocketServer(host="127.0.0.1", port=0, handler=handler)
    await server.start()
    try:
        assert server.port > 0
        assert server.port < 65536
    finally:
        await server.close()


# --- static asset serving (tui-client.md §Server-side static asset serving) ---


async def test_static_root_serves_index_html(tmp_path: Any) -> None:
    """GET / returns index.html with text/html content-type."""
    (tmp_path / "index.html").write_text("<!doctype html><title>mahjong</title>")

    async def ws_handler(conn: Connection) -> None:  # pragma: no cover
        return

    async with _running_server(ws_handler, static_dir=tmp_path) as server:
        url = f"http://127.0.0.1:{server.port}/"
        loop = asyncio.get_running_loop()
        body, status, ctype = await loop.run_in_executor(None, _fetch_with_ctype, url)
        assert status == 200
        assert b"<!doctype html>" in body
        assert ctype.startswith("text/html")


async def test_static_serves_nested_assets(tmp_path: Any) -> None:
    """GET /static/<path> returns the file under static_dir with the right content-type."""
    (tmp_path / "app.js").write_text("export const x = 1;")

    async def ws_handler(conn: Connection) -> None:  # pragma: no cover
        return

    async with _running_server(ws_handler, static_dir=tmp_path) as server:
        url = f"http://127.0.0.1:{server.port}/static/app.js"
        loop = asyncio.get_running_loop()
        body, status, ctype = await loop.run_in_executor(None, _fetch_with_ctype, url)
        assert status == 200
        assert body == b"export const x = 1;"
        assert ctype.startswith("text/javascript")


async def test_static_path_traversal_returns_404(tmp_path: Any) -> None:
    """A `..` escape attempt is rejected — never reaches the filesystem."""
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("PRIVATE")
    try:

        async def ws_handler(conn: Connection) -> None:  # pragma: no cover
            return

        async with _running_server(ws_handler, static_dir=tmp_path) as server:
            url = f"http://127.0.0.1:{server.port}/static/../secret.txt"
            loop = asyncio.get_running_loop()
            _body, status = await loop.run_in_executor(None, _fetch, url)
            assert status == 404
    finally:
        secret.unlink(missing_ok=True)


async def test_static_missing_file_returns_404(tmp_path: Any) -> None:
    async def ws_handler(conn: Connection) -> None:  # pragma: no cover
        return

    async with _running_server(ws_handler, static_dir=tmp_path) as server:
        url = f"http://127.0.0.1:{server.port}/static/does-not-exist.js"
        loop = asyncio.get_running_loop()
        _body, status = await loop.run_in_executor(None, _fetch, url)
        assert status == 404


async def test_static_disabled_when_no_dir_configured() -> None:
    """Without a static_dir, GET / falls through to the subprotocol gate (400)."""

    async def ws_handler(conn: Connection) -> None:  # pragma: no cover
        return

    async with _running_server(ws_handler) as server:
        url = f"http://127.0.0.1:{server.port}/"
        loop = asyncio.get_running_loop()
        _body, status = await loop.run_in_executor(None, _fetch, url)
        assert status == 400  # subprotocol mahjong-v1 required


async def test_ws_upgrade_still_works_when_static_configured(tmp_path: Any) -> None:
    """Static dir doesn't interfere with WS handshakes — subprotocol header
    short-circuits the static path before file lookup."""
    (tmp_path / "index.html").write_text("ignored")

    async def ws_handler(conn: Connection) -> None:
        await conn.send({"kind": "HELLO", "seq": 1, "protocol_version": 1, "server_id": "t"})
        try:
            async for _ in conn:
                pass
        except ConnectionClosedError:
            pass

    async with _running_server(ws_handler, static_dir=tmp_path) as server:
        url = f"ws://127.0.0.1:{server.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            msg = json.loads(await ws.recv())
            assert msg["kind"] == "HELLO"


def _fetch_with_ctype(url: str) -> tuple[bytes, int, str]:
    """Like `_fetch`, but also returns the Content-Type header."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return resp.read(), resp.status, resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.read(), exc.code, exc.headers.get("Content-Type", "")
