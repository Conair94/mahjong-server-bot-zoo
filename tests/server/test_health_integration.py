"""Step 8.8.a — `/health` wired through the live orchestrator.

Spec: docs/specs/server-lifecycle.md fixtures 9 (200 normal) + 10 (503 drain).

The handler logic is unit-tested in test_health_endpoint.py; this drives a real
HTTP GET against a running ``MultiTableOrchestrator`` to pin the wiring.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Persistence
from mahjong.server.orchestrator import MultiTableOrchestrator

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
SERVER_INFO: dict[str, Any] = {
    "version": "health-test",
    "server_id": "mahjong-server-0.1.0",
    "git_sha": "test",
    "host": "test",
}


def _get(url: str) -> tuple[int, bytes]:
    """Synchronous GET; returns (status, body).  Treats HTTPError as a response."""
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _make_orch(tmp_path: Path) -> tuple[MultiTableOrchestrator, Persistence]:
    (tmp_path / "records").mkdir(exist_ok=True)
    persistence = Persistence(tmp_path / "mahjong.db", tmp_path)
    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info=SERVER_INFO,
        persistence=persistence,
        shutdown_timeout_s=30.0,
    )
    return orch, persistence


async def test_health_returns_200_when_running(tmp_path: Path) -> None:
    orch, persistence = _make_orch(tmp_path)
    await orch.start()
    url = f"http://127.0.0.1:{orch.port}/health"
    try:
        loop = asyncio.get_running_loop()
        status, body = await loop.run_in_executor(None, _get, url)
        assert status == 200
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["server_id"] == "mahjong-server-0.1.0"
        assert payload["tables"] == 0
        assert isinstance(payload["uptime_s"], int)
    finally:
        await orch.close()
        persistence.close()


async def test_health_returns_503_during_drain(tmp_path: Path) -> None:
    orch, persistence = _make_orch(tmp_path)
    await orch.start()
    url = f"http://127.0.0.1:{orch.port}/health"
    try:
        await orch.registry.drain_all()  # flip accepting_new = False
        loop = asyncio.get_running_loop()
        status, body = await loop.run_in_executor(None, _get, url)
        assert status == 503
        payload = json.loads(body)
        assert payload["status"] == "draining"
        assert "drain_remaining_s" in payload
    finally:
        await orch.close()
        persistence.close()
