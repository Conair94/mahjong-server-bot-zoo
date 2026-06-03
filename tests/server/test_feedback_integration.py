"""Integration tests for the FEEDBACK wire message.

Spec: docs/specs/feedback-reporting.md § 23.1, § 23.4.

Tests written before the handler implementation (TDD).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.server.orchestrator import MultiTableOrchestrator

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _orch(tmp_path: Path, persistence: Persistence) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "feedback-test", "git_sha": "x", "host": "t"},
        between_hand_pause_seconds=0.05,
        persistence=persistence,
    )


async def _authed_ws(orch: MultiTableOrchestrator, *, username: str, password: str):
    """Context manager: yields a WS that has completed AUTH_REQUEST."""
    url = f"ws://127.0.0.1:{orch.port}"
    return websockets.connect(url, subprotocols=["mahjong-v1"])


async def _login(ws, *, username: str, password: str) -> None:
    await ws.recv()  # HELLO
    await ws.send(json.dumps({"kind": "AUTH_REQUEST", "username": username, "password": password}))
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "AUTH_RESPONSE" and resp["ok"] is True


async def test_feedback_bug_creates_file(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(p._conn, username="alice", display_name="Alice", kind="human", role="user", password="alicealice")  # type: ignore[attr-defined]

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{orch.port}", subprotocols=["mahjong-v1"]) as ws:
            await _login(ws, username="alice", password="alicealice")
            await ws.send(json.dumps({"kind": "FEEDBACK", "type": "bug", "text": "The discard button disappears sometimes."}))
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "FEEDBACK_ACK"

        reports = list((tmp_path / "reports").glob("*.txt"))
        assert len(reports) == 1
        content = reports[0].read_text()
        assert "type: bug" in content
        assert "submitter: Alice" in content
        assert "The discard button disappears sometimes." in content
    finally:
        await orch.close()


async def test_feedback_feature_creates_file(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(p._conn, username="bob", display_name="Bob", kind="human", role="user", password="bobbobbobbob")  # type: ignore[attr-defined]

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{orch.port}", subprotocols=["mahjong-v1"]) as ws:
            await _login(ws, username="bob", password="bobbobbobbob")
            await ws.send(json.dumps({"kind": "FEEDBACK", "type": "feature", "text": "Please add a chat window to the lobby."}))
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "FEEDBACK_ACK"

        reports = list((tmp_path / "reports").glob("*.txt"))
        assert len(reports) == 1
        assert "type: feature" in reports[0].read_text()
    finally:
        await orch.close()


async def test_feedback_invalid_type_returns_error(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(p._conn, username="carol", display_name="Carol", kind="human", role="user", password="carolcarol")  # type: ignore[attr-defined]

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{orch.port}", subprotocols=["mahjong-v1"]) as ws:
            await _login(ws, username="carol", password="carolcarol")
            await ws.send(json.dumps({"kind": "FEEDBACK", "type": "complaint", "text": "This is a complaint about the game rules."}))
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR"
            assert resp["code"] == "feedback_error"

        assert not list((tmp_path / "reports").glob("*.txt"))
    finally:
        await orch.close()


async def test_feedback_text_too_short_returns_error(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(p._conn, username="dave", display_name="Dave", kind="human", role="user", password="davedavedave")  # type: ignore[attr-defined]

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{orch.port}", subprotocols=["mahjong-v1"]) as ws:
            await _login(ws, username="dave", password="davedavedave")
            await ws.send(json.dumps({"kind": "FEEDBACK", "type": "bug", "text": "bad"}))
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR"
            assert resp["code"] == "feedback_error"
    finally:
        await orch.close()


async def test_feedback_unauthenticated_disconnects(tmp_path: Path) -> None:
    """FEEDBACK before auth should not be reachable — server returns ERROR unexpected_kind."""
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{orch.port}", subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO — skip auth
            await ws.send(json.dumps({"kind": "FEEDBACK", "type": "bug", "text": "This should be rejected since auth is required."}))
            # Server expects AUTH_REQUEST first; FEEDBACK is an unexpected kind → ERROR + close
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR"
    finally:
        await orch.close()
