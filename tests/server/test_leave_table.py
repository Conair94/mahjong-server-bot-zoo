"""FB-14 — leaving a table must return the connection to the lobby loop.

Regression for the "no way back to the main menu" trap: once a connection
attaches, the orchestrator's Phase 2 inbound loop forwards every frame to the
table and only exits on socket drop.  A client DETACH released the seat (the
mux acks DETACHED), but the connection stayed glued to the table — every
subsequent lobby message (LIST_TABLES, ATTACH, ...) came back as
``ERROR unknown_kind``.  Combined with the FB-03 auto-rejoin on refresh, a
player whose table hung (FB-13) had no escape at all.

Phase 2 now returns the connection to the Phase 1 lobby loop after the kind
that matches how it entered: DETACH for a seated connection, STOP_SPECTATING
for a spectator.  The mismatched kind must NOT escape — the mux no-ops it, and
breaking out anyway would leave a still-subscribed connection in the lobby.
"""

from __future__ import annotations

import asyncio
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
        seed=20_260_611,
        server_info={"version": "leave-test", "git_sha": "x", "host": "t"},
        between_hand_pause_seconds=0.05,
    )


async def _connect(url: str) -> Any:
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    hello = json.loads(cast(str, await ws.recv()))
    assert hello["kind"] == "HELLO", hello
    return ws


async def _create_1h3b(ws: Any) -> int:
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
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "TABLE_CREATED", resp
    return cast(int, resp["table_id"])


async def _attach(ws: Any, table_id: int, seat: int) -> dict[str, Any]:
    await ws.send(json.dumps({"kind": "ATTACH", "table_id": int(table_id), "seat": seat}))
    return cast(dict[str, Any], json.loads(cast(str, await ws.recv())))


async def _recv_until(ws: Any, kinds: set[str], *, timeout: float = 10.0) -> dict[str, Any]:
    """Receive messages, ignoring any whose kind is not in *kinds*."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {kinds}")
        msg = json.loads(cast(str, await asyncio.wait_for(ws.recv(), timeout=remaining)))
        if msg.get("kind") in kinds:
            return cast(dict[str, Any], msg)


async def test_detach_returns_connection_to_lobby(tmp_path: Path) -> None:
    """ATTACH → DETACH → the same connection can use the lobby again."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            table_id = await _create_1h3b(ws)
            attached = await _attach(ws, table_id, 0)
            assert attached["kind"] == "ATTACHED", attached

            await ws.send(json.dumps({"kind": "DETACH", "reason": "leaving"}))
            ack = await _recv_until(ws, {"DETACHED"})
            assert ack["kind"] == "DETACHED"

            # Back in the lobby loop: LIST_TABLES must answer TABLE_LIST,
            # not ERROR unknown_kind from the table mux.
            await ws.send(json.dumps({"kind": "LIST_TABLES"}))
            tables = await _recv_until(ws, {"TABLE_LIST", "ERROR"})
            assert tables["kind"] == "TABLE_LIST", tables

            # And the player can start over: re-ATTACH on the same connection.
            reattached = await _attach(ws, table_id, 0)
            assert reattached["kind"] == "ATTACHED", reattached
    finally:
        await orch.close()


async def test_detach_mid_prompt_escapes_to_lobby(tmp_path: Path) -> None:
    """The FB-13/FB-14 trap: a prompt is outstanding (worst case: forever).

    Leaving must work *during* a hand — the DETACH is dispatched by the
    connection read loop, independent of the (possibly wedged) hand task.
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            table_id = await _create_1h3b(ws)
            attached = await _attach(ws, table_id, 0)
            assert attached["kind"] == "ATTACHED", attached

            await ws.send(json.dumps({"kind": "START_HAND", "table_id": table_id}))
            prompt = await _recv_until(ws, {"PROMPT"})
            assert prompt["kind"] == "PROMPT"

            # Leave while the server is waiting on our decision.
            await ws.send(json.dumps({"kind": "DETACH", "reason": "leaving"}))
            ack = await _recv_until(ws, {"DETACHED"})
            assert ack["kind"] == "DETACHED"

            await ws.send(json.dumps({"kind": "LIST_TABLES"}))
            tables = await _recv_until(ws, {"TABLE_LIST", "ERROR"})
            assert tables["kind"] == "TABLE_LIST", tables
    finally:
        await orch.close()


async def test_stop_spectating_returns_connection_to_lobby(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as creator, await _connect(url) as spec:
            table_id = await _create_1h3b(creator)

            await spec.send(json.dumps({"kind": "SPECTATE", "table_id": table_id}))
            spectating = json.loads(cast(str, await spec.recv()))
            assert spectating["kind"] == "SPECTATING", spectating

            await spec.send(json.dumps({"kind": "STOP_SPECTATING"}))
            ack = await _recv_until(spec, {"DETACHED"})
            assert ack["kind"] == "DETACHED"

            await spec.send(json.dumps({"kind": "LIST_TABLES"}))
            tables = await _recv_until(spec, {"TABLE_LIST", "ERROR"})
            assert tables["kind"] == "TABLE_LIST", tables
    finally:
        await orch.close()


async def test_role_mismatched_kind_does_not_escape(tmp_path: Path) -> None:
    """A seated connection sending STOP_SPECTATING must stay in the game loop.

    The mux no-ops the mismatched kind; if the router broke out anyway the
    table would keep fanning events at a connection now sitting in the lobby.
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            table_id = await _create_1h3b(ws)
            attached = await _attach(ws, table_id, 0)
            assert attached["kind"] == "ATTACHED", attached

            await ws.send(json.dumps({"kind": "STOP_SPECTATING"}))
            # Still in Phase 2: a lobby message is answered by the table mux's
            # unknown_kind, proving the mismatched kind did not break out.
            await ws.send(json.dumps({"kind": "LIST_TABLES"}))
            reply = await _recv_until(ws, {"TABLE_LIST", "ERROR"})
            assert reply["kind"] == "ERROR" and reply.get("code") == "unknown_kind", reply
    finally:
        await orch.close()
