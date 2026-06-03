"""End-to-end: the orchestrator actually serves token-gated /admin/status.

Spec: docs/specs/admin-console.md § 1, fixture ``admin_status_token``.

Real socket, real ``WebSocketServer``, real ``TableRegistry`` — the only thing
faked is nothing.  Confirms the wiring from ``MultiTableOrchestrator(admin_token=…)``
through to the live registry projection over HTTP.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.server.orchestrator import MultiTableOrchestrator

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
SERVER_INFO: dict[str, Any] = {"version": "admin-test", "git_sha": "x", "host": "test"}


def _make_orch(tmp_path: Path, *, admin_token: str | None) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=123,
        server_info=SERVER_INFO,
        admin_token=admin_token,
    )


def _fetch(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, int]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        return exc.read(), exc.code


async def _get(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, int]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch, url, headers)


async def test_admin_status_served_with_token(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path, admin_token="tok-123")
    await orch.start()
    try:
        # Populate the registry so tables[] is non-trivial.
        orch.registry.create_table_direct(
            ruleset=MCR_REF, seed=123, server_info=SERVER_INFO, data_dir=tmp_path
        )
        url = f"http://127.0.0.1:{orch.port}/admin/status"

        body, status = await _get(url, {"Authorization": "Bearer tok-123"})
        assert status == 200, body
        payload = json.loads(body)
        # tables[] equals the live registry projection.
        assert payload["tables"] == [s.to_wire() for s in orch.registry.list_tables()]
        assert len(payload["tables"]) == 1
        assert "uptime_s" in payload and payload["uptime_s"] >= 0
    finally:
        await orch.close()


async def test_admin_status_rejects_bad_token(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path, admin_token="tok-123")
    await orch.start()
    try:
        url = f"http://127.0.0.1:{orch.port}/admin/status"
        _body, status = await _get(url, {"Authorization": "Bearer nope"})
        assert status == 401
        _body2, status2 = await _get(url)  # no header at all
        assert status2 == 401
    finally:
        await orch.close()


async def test_admin_status_absent_without_token(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path, admin_token=None)
    await orch.start()
    try:
        url = f"http://127.0.0.1:{orch.port}/admin/status"
        _body, status = await _get(url)
        assert status == 404
    finally:
        await orch.close()
