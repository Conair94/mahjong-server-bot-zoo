"""Step 8.7.d — START_HAND wire handler.

Verification fixtures 15-18 from
``docs/specs/multi-human-seats.md § Verification fixtures``:

15. Happy path: 2H+2B, both humans attached, either human's START_HAND
    kicks off the hand loop; the originator receives the first ``EVENT``
    as wire confirmation.
16. Premature start: only one human attached → ``humans_not_ready`` with
    a count of still-unoccupied human seats.
17. Non-human (spectator) START_HAND → ``not_authorized``.
18. Double-start idempotency: first START_HAND wins; second returns
    ``hand_already_started``; the hand loop runs exactly once.
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
_SH_SEED = 44_444
_SH_SERVER_INFO: dict[str, Any] = {
    "version": "sh-test",
    "git_sha": "test",
    "host": "test",
}


def _make_orch(tmp_path: Path) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=_SH_SEED,
        server_info=_SH_SERVER_INFO,
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


async def _attach(ws: Any, table_id: int, seat: int) -> dict[str, Any]:
    await ws.send(
        json.dumps({"kind": "ATTACH", "table_id": int(table_id), "seat": seat})
    )
    return cast(dict[str, Any], json.loads(cast(str, await ws.recv())))


async def _send_start_hand(ws: Any, table_id: int) -> None:
    await ws.send(
        json.dumps({"kind": "START_HAND", "table_id": int(table_id)})
    )


async def _recv_until(
    ws: Any, kinds: set[str], *, timeout: float = 10.0
) -> dict[str, Any]:
    """Receive messages, ignoring any whose kind is not in *kinds*."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {kinds}")
        msg = json.loads(
            cast(str, await asyncio.wait_for(ws.recv(), timeout=remaining))
        )
        if msg.get("kind") in kinds:
            return cast(dict[str, Any], msg)


# ---------------------------------------------------------------------------
# Fixture 15 — Happy path
# ---------------------------------------------------------------------------


async def test_fixture_15_happy_path_start_hand_kicks_off_loop(
    tmp_path: Path,
) -> None:
    """2H+2B; both humans attached; alice's START_HAND ignites the loop.

    The wire confirmation is the first hand-loop frame delivered to alice
    — typically a ``PROMPT`` for her turn, since seat 0 is the dealer and
    the ``ATTACHED`` snapshot already covered the DEAL state.  Before
    START_HAND the table is in WAITING_FOR_PLAYERS; after, IN_PROGRESS.
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            alice_resp = await _attach(alice_ws, table_id, seat=0)
            assert alice_resp["kind"] == "ATTACHED", alice_resp

            async with await _connect(url) as bob_ws:
                bob_resp = await _attach(bob_ws, table_id, seat=1)
                assert bob_resp["kind"] == "ATTACHED", bob_resp

                # Pre-START_HAND: phase must still be WAITING_FOR_PLAYERS.
                handle = orch.registry.get_table(str(table_id))
                assert handle.summary().phase == "WAITING_FOR_PLAYERS"
                assert handle._hand_task is None

                await _send_start_hand(alice_ws, table_id)

                # Alice receives the first hand-loop frame as wire confirmation.
                evt = await _recv_until(alice_ws, {"EVENT", "PROMPT"})
                assert evt["kind"] in {"EVENT", "PROMPT"}, evt

                # Hand task is now alive.
                assert handle._hand_task is not None
                assert handle.summary().phase == "IN_PROGRESS"
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 16 — Premature start rejection
# ---------------------------------------------------------------------------


async def test_fixture_16_premature_start_humans_not_ready(tmp_path: Path) -> None:
    """2H+2B; only alice attached; her START_HAND → humans_not_ready.

    Message must surface the count of still-unoccupied human seats.
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            alice_resp = await _attach(alice_ws, table_id, seat=0)
            assert alice_resp["kind"] == "ATTACHED", alice_resp

            await _send_start_hand(alice_ws, table_id)
            err = await _recv_until(alice_ws, {"ERROR"})
            assert err["code"] == "humans_not_ready", err
            assert "1" in (err.get("message") or ""), err

            # Hand task must NOT have been created.
            handle = orch.registry.get_table(str(table_id))
            assert handle._hand_task is None
            assert handle.summary().phase == "WAITING_FOR_PLAYERS"
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 17 — Non-human (spectator) rejection
# ---------------------------------------------------------------------------


async def test_fixture_17_spectator_start_hand_not_authorized(
    tmp_path: Path,
) -> None:
    """A SPECTATE connection sends START_HAND → ERROR not_authorized."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            await _attach(alice_ws, table_id, seat=0)

            async with await _connect(url) as spec_ws:
                await spec_ws.send(
                    json.dumps(
                        {"kind": "SPECTATE", "table_id": int(table_id)}
                    )
                )
                resp = json.loads(cast(str, await spec_ws.recv()))
                assert resp["kind"] == "SPECTATING", resp

                await _send_start_hand(spec_ws, table_id)
                err = await _recv_until(spec_ws, {"ERROR"})
                assert err["code"] == "not_authorized", err

                # Hand task must NOT have started.
                handle = orch.registry.get_table(str(table_id))
                assert handle._hand_task is None
    finally:
        await orch.close()


# ---------------------------------------------------------------------------
# Fixture 18 — Double-start idempotency
# ---------------------------------------------------------------------------


async def test_fixture_18_double_start_returns_hand_already_started(
    tmp_path: Path,
) -> None:
    """Alice's START_HAND wins; Bob's subsequent START_HAND → hand_already_started.

    The hand loop runs exactly once (one ``_hand_task`` on the handle).
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            await _attach(alice_ws, table_id, seat=0)

            async with await _connect(url) as bob_ws:
                await _attach(bob_ws, table_id, seat=1)

                # Alice starts; wait until the loop is alive.
                await _send_start_hand(alice_ws, table_id)
                await _recv_until(alice_ws, {"EVENT", "PROMPT"})

                handle = orch.registry.get_table(str(table_id))
                task_before = handle._hand_task
                assert task_before is not None

                # Bob's redundant START_HAND → hand_already_started; the
                # hand task identity must be unchanged.
                await _send_start_hand(bob_ws, table_id)
                err = await _recv_until(bob_ws, {"ERROR"})
                assert err["code"] == "hand_already_started", err

                task_after = handle._hand_task
                assert task_after is task_before
    finally:
        await orch.close()
