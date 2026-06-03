"""End-to-end WS round-trips for the feedback + tunnel panes (steps 9, 10).

Spec: docs/specs/admin-console.md fixtures ``feedback_inbox`` + ``tunnel_url_parse``.

Mirrors test_admin_server_e2e.py's harness but wires a real FeedbackInbox (reading
a tmp reports dir) and a real TunnelSupervisor (pointed at a missing binary) so the
new commands are exercised over an actual ``mahjong-admin-v1`` socket — the same
path the browser uses — not just at the plane level.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
import websockets

from mahjong.control.feedback import FeedbackInbox
from mahjong.control.metrics import Metrics
from mahjong.control.plane import ControlPlane
from mahjong.control.server import SUBPROTOCOL, AdminWebServer
from mahjong.control.supervisor import ServerState
from mahjong.control.tunnel import TunnelSupervisor

pytestmark = pytest.mark.asyncio


class _FakeSupervisor:
    state = ServerState.STOPPED
    pid: int | None = None
    started_at_monotonic: float | None = None

    async def start(self) -> bool:
        return True

    async def stop(self) -> None:
        return None

    async def restart(self) -> bool:
        return True


class _FakeMetrics:
    latest = Metrics(cpu_pct=0.0, mem_rss_bytes=0)


@asynccontextmanager
async def _running(plane: ControlPlane) -> AsyncIterator[AdminWebServer]:
    server = AdminWebServer(plane=plane, host="127.0.0.1", port=0, status_interval_s=60.0)
    await server.start()
    try:
        yield server
    finally:
        await server.close()


async def _fetch_none() -> dict[str, Any] | None:
    return None


async def _recv(ws: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(await ws.recv()))


async def test_feedback_list_round_trip(tmp_path: Any) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    # Exactly how the server's _write_report lays a file down: header + the
    # sanitised body (which is .strip()ed, so no trailing newline).
    (reports / "20260603_120000_bug.txt").write_text(
        "type: bug\nsubmitted: 2026-06-03T12:00:00+00:00\nsubmitter: Alice\n---\ntiles overlap",
        encoding="utf-8",
    )
    plane = ControlPlane(
        supervisor=_FakeSupervisor(),  # type: ignore[arg-type]
        metrics=_FakeMetrics(),  # type: ignore[arg-type]
        admin_status_fetch=_fetch_none,
        server_listen_url="ws://0.0.0.0:8400",
        feedback=FeedbackInbox(reports),
    )
    async with _running(plane) as server:
        url = f"ws://127.0.0.1:{server.port}/"
        async with websockets.connect(url, subprotocols=[SUBPROTOCOL]) as ws:
            await _recv(ws)  # initial STATUS
            await ws.send(json.dumps({"kind": "FEEDBACK_LIST"}))
            reply = await _recv(ws)
            assert reply["kind"] == "FEEDBACK_LIST"
            assert len(reply["reports"]) == 1
            row = reply["reports"][0]
            assert row["type"] == "bug"
            assert row["submitter"] == "Alice"
            assert row["text"] == "tiles overlap"


async def test_tunnel_start_missing_binary_reports_error_over_ws() -> None:
    tunnel = TunnelSupervisor(argv=["definitely-not-a-real-binary-xyz", "tunnel"])
    plane = ControlPlane(
        supervisor=_FakeSupervisor(),  # type: ignore[arg-type]
        metrics=_FakeMetrics(),  # type: ignore[arg-type]
        admin_status_fetch=_fetch_none,
        server_listen_url="ws://0.0.0.0:8400",
        tunnel=tunnel,
    )
    async with _running(plane) as server:
        url = f"ws://127.0.0.1:{server.port}/"
        async with websockets.connect(url, subprotocols=[SUBPROTOCOL]) as ws:
            await _recv(ws)  # initial STATUS
            await ws.send(json.dumps({"kind": "TUNNEL_START"}))
            reply = await _recv(ws)
            assert reply["kind"] == "STATUS"
            # Graceful: a missing cloudflared surfaces as an error field, the
            # tunnel stays down, and the socket is NOT dropped.
            assert reply["tunnel"]["running"] is False
            assert reply["tunnel"]["error"] == "cloudflared_not_found"
