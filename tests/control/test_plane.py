"""ControlPlane — command dispatch + STATUS aggregation (the WS message contract).

Spec: docs/specs/admin-console.md § "Control-plane WS protocol", fixture
``ctl_status_aggregation``.

The plane is exercised directly with fake supervisor/metrics and an injected
admin-status fetch — no sockets.  The socket layer (AdminWebServer) is thin glue
over this.
"""

from __future__ import annotations

import time

import pytest

from mahjong.control.metrics import Metrics
from mahjong.control.plane import ControlPlane
from mahjong.control.supervisor import ServerState

pytestmark = pytest.mark.asyncio


class _FakeSupervisor:
    def __init__(self, state: ServerState, *, pid: int | None = None) -> None:
        self.state = state
        self.pid = pid
        self.started_at_monotonic = time.monotonic() if state is ServerState.RUNNING else None
        self.calls: list[str] = []

    async def start(self) -> bool:
        self.calls.append("start")
        self.state = ServerState.RUNNING
        self.pid = 4242
        self.started_at_monotonic = time.monotonic()
        return True

    async def stop(self) -> None:
        self.calls.append("stop")
        self.state = ServerState.STOPPED
        self.pid = None
        self.started_at_monotonic = None

    async def restart(self) -> bool:
        self.calls.append("restart")
        self.state = ServerState.RUNNING
        return True


class _FakeMetrics:
    def __init__(self, latest: Metrics | None) -> None:
        self.latest = latest


def _plane(
    *,
    supervisor: _FakeSupervisor,
    metrics_latest: Metrics | None = None,
    admin_status: dict | None = None,
) -> ControlPlane:
    async def fetch() -> dict | None:
        return admin_status

    return ControlPlane(
        supervisor=supervisor,  # type: ignore[arg-type]
        metrics=_FakeMetrics(metrics_latest),  # type: ignore[arg-type]
        admin_status_fetch=fetch,
        server_listen_url="ws://0.0.0.0:8400",
    )


# --- STATUS aggregation ---


async def test_status_running_aggregates_all_sources() -> None:
    sup = _FakeSupervisor(ServerState.RUNNING, pid=4242)
    plane = _plane(
        supervisor=sup,
        metrics_latest=Metrics(cpu_pct=4.2, mem_rss_bytes=96_329_728),
        admin_status={
            "uptime_s": 11532,
            "players_connected": 5,
            "tables": [{"table_id": 1, "phase": "IN_PROGRESS"}],
        },
    )
    status = await plane.build_status()
    assert status["kind"] == "STATUS"
    server = status["server"]
    assert server["state"] == "RUNNING"
    assert server["pid"] == 4242
    assert server["cpu_pct"] == 4.2
    assert server["mem_rss_bytes"] == 96_329_728
    assert server["players_connected"] == 5
    assert server["tables"] == [{"table_id": 1, "phase": "IN_PROGRESS"}]
    assert server["listen_url"] == "ws://0.0.0.0:8400"
    assert status["health"]["admin_status_ok"] is True
    assert status["tunnel"] == {"running": False, "url": None}


async def test_status_stopped_has_empty_tables_and_null_metrics() -> None:
    sup = _FakeSupervisor(ServerState.STOPPED)
    plane = _plane(supervisor=sup, metrics_latest=None, admin_status=None)
    status = await plane.build_status()
    server = status["server"]
    assert server["state"] == "STOPPED"
    assert server["pid"] is None
    assert server["cpu_pct"] is None
    assert server["mem_rss_bytes"] is None
    assert server["tables"] == []
    assert server["players_connected"] == 0
    assert status["health"]["admin_status_ok"] is False


# --- command dispatch ---


async def test_server_start_command_starts_and_returns_status() -> None:
    sup = _FakeSupervisor(ServerState.STOPPED)
    plane = _plane(supervisor=sup)
    reply = await plane.handle_command({"kind": "SERVER_START"})
    assert sup.calls == ["start"]
    assert reply["kind"] == "STATUS"
    assert reply["server"]["state"] == "RUNNING"


async def test_server_stop_command_stops() -> None:
    sup = _FakeSupervisor(ServerState.RUNNING, pid=1)
    plane = _plane(supervisor=sup)
    reply = await plane.handle_command({"kind": "SERVER_STOP"})
    assert sup.calls == ["stop"]
    assert reply["server"]["state"] == "STOPPED"


async def test_server_restart_command_restarts() -> None:
    sup = _FakeSupervisor(ServerState.RUNNING, pid=1)
    plane = _plane(supervisor=sup)
    reply = await plane.handle_command({"kind": "SERVER_RESTART"})
    assert sup.calls == ["restart"]
    assert reply["kind"] == "STATUS"


async def test_unknown_command_returns_error_frame() -> None:
    sup = _FakeSupervisor(ServerState.STOPPED)
    plane = _plane(supervisor=sup)
    reply = await plane.handle_command({"kind": "NONSENSE"})
    assert reply["kind"] == "ERROR"
    assert reply["code"] == "unknown_command"
    assert sup.calls == []
