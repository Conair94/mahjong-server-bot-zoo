"""AdminWebServer end-to-end over a real WebSocket (Spec 25 § socket layer).

Uses a fake supervisor (no real subprocess) so the test is fast: it verifies the
socket glue — initial STATUS on connect, command dispatch, and the reply frame —
against the real ``ControlPlane`` + ``AdminWebServer``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
import websockets

from mahjong.control.metrics import Metrics
from mahjong.control.plane import ControlPlane
from mahjong.control.server import SUBPROTOCOL, AdminWebServer
from mahjong.control.supervisor import ServerState

pytestmark = pytest.mark.asyncio


class _FakeSupervisor:
    def __init__(self) -> None:
        self.state = ServerState.STOPPED
        self.pid: int | None = None
        self.started_at_monotonic: float | None = None

    async def start(self) -> bool:
        self.state = ServerState.RUNNING
        self.pid = 1234
        return True

    async def stop(self) -> None:
        self.state = ServerState.STOPPED
        self.pid = None

    async def restart(self) -> bool:
        self.state = ServerState.RUNNING
        return True


class _FakeMetrics:
    latest = Metrics(cpu_pct=1.0, mem_rss_bytes=1024)


@asynccontextmanager
async def _running(plane: ControlPlane) -> AsyncIterator[AdminWebServer]:
    server = AdminWebServer(plane=plane, host="127.0.0.1", port=0, status_interval_s=60.0)
    await server.start()
    try:
        yield server
    finally:
        await server.close()


def _make_plane(sup: _FakeSupervisor) -> ControlPlane:
    async def fetch() -> dict[str, Any] | None:
        return None  # server treated as not reporting in this fast test

    return ControlPlane(
        supervisor=sup,  # type: ignore[arg-type]
        metrics=_FakeMetrics(),  # type: ignore[arg-type]
        admin_status_fetch=fetch,
        server_listen_url="ws://0.0.0.0:8400",
    )


async def _recv(ws: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(await ws.recv()))


async def test_initial_status_on_connect() -> None:
    sup = _FakeSupervisor()
    async with _running(_make_plane(sup)) as server:
        url = f"ws://127.0.0.1:{server.port}/"
        async with websockets.connect(url, subprotocols=[SUBPROTOCOL]) as ws:
            first = await _recv(ws)
            assert first["kind"] == "STATUS"
            assert first["server"]["state"] == "STOPPED"


async def test_server_start_command_round_trip() -> None:
    sup = _FakeSupervisor()
    async with _running(_make_plane(sup)) as server:
        url = f"ws://127.0.0.1:{server.port}/"
        async with websockets.connect(url, subprotocols=[SUBPROTOCOL]) as ws:
            await _recv(ws)  # initial STATUS
            await ws.send(json.dumps({"kind": "SERVER_START"}))
            reply = await _recv(ws)
            assert reply["kind"] == "STATUS"
            assert reply["server"]["state"] == "RUNNING"
            assert sup.state is ServerState.RUNNING


async def test_bad_json_yields_error_not_drop() -> None:
    sup = _FakeSupervisor()
    async with _running(_make_plane(sup)) as server:
        url = f"ws://127.0.0.1:{server.port}/"
        async with websockets.connect(url, subprotocols=[SUBPROTOCOL]) as ws:
            await _recv(ws)
            await ws.send("not json{")
            reply = await _recv(ws)
            assert reply["kind"] == "ERROR"
            assert reply["code"] == "bad_json"


async def test_serves_admin_ui_index() -> None:
    """GET / returns the bundled admin index.html (the dashboard shell)."""
    import asyncio
    import urllib.request

    from mahjong.control import static_root

    sup = _FakeSupervisor()
    server = AdminWebServer(
        plane=_make_plane(sup), host="127.0.0.1", port=0, static_dir=static_root()
    )
    await server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/"

        def _fetch() -> tuple[int, str]:
            with urllib.request.urlopen(url, timeout=2.0) as r:
                return r.status, r.read().decode("utf-8")

        status, body = await asyncio.get_running_loop().run_in_executor(None, _fetch)
        assert status == 200
        assert "<admin-app>" in body
    finally:
        await server.close()
