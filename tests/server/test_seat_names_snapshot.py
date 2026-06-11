"""Player-name enrichment in the ATTACHED snapshot.

The engine projection (``project_state``) is pure and deliberately carries no
player names — names are a server/registry concept. ``TableHandle``'s snapshot
provider splices the table-roster display name onto each projected seat so the
web client can label seats by player (and badge bots) rather than by wind+seat
alone. These fixtures pin that the enrichment reaches the wire:

- bot seats carry ``is_bot=True`` and ``name`` = their ``bot_id``;
- an occupied human seat carries ``is_bot=False`` and the player's display name.

Without the enrichment the ``name`` / ``is_bot`` keys are absent and these
assertions fail, so this is a real regression guard for the feature.
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

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
_SERVER_INFO: dict[str, Any] = {"version": "names-test", "git_sha": "test", "host": "test"}
_KNOWN_NAMES = ("Alice", "Bob", "Carol", "Dave")


def _named_identity_factory() -> Any:
    """Assign deterministic display names from ``_KNOWN_NAMES`` per connection.

    The default factory yields ``player-<suffix>`` which is non-deterministic;
    pinning known display strings lets the test assert a *real* name (not the
    wind+seat fallback) landed on the seat.
    """
    assigned: dict[int, HumanIdentity] = {}

    def factory(conn: Any) -> HumanIdentity:
        key = id(conn)
        if key not in assigned:
            name = _KNOWN_NAMES[len(assigned) % len(_KNOWN_NAMES)]
            assigned[key] = {
                "kind": "human",
                "user_id": f"u_{name.lower()}",
                "display": name,
            }
        return assigned[key]

    return factory


def _make_orch(tmp_path: Path) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=55_555,
        server_info=_SERVER_INFO,
        between_hand_pause_seconds=0.05,
        identity_factory=_named_identity_factory(),
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
    await ws.send(json.dumps({"kind": "ATTACH", "table_id": int(table_id), "seat": seat}))
    return cast(dict[str, Any], json.loads(cast(str, await ws.recv())))


def _seats_by_index(attached: dict[str, Any]) -> dict[int, dict[str, Any]]:
    assert attached["kind"] == "ATTACHED", attached
    return {s["seat"]: s for s in attached["snapshot"]["seats"]}


async def test_attached_snapshot_names_bots_by_bot_id(tmp_path: Path) -> None:
    """Bot seats in the snapshot are flagged ``is_bot`` and named by ``bot_id``."""
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            seats = _seats_by_index(await _attach(alice_ws, table_id, seat=0))

            for bot_seat in (2, 3):
                assert seats[bot_seat]["is_bot"] is True, seats[bot_seat]
                assert seats[bot_seat]["name"] == "v0", seats[bot_seat]
            # Alice's own human seat is flagged human.
            assert seats[0]["is_bot"] is False, seats[0]
    finally:
        await orch.close()


async def test_attached_snapshot_shows_other_humans_display_name(tmp_path: Path) -> None:
    """A second player's ATTACHED snapshot carries the first player's name.

    Alice attaches first, so her identity is recorded on the table roster
    before Bob attaches; Bob's snapshot must therefore show a real display
    name on seat 0 (not ``None`` and not the wind+seat fallback).
    """
    orch = _make_orch(tmp_path)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect(url) as alice_ws:
            table_id = await _create_2h2b(alice_ws)
            await _attach(alice_ws, table_id, seat=0)

            async with await _connect(url) as bob_ws:
                seats = _seats_by_index(await _attach(bob_ws, table_id, seat=1))
                assert seats[0]["is_bot"] is False, seats[0]
                assert seats[0]["name"] in _KNOWN_NAMES, seats[0]
    finally:
        await orch.close()
