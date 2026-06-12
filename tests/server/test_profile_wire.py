"""GET_PROFILE / PROFILE wired into the multi-table orchestrator.

Spec: docs/specs/profile-and-settings.md § B.4 (verification fixtures 9-10).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.persistence.models import Participant
from mahjong.server.orchestrator import MultiTableOrchestrator

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _orch(
    tmp_path: Path, persistence: Persistence, *, require_auth: bool | None = None
) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "profile-test", "git_sha": "x", "host": "t"},
        between_hand_pause_seconds=0.05,
        persistence=persistence,
        require_auth=require_auth,
    )


def _seed_finalized_win(
    p: Persistence, account_id: int, *, hand_id: str, started_at_ms: int
) -> None:
    """One finalized live hand where *account_id* (seat 0) wins."""
    participants = [
        Participant(
            seat=s,
            account_id=account_id if s == 0 else None,
            seat_kind="human" if s == 0 else "canned",
            wind=f"F{s + 1}",
            final_score_delta=None,
        )
        for s in range(4)
    ]
    p.reserve_hand(
        hand_id=hand_id,
        match_id=None,
        hand_index_in_match=0,
        ruleset_id="mcr-2006",
        ruleset_config_hash="abc123",
        started_at_ms=started_at_ms,
        master_seed="0x1",
        record_path=f"records/{hand_id}.jsonl",
        server_version="profile-test",
        source="live",
        participants=participants,
    )
    p.finalize_hand(
        hand_id,
        ended_at_ms=started_at_ms + 60_000,
        terminal_kind="HU",
        winner_seat=0,
        fan_total=8,
        record_checksum="cs",
        participants_scores={0: 24, 1: -8, 2: -8, 3: -8},
    )


async def test_get_profile_returns_stats_for_authed_account(tmp_path: Path) -> None:
    """Fixture 9: authed GET_PROFILE → PROFILE for the right account, with
    stats reflecting the seeded hands."""
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    account_id = create_account(
        p._conn,  # type: ignore[attr-defined]
        username="connor",
        display_name="Connor",
        kind="human",
        role="user",
        password="connorpw12",
    )
    _seed_finalized_win(p, account_id, hand_id="h1", started_at_ms=1000)
    _seed_finalized_win(p, account_id, hand_id="h2", started_at_ms=2000)

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            hello = json.loads(cast(str, await ws.recv()))
            assert "profile" in hello.get("features", [])
            await ws.send(
                json.dumps(
                    {"kind": "AUTH_REQUEST", "username": "connor", "password": "connorpw12"}
                )
            )
            auth = json.loads(cast(str, await ws.recv()))
            assert auth["ok"] is True

            await ws.send(json.dumps({"kind": "GET_PROFILE"}))
            prof = json.loads(cast(str, await ws.recv()))

            assert prof["kind"] == "PROFILE"
            assert prof["account"]["account_id"] == account_id
            assert prof["account"]["display_name"] == "Connor"
            assert prof["stats"]["hands_played"] == 2
            assert prof["stats"]["hands_won"] == 2
            assert prof["stats"]["total_score"] == 48
            assert len(prof["recent"]) == 2
            assert prof["recent"][0]["won"] is True
            assert prof["recent"][0]["seat"] == 0
            # series is cumulative, ascending by time
            cums = [pt["cumulative"] for pt in prof["series"]]
            assert cums == [24, 48]
            # Spec 39: achievements ride the same PROFILE frame, derive-at-read.
            achievements = {a["id"]: a for a in prof["achievements"]}
            assert achievements["first-win"]["earned"] is True
            assert achievements["streak-3"]["progress"] == 2  # two straight wins
            assert achievements["wins-10"] == {
                **achievements["wins-10"], "earned": False, "progress": 2, "target": 10,
            }
    finally:
        await orch.close()
        p.close()


async def test_get_profile_without_auth_is_refused(tmp_path: Path) -> None:
    """Fixture 10: no authenticated identity → ERROR not_authenticated, and the
    connection stays open (the server does not hang up)."""
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)

    # require_auth=False reaches the admin loop without an auth identity, so the
    # GET_PROFILE guard is the thing under test.
    orch = _orch(tmp_path, p, require_auth=False)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            await ws.recv()  # HELLO
            await ws.send(json.dumps({"kind": "GET_PROFILE"}))
            resp = json.loads(cast(str, await ws.recv()))
            assert resp["kind"] == "ERROR"
            assert resp["code"] == "not_authenticated"

            # Connection still usable: LIST_TABLES still answered.
            await ws.send(json.dumps({"kind": "LIST_TABLES"}))
            tl = json.loads(cast(str, await ws.recv()))
            assert tl["kind"] == "TABLE_LIST"
    finally:
        await orch.close()
        p.close()
