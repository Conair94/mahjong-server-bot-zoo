"""GET_PROFILE / GET_HISTORY / GET_REPLAY must work while attached to a table.

Same two-phase trap as the FEEDBACK in-game regression (see
``test_feedback_in_game.py``): once a connection attaches to a table it enters
the orchestrator's Phase 2 inbound loop, which delegates every frame to
``TableSessions.handle_inbound``.  That only knows ACTION/DETACH/STOP_SPECTATING,
so a lobby/account read frame used to come back as ``ERROR unknown_kind``.

The profile button is rendered in the table header (app.js — it shows whenever
``profileSupported``, including ``_view === "table"``), so clicking it mid-game
sent GET_PROFILE into Phase 2 and the client hung forever on "Loading profile…".
The orchestrator now intercepts these read-only kinds in Phase 2.
"""

from __future__ import annotations

import asyncio
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

MCR_REF: dict[str, Any] = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}


def _make_orch(tmp_path: Path, persistence: Persistence) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=7,
        server_info={"version": "profile-ingame-test", "git_sha": "x", "host": "t"},
        between_hand_pause_seconds=0.05,
        persistence=persistence,
    )


async def _recv_until(ws: Any, kind: str, *, forbid: str = "unknown_kind") -> dict[str, Any]:
    """Read frames until one of ``kind`` arrives, skipping interleaved game
    frames.  Fails loudly if the connection hangs (timeout) or replies with an
    ``ERROR`` whose code is ``forbid`` (the pre-fix symptom)."""
    for _ in range(80):
        frame = json.loads(cast(str, await asyncio.wait_for(ws.recv(), timeout=5.0)))
        if frame.get("kind") == "ERROR" and frame.get("code") == forbid:
            raise AssertionError(f"got ERROR {forbid} instead of {kind}: {frame}")
        if frame.get("kind") == kind:
            return frame
    raise AssertionError(f"never received a {kind} frame")


async def test_get_profile_while_attached_returns_profile(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(
        p._conn,  # type: ignore[attr-defined]
        username="connor",
        display_name="Connor",
        kind="human",
        role="user",
        password="connorpw12",
    )

    orch = _make_orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            hello = json.loads(cast(str, await ws.recv()))
            assert hello["kind"] == "HELLO", hello

            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "connor", "password": "connorpw12"}
                )
            )
            auth = json.loads(cast(str, await ws.recv()))
            assert auth["ok"] is True, auth

            await ws.send(
                json.dumps(
                    {
                        "kind": "CREATE_TABLE",
                        "ruleset": "mcr-2006",
                        "seats": [
                            {"kind": "human"},
                            {"kind": "bot"},
                            {"kind": "bot"},
                            {"kind": "bot"},
                        ],
                    }
                )
            )
            created = json.loads(cast(str, await ws.recv()))
            assert created["kind"] == "TABLE_CREATED", created
            table_id = created["table_id"]

            # Attach to the human seat → connection enters the Phase 2 inbound loop.
            await ws.send(json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 0}))
            attached = await _recv_until(ws, "ATTACHED")
            assert attached["kind"] == "ATTACHED", attached

            # Click [ profile ] from inside the table.  Before the fix this fell
            # through to the table session and came back ERROR unknown_kind, so the
            # profile page spun on "Loading profile…" forever.  It must now answer.
            await ws.send(json.dumps({"kind": "GET_PROFILE"}))
            profile = await _recv_until(ws, "PROFILE")
            assert profile["account"]["display_name"] == "Connor"
            assert profile["stats"]["hands_played"] == 0  # the in-progress hand isn't finalized
    finally:
        await orch.close()
        p.close()
