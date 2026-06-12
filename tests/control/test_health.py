"""HealthMonitor + STATUS.health block (Spec 25 step 11).

Real temp DB → real integrity_check + disk usage.  No mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mahjong.control.health import HealthMonitor
from mahjong.control.plane import ControlPlane
from mahjong.control.supervisor import ServerState
from mahjong.persistence import Persistence

pytestmark = pytest.mark.asyncio


class _FakeSup:
    state = ServerState.STOPPED
    pid = None
    started_at_monotonic = None


class _FakeMetrics:
    latest = None


async def test_snapshot_reports_integrity_and_storage(tmp_path: Path) -> None:
    p = Persistence(str(tmp_path / "mj.db"), tmp_path)
    try:
        mon = HealthMonitor(persistence=p, db_path=tmp_path / "mj.db")
        snap = await mon.snapshot()
        assert snap["db_integrity_ok"] is True
        assert snap["disk_free_bytes"] > 0
        assert snap["wal_bytes"] >= 0
    finally:
        p.close()


async def test_integrity_result_is_cached(tmp_path: Path) -> None:
    """A second snapshot within the TTL must not re-run integrity_check."""
    calls = {"n": 0}

    class _CountingP:
        def integrity_check(self):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            from types import SimpleNamespace

            return SimpleNamespace(pragma_ok=True)

    mon = HealthMonitor(persistence=_CountingP(), db_path=tmp_path / "x.db", integrity_ttl_s=60.0)
    await mon.snapshot()
    await mon.snapshot()
    assert calls["n"] == 1  # cached on the second call


async def test_status_includes_health_block(tmp_path: Path) -> None:
    p = Persistence(str(tmp_path / "mj.db"), tmp_path)
    try:

        async def fetch() -> dict | None:
            return None

        plane = ControlPlane(
            supervisor=_FakeSup(),  # type: ignore[arg-type]
            metrics=_FakeMetrics(),  # type: ignore[arg-type]
            admin_status_fetch=fetch,
            server_listen_url="ws://x:1",
            health_monitor=HealthMonitor(persistence=p, db_path=tmp_path / "mj.db"),
        )
        status = await plane.build_status()
        h = status["health"]
        assert h["admin_status_ok"] is False
        assert h["db_integrity_ok"] is True
        assert h["disk_free_bytes"] > 0
    finally:
        p.close()
