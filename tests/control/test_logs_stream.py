"""LOG_SUBSCRIBE backlog + live tail (Spec 25 step 8, fixture log_ring_buffer).

Drives a real AdminWebServer over a WebSocket against a real LogRingBuffer, so it
verifies both the backlog reply and the broadcast-loop streaming of new lines.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
import websockets

from mahjong.control.logbuffer import LogRingBuffer
from mahjong.control.plane import ControlPlane
from mahjong.control.server import SUBPROTOCOL, AdminWebServer
from mahjong.control.supervisor import ServerState

pytestmark = pytest.mark.asyncio


class _FakeSup:
    state = ServerState.STOPPED
    pid = None
    started_at_monotonic = None


class _FakeMetrics:
    latest = None


def _plane(buf: LogRingBuffer) -> ControlPlane:
    async def fetch() -> dict[str, Any] | None:
        return None

    return ControlPlane(
        supervisor=_FakeSup(),  # type: ignore[arg-type]
        metrics=_FakeMetrics(),  # type: ignore[arg-type]
        admin_status_fetch=fetch,
        server_listen_url="ws://x:1",
        log_buffer=buf,
    )


async def _recv(ws: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(await ws.recv()))


async def _recv_kind(ws: Any, kind: str, *, tries: int = 10) -> dict[str, Any]:
    for _ in range(tries):
        m = await _recv(ws)
        if m.get("kind") == kind:
            return m
    raise AssertionError(f"never received {kind}")


# --- plane-level unit ---


async def test_log_recent_and_since() -> None:
    buf = LogRingBuffer(maxlen=10)
    for i in range(3):
        buf.append(f"line-{i}", "stdout")
    lines, cursor = _plane(buf).log_recent()
    assert [ln["text"] for ln in lines] == ["line-0", "line-1", "line-2"]
    assert cursor == 3
    newer, cur2 = _plane(buf).log_since(2)
    assert [ln["line"] for ln in newer] == [3]
    assert cur2 == 3


# --- socket-level backlog + live tail ---


async def test_subscribe_backlog_then_live_tail() -> None:
    buf = LogRingBuffer(maxlen=100)
    buf.append("startup-line", "stdout")
    server = AdminWebServer(
        plane=_plane(buf), host="127.0.0.1", port=0, status_interval_s=0.1
    )
    await server.start()
    try:
        url = f"ws://127.0.0.1:{server.port}/"
        async with websockets.connect(url, subprotocols=[SUBPROTOCOL]) as ws:
            await _recv_kind(ws, "STATUS")  # initial status
            await ws.send(json.dumps({"kind": "LOG_SUBSCRIBE"}))
            backlog = await _recv_kind(ws, "LOG_BATCH")
            assert any(ln["text"] == "startup-line" for ln in backlog["lines"])

            # New line appears after subscribe → streamed by the broadcast loop.
            buf.append("live-line", "stderr")
            batch = await _recv_kind(ws, "LOG_BATCH", tries=30)
            assert any(ln["text"] == "live-line" and ln["stream"] == "stderr" for ln in batch["lines"])
    finally:
        await server.close()
