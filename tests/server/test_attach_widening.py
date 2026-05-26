"""Step 8.7.b — Attach widening for multi-human-seat tables.

Verification fixtures 8-11 from
``docs/specs/multi-human-seats.md § Verification fixtures``:

8.  Two humans attach to seats 0 and 1 of a 2H+2B table; both ATTACHED.
9.  Bot seat (seat 2 of a 2H+2B table) rejects attach → ERROR seat_not_yours.
10. Occupied human seat rejects different user → ERROR seat_occupied.
11. Same-user reconnect to held seat — succeeds via existing takeover path.
    (Regression check for session-mux behavior under multi-human composition.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.rulesets import MANIFEST
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.server.seats import SeatComposition
from mahjong.wire.server import Connection

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
_AW_SEED = 99_999
_AW_SERVER_INFO: dict[str, Any] = {
    "version": "aw-test",
    "git_sha": "test",
    "host": "test",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orch(tmp_path: Path, *, identity_factory: Any = None) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=_AW_SEED,
        server_info=_AW_SERVER_INFO,
        between_hand_pause_seconds=0.05,
        identity_factory=identity_factory,
    )


def _stable_identity_factory(user_id: str, display: str) -> Any:
    def _factory(_conn: Connection) -> HumanIdentity:
        return {"kind": "human", "user_id": user_id, "display": display}

    return _factory


async def _connect(url: str) -> Any:
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    hello = json.loads(cast(str, await ws.recv()))
    assert hello["kind"] == "HELLO", hello
    return ws


async def _create_table_with(ws: Any, seats: list[dict[str, str]]) -> int:
    await ws.send(json.dumps({"kind": "CREATE_TABLE", "ruleset": "mcr-2006", "seats": seats}))
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "TABLE_CREATED", resp
    return cast(int, resp["table_id"])


async def _attach(ws: Any, table_id: int, seat: int) -> dict[str, Any]:
    await ws.send(json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": seat}))
    return cast(dict[str, Any], json.loads(cast(str, await ws.recv())))


# ---------------------------------------------------------------------------
# Fixture 8 — Two humans attach to a 2H+2B table
# ---------------------------------------------------------------------------


async def test_fixture_8_two_humans_attach_to_2h2b_table(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_table_with(
                alice_ws,
                seats=[
                    {"kind": "human"},
                    {"kind": "human"},
                    {"kind": "bot"},
                    {"kind": "bot"},
                ],
            )
            alice_resp = await _attach(alice_ws, table_id, seat=0)
            assert alice_resp["kind"] == "ATTACHED", alice_resp
            assert alice_resp["seat"] == 0

            # Bob connects with a *different* user_id (default factory uses
            # connection_id, which differs between sockets).
            async with await _connect(url) as bob_ws:
                bob_resp = await _attach(bob_ws, table_id, seat=1)
                assert bob_resp["kind"] == "ATTACHED", bob_resp
                assert bob_resp["seat"] == 1

                # Both sessions independent: session-mux exposes user_id per seat.
                handle = orch.registry.get_table(str(table_id))
                seat0_user = handle.sessions.seat(0).user_id
                seat1_user = handle.sessions.seat(1).user_id
                assert seat0_user is not None
                assert seat1_user is not None
                assert seat0_user != seat1_user
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 9 — Bot seat rejects attach
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bot_seat", [2, 3])
async def test_fixture_9_bot_seat_rejects_attach(tmp_path: Path, bot_seat: int) -> None:
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            table_id = await _create_table_with(
                ws,
                seats=[
                    {"kind": "human"},
                    {"kind": "human"},
                    {"kind": "bot"},
                    {"kind": "bot"},
                ],
            )
            resp = await _attach(ws, table_id, seat=bot_seat)
            assert resp["kind"] == "ERROR", resp
            assert resp["code"] == "seat_not_yours", resp
            # The session-mux for that seat must remain UNBOUND.
            handle = orch.registry.get_table(str(table_id))
            assert handle.sessions.seat(bot_seat).user_id is None
    finally:
        await orch.close()


@pytest.mark.parametrize("bot_seat", [1, 2, 3])
async def test_fixture_9_default_composition_bot_seats_rejected(
    tmp_path: Path, bot_seat: int
) -> None:
    """Default (1H+3B): seats 1, 2, 3 are bot seats and must reject attach.

    Each ATTACH gets its own connection because the orchestrator closes the
    socket after a failed ATTACH per the wire protocol.
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as setup_ws:
            await setup_ws.send(json.dumps({"kind": "CREATE_TABLE", "ruleset": "mcr-2006"}))
            created = json.loads(cast(str, await setup_ws.recv()))
            table_id = created["table_id"]
        async with await _connect(url) as attach_ws:
            resp = await _attach(attach_ws, table_id, seat=bot_seat)
            assert resp["kind"] == "ERROR", resp
            assert resp["code"] == "seat_not_yours", resp
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 10 — Occupied human seat rejects different user
# ---------------------------------------------------------------------------


async def test_fixture_10_occupied_seat_rejects_different_user(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_table_with(
                alice_ws,
                seats=[
                    {"kind": "human"},
                    {"kind": "human"},
                    {"kind": "bot"},
                    {"kind": "bot"},
                ],
            )
            alice_resp = await _attach(alice_ws, table_id, seat=0)
            assert alice_resp["kind"] == "ATTACHED"

            # Bob (different connection → different user_id under default
            # factory) tries to take seat 0.
            async with await _connect(url) as bob_ws:
                resp = await _attach(bob_ws, table_id, seat=0)
                assert resp["kind"] == "ERROR", resp
                assert resp["code"] == "seat_occupied", resp
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 11 — Same-user reconnect to held seat
# ---------------------------------------------------------------------------


async def test_fixture_11_same_user_reconnect_to_held_seat(tmp_path: Path) -> None:
    """Alice attaches, drops, reconnects with the same user_id → re-ATTACHED.

    Uses a stable identity factory so both sockets present as the same user.
    """
    orch = _make_orch(
        tmp_path,
        identity_factory=_stable_identity_factory("u_alice", "Alice"),
    )
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        # First connection: create + attach.
        ws1 = await _connect(url)
        table_id = await _create_table_with(
            ws1,
            seats=[
                {"kind": "human"},
                {"kind": "human"},
                {"kind": "bot"},
                {"kind": "bot"},
            ],
        )
        resp1 = await _attach(ws1, table_id, seat=0)
        assert resp1["kind"] == "ATTACHED"

        # Drop the first socket; session-mux transitions seat to HELD.
        await ws1.close()

        # Second connection (same user_id via stable factory) reconnects.
        ws2 = await _connect(url)
        try:
            resp2 = await _attach(ws2, table_id, seat=0)
            assert resp2["kind"] == "ATTACHED", resp2
            assert resp2["seat"] == 0
            # User identity preserved across the reconnect.
            handle = orch.registry.get_table(str(table_id))
            assert handle.sessions.seat(0).user_id == "u_alice"
        finally:
            await ws2.close()
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Default-composition regression: single human can still attach to seat 0
# (8.7.b widens permissions; must not regress the single-human path).
# ---------------------------------------------------------------------------


async def test_default_composition_human_attaches_to_seat_zero(tmp_path: Path) -> None:
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as ws:
            await ws.send(json.dumps({"kind": "CREATE_TABLE", "ruleset": "mcr-2006"}))
            created = json.loads(cast(str, await ws.recv()))
            table_id = created["table_id"]
            resp = await _attach(ws, table_id, seat=0)
            assert resp["kind"] == "ATTACHED", resp
            handle = orch.registry.get_table(str(table_id))
            # Composition: seat 0 is human, 1-3 bot
            assert handle.seats[0] == SeatComposition("human")
            assert handle.seats[1] == SeatComposition("bot")
            assert handle.is_human_seat(0) is True
            assert handle.is_human_seat(1) is False
    finally:
        await orch.close()
