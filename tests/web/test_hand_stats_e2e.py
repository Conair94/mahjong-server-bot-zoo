"""Spec 37 composition-root check: a real `WebOrchestrator` PROMPT carries
a well-formed `stats` payload (the adapter-level tests use a fake provider;
this pins the actual `analysis.stats_for_prompt` wiring end-to-end)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, cast

import pytest
import websockets

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.rulesets import MANIFEST
from mahjong.web.server import WebOrchestrator
from mahjong.wire.server import Connection

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _identity(_conn: Connection) -> HumanIdentity:
    return {"kind": "human", "user_id": "u_test", "display": "Tester"}


async def test_real_prompt_carries_stats(tmp_path: Any) -> None:
    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info={"version": "test", "git_sha": "x", "host": "test"},
        identity_factory=_identity,
        canned_seat_actions={1: [], 2: [], 3: []},
    )
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            hello = json.loads(cast(str, await ws.recv()))
            assert hello["kind"] == "HELLO"
            await ws.send(json.dumps({"kind": "ATTACH", "table_id": 1, "seat": 0}))

            prompt: dict[str, Any] | None = None
            async with asyncio.timeout(10.0):
                while prompt is None:
                    msg = json.loads(cast(str, await ws.recv()))
                    if msg["kind"] == "PROMPT":
                        prompt = msg

            stats = prompt.get("stats")
            assert stats is not None, "real PROMPT must carry Spec 37 stats"
            assert stats["floor"] == 8  # mcr-2006 cliff
            assert isinstance(stats["wall_remaining"], int)
            if prompt["phase"] == "DISCARD":
                rows = stats["discards"]
                assert rows and all({"tile", "shanten", "tiles"} <= set(r) for r in rows)
                # Sorted: best candidate first.
                assert rows[0]["shanten"] == min(r["shanten"] for r in rows)
            else:
                assert "hand" in stats
    finally:
        with contextlib.suppress(Exception):
            await orch.close()
