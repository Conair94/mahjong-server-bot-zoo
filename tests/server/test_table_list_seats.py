"""Step 8.7.c — TABLE_LIST.seats[] population.

Verification fixtures 12-14 from
``docs/specs/multi-human-seats.md § Verification fixtures``:

12. Empty 2H+2B table snapshot: human seats occupied=false; bot seats
    occupied=true with bot_id="canned-pass".
13. Mid-fill snapshot: one human attached → that seat occupied=true,
    user_id populated; other human seat still occupied=false.
14. Phase transitions: WAITING_FOR_PLAYERS before the hand loop starts;
    IN_PROGRESS after.  (8.7.b ignites the loop on first attach; 8.7.d
    will move the trigger to an explicit START_HAND.)
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

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
_TL_SEED = 33_333
_TL_SERVER_INFO: dict[str, Any] = {
    "version": "tl-test",
    "git_sha": "test",
    "host": "test",
}


def _make_orch(tmp_path: Path) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=_TL_SEED,
        server_info=_TL_SERVER_INFO,
        between_hand_pause_seconds=0.05,
    )


async def _connect(url: str) -> Any:
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    hello = json.loads(cast(str, await ws.recv()))
    assert hello["kind"] == "HELLO", hello
    return ws


async def _create_2h2b(ws: Any) -> int:
    await ws.send(
        json.dumps(
            {
                "kind": "CREATE_TABLE",
                "ruleset": "mcr-2006",
                "seats": [
                    {"kind": "human"},
                    {"kind": "human"},
                    {"kind": "bot"},
                    {"kind": "bot"},
                ],
            }
        )
    )
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "TABLE_CREATED", resp
    return cast(int, resp["table_id"])


async def _list_tables(ws: Any) -> list[dict[str, Any]]:
    await ws.send(json.dumps({"kind": "LIST_TABLES"}))
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "TABLE_LIST", resp
    return cast(list[dict[str, Any]], resp["tables"])


# ---------------------------------------------------------------------------
# Fixture 12 — Empty table snapshot
# ---------------------------------------------------------------------------


async def test_fixture_12_empty_2h2b_snapshot(tmp_path: Path) -> None:
    """Fresh 2H+2B table, no attaches yet → seats reflect composition.

    Bot seats are occupied (server-owned CannedAdapter); human seats are
    unoccupied.
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            table_id = await _create_2h2b(ws)
            tables = await _list_tables(ws)
            assert len(tables) == 1
            table = tables[0]
            assert table["table_id"] == table_id
            assert table["seats"] == [
                {"seat": 0, "kind": "human", "occupied": False},
                {"seat": 1, "kind": "human", "occupied": False},
                {"seat": 2, "kind": "bot", "occupied": True, "bot_id": "canned-pass"},
                {"seat": 3, "kind": "bot", "occupied": True, "bot_id": "canned-pass"},
            ]
    finally:
        await orch.close()


async def test_fixture_12_default_composition_snapshot(tmp_path: Path) -> None:
    """Default (1H+3B) table: seat 0 unoccupied human; 1-3 occupied bots."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            await ws.send(json.dumps({"kind": "CREATE_TABLE", "ruleset": "mcr-2006"}))
            await ws.recv()  # TABLE_CREATED
            tables = await _list_tables(ws)
            assert tables[0]["seats"] == [
                {"seat": 0, "kind": "human", "occupied": False},
                {"seat": 1, "kind": "bot", "occupied": True, "bot_id": "canned-pass"},
                {"seat": 2, "kind": "bot", "occupied": True, "bot_id": "canned-pass"},
                {"seat": 3, "kind": "bot", "occupied": True, "bot_id": "canned-pass"},
            ]
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 13 — Mid-fill snapshot
# ---------------------------------------------------------------------------


async def test_fixture_13_one_human_attached(tmp_path: Path) -> None:
    """Alice attaches to seat 0; LIST_TABLES from a second observer shows
    seat 0 occupied with user_id, seat 1 still open."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            await alice_ws.send(json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 0}))
            attached = json.loads(cast(str, await alice_ws.recv()))
            assert attached["kind"] == "ATTACHED"
            alice_user_id = cast(str, attached.get("user_id")) or (
                orch.registry.get_table(str(table_id)).sessions.seat(0).user_id
            )
            assert alice_user_id is not None

            # Observer connection lists tables.
            async with await _connect(url) as observer_ws:
                tables = await _list_tables(observer_ws)
                seats = tables[0]["seats"]
                assert seats[0] == {
                    "seat": 0,
                    "kind": "human",
                    "occupied": True,
                    "user_id": alice_user_id,
                }
                assert seats[1] == {
                    "seat": 1,
                    "kind": "human",
                    "occupied": False,
                }
                assert seats[2]["kind"] == "bot"
                assert seats[2]["occupied"] is True
                assert seats[2]["bot_id"] == "canned-pass"
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 14 — Phase transitions
# ---------------------------------------------------------------------------


async def test_fixture_14_phase_waiting_before_attach(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            await _create_2h2b(ws)
            tables = await _list_tables(ws)
            assert tables[0]["phase"] == "WAITING_FOR_PLAYERS"
    finally:
        await orch.close()


async def test_fixture_14_phase_in_progress_after_hand_starts(tmp_path: Path) -> None:
    """In 8.7.b the hand task starts on first attach.  Once it's running, the
    summary reports IN_PROGRESS.  (After 8.7.d the trigger moves to START_HAND
    and this fixture is re-verified through that path.)"""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            await alice_ws.send(json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 0}))
            attached = json.loads(cast(str, await alice_ws.recv()))
            assert attached["kind"] == "ATTACHED"

            # Hand task is created in attach() but the asyncio loop hasn't
            # necessarily scheduled it yet; yield once to let it run.
            await asyncio.sleep(0)

            async with await _connect(url) as observer_ws:
                tables = await _list_tables(observer_ws)
                assert tables[0]["phase"] == "IN_PROGRESS"
    finally:
        await orch.close()
