"""ServerSupervisor against a real `serve` child (Spec 25 fixture supervisor_lifecycle).

Slow: spawns the actual server, waits for its listener via the HTTP readiness
probe, and confirms the port frees on stop.  Marked ``slow`` so the default fast
suite skips it (see feedback_slow_pytest_mark).
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest

from mahjong.control.supervisor import (
    ServerState,
    ServerSupervisor,
    make_http_readiness_probe,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.slow]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


async def test_real_serve_lifecycle(tmp_path: Path) -> None:
    port = _free_port()
    token = "integration-token"
    env = {
        **os.environ,
        "MAHJONG_DATA_DIR": str(tmp_path),
        "MAHJONG_LISTEN_ADDR": f"127.0.0.1:{port}",
        "MAHJONG_ADMIN_TOKEN": token,
        "MAHJONG_BOT_PACING": "0",  # no wall-clock pacing in the test
    }
    probe = make_http_readiness_probe(f"http://127.0.0.1:{port}/admin/status", token)
    sup = ServerSupervisor(
        argv=[sys.executable, "-m", "mahjong", "serve"],
        env=env,
        readiness_probe=probe,
        startup_timeout_s=20.0,
    )

    ok = await sup.start()
    try:
        assert ok is True
        assert sup.state is ServerState.RUNNING
        assert _port_open(port)  # the real listener is bound
    finally:
        await sup.stop()

    assert sup.state is ServerState.STOPPED
    # Give the kernel a beat to release the socket, then confirm it's free.
    for _ in range(20):
        if not _port_open(port):
            break
        await asyncio.sleep(0.1)
    assert not _port_open(port)


async def test_real_serve_restart(tmp_path: Path) -> None:
    port = _free_port()
    token = "integration-token-2"
    env = {
        **os.environ,
        "MAHJONG_DATA_DIR": str(tmp_path),
        "MAHJONG_LISTEN_ADDR": f"127.0.0.1:{port}",
        "MAHJONG_ADMIN_TOKEN": token,
        "MAHJONG_BOT_PACING": "0",
    }
    probe = make_http_readiness_probe(f"http://127.0.0.1:{port}/admin/status", token)
    sup = ServerSupervisor(
        argv=[sys.executable, "-m", "mahjong", "serve"],
        env=env,
        readiness_probe=probe,
        startup_timeout_s=20.0,
    )
    await sup.start()
    first_pid = sup.pid
    try:
        ok = await sup.restart()
        assert ok is True
        assert sup.state is ServerState.RUNNING
        assert sup.pid != first_pid
        assert _port_open(port)
    finally:
        await sup.stop()
