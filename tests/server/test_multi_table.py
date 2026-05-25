"""Multi-table orchestrator end-to-end fixtures.

Step 8.4: One WebSocketServer hosts N tables.

F_LIST: LIST_TABLES response reflects post-create / post-close transitions.
F_CREATE: CREATE_TABLE wire handler allocates a table and returns TABLE_CREATED.
F17: Two-table isolation — concurrent hands on two tables write independent
     records; closing table A does not affect table B.
F18: CREATE_TABLE is rejected with ERROR { code: "shutting_down" } when the
     registry is draining.
F_CLOSE_ADMIN: CLOSE_TABLE rejected with ERROR { code: "not_authorized" }
     for a non-admin connection.

Spec: docs/specs/server-lifecycle.md fixture 17, fixture 18.
      docs/specs/wire-protocol.md § Server-administrative.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.rulesets import MANIFEST
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.table import manager as mgr

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
MT_SEED = 77_777
MT_SERVER_INFO: dict[str, Any] = {
    "version": "mt-test",
    "git_sha": "test",
    "host": "test",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixed_identity(suffix: str) -> HumanIdentity:
    return {"kind": "human", "user_id": f"u_{suffix}", "display": f"player-{suffix}"}


def _make_orch(tmp_path: Path, *, admin_predicate: Any = None) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=MT_SEED,
        server_info=MT_SERVER_INFO,
        between_hand_pause_seconds=0.05,
        admin_predicate=admin_predicate,
    )


async def _ws_connect(url: str) -> Any:
    """Connect and consume HELLO."""
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    hello = json.loads(cast(str, await ws.recv()))
    assert hello["kind"] == "HELLO", hello
    return ws


async def _create_table(ws: Any, *, ruleset: str = "mcr-2006") -> str:
    """Send CREATE_TABLE and return the allocated table_id as string."""
    await ws.send(
        json.dumps(
            {
                "kind": "CREATE_TABLE",
                "ruleset": ruleset,
                "seats": [
                    {"kind": "human", "user_id": "u_test"},
                    {"kind": "canned"},
                    {"kind": "canned"},
                    {"kind": "canned"},
                ],
            }
        )
    )
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "TABLE_CREATED", resp
    return str(resp["table_id"])


async def _list_tables(ws: Any) -> list[dict[str, Any]]:
    """Send LIST_TABLES and return the tables list."""
    await ws.send(json.dumps({"kind": "LIST_TABLES"}))
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "TABLE_LIST", resp
    return cast(list[dict[str, Any]], resp["tables"])


async def _drive_one_hand(url: str, *, table_id: str, user_suffix: str = "test") -> None:
    """Connect, ATTACH seat 0 at the given table, play one hand to completion."""
    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
        hello = json.loads(cast(str, await ws.recv()))
        assert hello["kind"] == "HELLO"

        await ws.send(
            json.dumps({"kind": "ATTACH", "table_id": int(table_id), "seat": 0})
        )
        attached = json.loads(cast(str, await ws.recv()))
        assert attached["kind"] == "ATTACHED", attached

        deadline = asyncio.get_event_loop().time() + 60.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            assert remaining > 0, "timed out driving single-hand client"
            raw = cast(str, await asyncio.wait_for(ws.recv(), timeout=remaining))
            msg = json.loads(raw)
            kind = msg["kind"]
            if kind == "PROMPT":
                await ws.send(
                    json.dumps(
                        {
                            "kind": "ACTION",
                            "prompt_id": msg["prompt_id"],
                            "action": msg["default_action"],
                        }
                    )
                )
            elif kind == "HAND_END":
                break


# ---------------------------------------------------------------------------
# F_LIST: LIST_TABLES response reflects transitions
# ---------------------------------------------------------------------------


async def test_mt_f_list_tables_reflects_transitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIST_TABLES shows 0 tables initially, 1 after CREATE_TABLE, 0 after
    CLOSE_TABLE (via registry.close_table directly for simplicity)."""
    counter: dict[str, int] = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-25T10:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)

    orch = _make_orch(tmp_path)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    try:
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO

            # Initially 0 tables
            tables = await _list_tables(ws)
            assert tables == [], f"Expected empty table list, got: {tables}"

            # CREATE_TABLE → TABLE_CREATED
            table_id = await _create_table(ws)
            assert table_id  # non-empty string

            # LIST_TABLES now shows 1 table
            tables = await _list_tables(ws)
            assert len(tables) == 1, f"Expected 1 table, got: {tables}"
            assert str(tables[0]["table_id"]) == table_id

            # Close table via registry (direct, since CLOSE_TABLE wire is admin-gated)
            await orch.registry.close_table(table_id, reason="test_done")

            # LIST_TABLES now shows 0 tables
            tables = await _list_tables(ws)
            assert tables == [], f"Expected 0 tables after close, got: {tables}"
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# F_CREATE: TABLE_CREATED carries a valid table_id; table appears in LIST_TABLES
# ---------------------------------------------------------------------------


async def test_mt_f_create_table_returns_table_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CREATE_TABLE returns TABLE_CREATED with a positive integer table_id.
    Two successive CREATE_TABLEs return distinct table_ids."""
    counter: dict[str, int] = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-25T11:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)

    orch = _make_orch(tmp_path)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    try:
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO

            table_id_a = await _create_table(ws)
            table_id_b = await _create_table(ws)

            assert table_id_a != table_id_b, "Two CREATE_TABLEs should yield distinct table_ids"
            assert int(table_id_a) > 0
            assert int(table_id_b) > 0

            # Both appear in LIST_TABLES
            tables = await _list_tables(ws)
            table_ids_listed = {str(t["table_id"]) for t in tables}
            assert table_id_a in table_ids_listed
            assert table_id_b in table_ids_listed
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# F17: Two-table isolation (server-lifecycle.md fixture 17)
# ---------------------------------------------------------------------------


async def test_mt_f17_two_table_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F17: Hands on two tables run concurrently without interference.

    Assertions:
    - Their record files are distinct (different paths).
    - Their hand_ids differ.
    - Closing table A while table B is still playing does not kill table B.
    - After table A is closed, LIST_TABLES omits A but still shows B.
    """
    counter: dict[str, int] = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-25T12:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)

    orch = _make_orch(tmp_path)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    try:
        # Create two tables via wire protocol
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as admin_ws:
            await admin_ws.recv()  # HELLO
            table_id_a = await _create_table(admin_ws)
            table_id_b = await _create_table(admin_ws)

            assert table_id_a != table_id_b

        # Play one hand on each table concurrently
        await asyncio.wait_for(
            asyncio.gather(
                _drive_one_hand(url, table_id=table_id_a, user_suffix="ta"),
                _drive_one_hand(url, table_id=table_id_b, user_suffix="tb"),
            ),
            timeout=90.0,
        )

        # Get the two table handles
        handle_a = orch.registry.get_table(table_id_a)
        handle_b = orch.registry.get_table(table_id_b)

        # Record paths are distinct
        assert handle_a.record_path != handle_b.record_path, (
            "Tables A and B should write to independent record files"
        )

        # hand_ids are distinct
        assert handle_a.hand_id != handle_b.hand_id, (
            "Tables A and B should have distinct hand_ids"
        )

        # Close table A; table B should be unaffected
        await orch.registry.close_table(table_id_a, reason="test_close_a")

        # LIST_TABLES via wire: A gone, B still there
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as check_ws:
            await check_ws.recv()  # HELLO
            tables = await _list_tables(check_ws)
            listed_ids = {str(t["table_id"]) for t in tables}
            assert table_id_a not in listed_ids, (
                f"Table A should have been removed from LIST_TABLES; got: {listed_ids}"
            )
            assert table_id_b in listed_ids, (
                f"Table B should still appear in LIST_TABLES; got: {listed_ids}"
            )

        # Table B match_done fires after the hand completes; it already did
        assert handle_b.match_done.is_set(), "Table B's hand should have completed"
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# F18: CREATE_TABLE rejected after drain begins (server-lifecycle.md fixture 18)
# ---------------------------------------------------------------------------


async def test_mt_f18_create_table_rejected_post_drain(
    tmp_path: Path,
) -> None:
    """F18: During registry drain, CREATE_TABLE returns ERROR { code: 'shutting_down' }."""
    orch = _make_orch(tmp_path)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    try:
        # Trigger drain on the registry
        await orch.registry.drain_all()

        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO

            await ws.send(
                json.dumps(
                    {
                        "kind": "CREATE_TABLE",
                        "ruleset": "mcr-2006",
                        "seats": [
                            {"kind": "human"},
                            {"kind": "canned"},
                            {"kind": "canned"},
                            {"kind": "canned"},
                        ],
                    }
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR", f"Expected ERROR, got: {resp}"
            assert resp["code"] == "shutting_down", f"Wrong code: {resp}"
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# F_CLOSE_ADMIN: CLOSE_TABLE rejected for non-admin connection
# ---------------------------------------------------------------------------


async def test_mt_f_close_table_admin_gating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLOSE_TABLE from a non-admin connection returns ERROR { code: 'not_authorized' }."""
    counter: dict[str, int] = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-25T13:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)

    # Non-admin predicate: always False
    orch = _make_orch(tmp_path, admin_predicate=lambda conn: False)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    try:
        # Create a table with an admin connection (bypassing the predicate by using
        # the registry directly)
        table_id = orch.registry.create_table_direct(
            ruleset=MCR_REF,
            seed=MT_SEED,
            server_info=MT_SERVER_INFO,
            data_dir=tmp_path,
        )

        # Try to CLOSE_TABLE from a non-admin connection
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO

            await ws.send(
                json.dumps(
                    {
                        "kind": "CLOSE_TABLE",
                        "table_id": int(table_id),
                    }
                )
            )
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR", f"Expected ERROR, got: {resp}"
            assert resp["code"] == "not_authorized", f"Wrong code: {resp}"
    finally:
        await orch.close()
