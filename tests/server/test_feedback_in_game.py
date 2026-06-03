"""FEEDBACK must work while attached to a table, not only in the lobby.

Regression for the in-game feedback hang: once a connection attaches to a table
it enters the orchestrator's Phase 2 inbound loop, which delegates every frame to
``TableSessions.handle_inbound`` — that only knows ACTION/DETACH/STOP_SPECTATING,
so a FEEDBACK frame used to come back as ``ERROR unknown_kind``.  The browser's
feedback modal only closes on FEEDBACK_ACK / feedback_error, so the wrong error
left it spinning forever.  The orchestrator now intercepts FEEDBACK in Phase 2.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.engine.rulesets import MANIFEST
from mahjong.server.orchestrator import MultiTableOrchestrator

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}


def _make_orch(tmp_path: Path) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=7,
        server_info={"version": "fb-ingame-test", "git_sha": "x", "host": "t"},
        between_hand_pause_seconds=0.05,
    )


async def _connect(url: str) -> Any:
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    hello = json.loads(cast(str, await ws.recv()))
    assert hello["kind"] == "HELLO", hello
    return ws


async def test_feedback_while_attached_gets_ack(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
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

            # Attach to the human seat → connection enters the in-game (Phase 2) loop.
            await ws.send(json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 0}))
            attached = json.loads(cast(str, await ws.recv()))
            assert attached["kind"] == "ATTACHED", attached

            # Submit feedback from inside the table.  Before the fix this returned
            # ERROR unknown_kind and the client hung; now it must ACK.
            await ws.send(
                json.dumps(
                    {
                        "kind": "FEEDBACK",
                        "type": "bug",
                        "text": "Feedback submitted from inside a game should work.",
                    }
                )
            )
            reply = json.loads(cast(str, await ws.recv()))
            assert reply["kind"] == "FEEDBACK_ACK", reply

        reports = list((tmp_path / "reports").glob("*.txt"))
        assert len(reports) == 1
        assert "type: bug" in reports[0].read_text()
    finally:
        await orch.close()
