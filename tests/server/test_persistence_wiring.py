"""Step 8.5 — Persistence wiring into TableHandle.

When TableRegistry is constructed with a Persistence, every hand played at
every table writes a ``hand_index`` row + ``hand_participants`` rows at HEADER
time and finalises them at FOOTER time.  ``find_hands_by_account`` then
returns the hand for the human seat's account.

Fixtures here back server-lifecycle.md fixture 22 (S3 exit gate).
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
from mahjong.table import manager as mgr

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
SEED = 12_345
SERVER_INFO: dict[str, Any] = {
    "version": "persist-test",
    "git_sha": "test",
    "host": "test",
}


def _fixed_ts(counter: dict[str, int]):
    def make() -> str:
        counter["i"] += 1
        return f"2026-05-25T20:00:00.{counter['i']:03d}Z"

    return make


async def _auth(ws: Any, *, username: str, password: str) -> dict[str, Any]:
    await ws.send(
        json.dumps(
            {"kind": "AUTH_REQUEST", "username": username, "password": password}
        )
    )
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "AUTH_RESPONSE" and resp.get("ok"), resp
    return cast(dict[str, Any], resp)


async def _drive_one_hand_at(
    url: str, table_id: str, *, username: str, password: str
) -> None:
    """ATTACH seat 0 with a fixed user_id and play the hand to HAND_END."""
    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
        hello = json.loads(cast(str, await ws.recv()))
        assert hello["kind"] == "HELLO"
        await _auth(ws, username=username, password=password)

        await ws.send(
            json.dumps({"kind": "ATTACH", "table_id": int(table_id), "seat": 0})
        )
        attached = json.loads(cast(str, await ws.recv()))
        assert attached["kind"] == "ATTACHED", attached

        # Step 8.7.d: explicit START_HAND now drives the hand loop.
        await ws.send(
            json.dumps({"kind": "START_HAND", "table_id": int(table_id)})
        )

        deadline = asyncio.get_event_loop().time() + 60.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            assert remaining > 0
            msg = json.loads(
                cast(str, await asyncio.wait_for(ws.recv(), timeout=remaining))
            )
            if msg["kind"] == "PROMPT":
                await ws.send(
                    json.dumps(
                        {
                            "kind": "ACTION",
                            "prompt_id": msg["prompt_id"],
                            "action": msg["default_action"],
                        }
                    )
                )
            elif msg["kind"] == "HAND_END":
                return


async def test_persistence_wiring_records_hand_for_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Play one hand → persistence row exists, finalized, owned by the account."""
    monkeypatch.setattr(mgr, "_now_ts", _fixed_ts({"i": 0}))

    (tmp_path / "records").mkdir(exist_ok=True)
    persistence = Persistence(tmp_path / "mahjong.db", tmp_path)

    account_id = create_account(
        persistence._conn,  # type: ignore[attr-defined]
        username="alice",
        display_name="Alice",
        kind="human",
        role="admin",  # admin so CREATE_TABLE / CLOSE_TABLE work in tests
        password="alicealice",
    )

    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=SEED,
        server_info=SERVER_INFO,
        between_hand_pause_seconds=0.05,
        persistence=persistence,
    )
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    try:
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as admin_ws:
            await admin_ws.recv()  # HELLO
            await _auth(admin_ws, username="alice", password="alicealice")
            await admin_ws.send(
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
            created = json.loads(cast(str, await admin_ws.recv()))
            assert created["kind"] == "TABLE_CREATED"
            table_id = str(created["table_id"])

        await asyncio.wait_for(
            _drive_one_hand_at(
                url, table_id, username="alice", password="alicealice"
            ),
            timeout=90.0,
        )

        # Allow the finally-block finalize to commit.
        await asyncio.sleep(0.1)

        hands = persistence.find_hands_by_account(account_id)
        assert len(hands) == 1, f"Expected 1 hand for account, got {hands}"
        row = hands[0]
        assert row.terminal_kind in {"HU", "EXHAUSTIVE_DRAW"}, row
        assert row.ended_at_ms is not None
        assert row.record_checksum and row.record_checksum.startswith("sha256:")
        assert row.ruleset_id == "mcr-2006"
        assert row.source == "live"

        full = persistence.get_hand(row.hand_id)
        assert full is not None
        # Human seat 0 has account_id; canned seats 1-3 do not.
        seat0 = next(p for p in full.participants if p.seat == 0)
        assert seat0.account_id == account_id
        assert seat0.seat_kind == "human"
        for seat in (1, 2, 3):
            other = next(p for p in full.participants if p.seat == seat)
            assert other.account_id is None
            assert other.seat_kind == "canned"
    finally:
        await orch.close()
        persistence.close()
