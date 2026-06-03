"""Full-stack walking-skeleton check: console → supervisor → real serve → STATUS.

Slow: the console actually spawns ``python -m mahjong serve``, drives it through
the admin WS (SERVER_START / SERVER_STOP), and observes the live STATUS reflect
the running server.  This is the walking-skeleton verification artifact for
Spec 25 step 5.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.control.app import ControlApp, ControlConfig
from mahjong.control.server import SUBPROTOCOL

pytestmark = [pytest.mark.asyncio, pytest.mark.slow]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _recv(ws: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(await ws.recv()))


async def _await_state(ws: Any, state: str, *, max_frames: int = 40) -> dict[str, Any]:
    """Read STATUS frames until server.state == *state*."""
    for _ in range(max_frames):
        msg = await _recv(ws)
        if msg.get("kind") == "STATUS" and msg["server"]["state"] == state:
            return msg
    raise AssertionError(f"server never reached {state}")


async def test_console_starts_and_stops_real_server(tmp_path: Path) -> None:
    server_port = _free_port()
    env = {
        **os.environ,
        "MAHJONG_DATA_DIR": str(tmp_path),
        "MAHJONG_LISTEN_ADDR": f"127.0.0.1:{server_port}",
        "MAHJONG_BOT_PACING": "0",
    }
    app = ControlApp(
        config=ControlConfig(ctl_host="127.0.0.1", ctl_port=0, startup_timeout_s=25.0),
        server_env=env,
        server_listen_addr=f"127.0.0.1:{server_port}",
    )
    await app.start()
    try:
        ws_url = app.url.replace("http://", "ws://")
        async with websockets.connect(ws_url, subprotocols=[SUBPROTOCOL]) as ws:
            first = await _recv(ws)
            assert first["kind"] == "STATUS"
            assert first["server"]["state"] == "STOPPED"

            # Start the real server from the console.
            await ws.send(json.dumps({"kind": "SERVER_START"}))
            running = await _await_state(ws, "RUNNING")
            assert running["server"]["pid"] is not None
            assert running["health"]["admin_status_ok"] is True
            assert running["server"]["players_connected"] == 0

            # Stop it again.
            await ws.send(json.dumps({"kind": "SERVER_STOP"}))
            await _await_state(ws, "STOPPED")
    finally:
        await app.aclose()
