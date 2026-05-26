"""AUTH_REQUEST / RESUME wired into the multi-table orchestrator.

Spec: docs/specs/auth.md § Wire flow,
      docs/specs/wire-protocol.md § Authentication.
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
        server_info={"version": "auth-test", "git_sha": "x", "host": "t"},
        between_hand_pause_seconds=0.05,
        persistence=persistence,
    )


async def test_auth_request_success_returns_token(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="bob",
        display_name="Bob",
        kind="human",
        role="user",
        password="bobbobbob",
    )

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO
            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "bob", "password": "bobbobbob"}
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "AUTH_RESPONSE"
            assert resp["ok"] is True
            assert resp["session_token"].startswith("s_")
            assert resp["display_name"] == "Bob"
    finally:
        await orch.close()
        p.close()


async def test_auth_request_failure_does_not_leak_reason(tmp_path: Path) -> None:
    """Wrong-password and unknown-user both return identical AUTH_RESPONSE { ok: false }."""
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="bob",
        display_name="Bob",
        kind="human",
        role="user",
        password="rightpass1",
    )

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        # wrong password
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO
            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "bob", "password": "wrongone"}
                )
            )
            wrong_pw_resp = json.loads(cast(str, await ws.recv()))

        # unknown user
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO
            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "ghost", "password": "anything1"}
                )
            )
            unknown_resp = json.loads(cast(str, await ws.recv()))

        for resp in (wrong_pw_resp, unknown_resp):
            assert resp["kind"] == "AUTH_RESPONSE"
            assert resp["ok"] is False
            # Failure shape must be identical between the two paths (except seq)
            assert "session_token" not in resp
            assert "display_name" not in resp
    finally:
        await orch.close()
        p.close()


async def test_resume_succeeds_with_returned_token(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="carol",
        display_name="Carol",
        kind="human",
        role="user",
        password="carolcarol",
    )

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        # First login obtains a token.
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()
            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "carol", "password": "carolcarol"}
                )
            )
            first = json.loads(cast(str, await ws.recv()))
            token = first["session_token"]

        # Reconnect; RESUME with the token.
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()
            await ws.send(json.dumps({"kind": "RESUME", "session_token": token}))
            resumed = json.loads(cast(str, await ws.recv()))
            assert resumed["kind"] == "AUTH_RESPONSE"
            assert resumed["ok"] is True
            assert resumed["session_token"] == token  # no rotation in v1
    finally:
        await orch.close()
        p.close()
