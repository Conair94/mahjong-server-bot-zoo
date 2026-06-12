"""GET_HISTORY / GET_REPLAY wired into the multi-table orchestrator (FB-04).

Spec: docs/specs/account-records-replay.md § Verification fixtures (3,4,5,6,9).

Pins the server seam: the keyset-paginated history list, and replay
authorization (participant → own seat; admin → public view; everyone else
refused) + the record-integrity guard.
"""

from __future__ import annotations

import json
import shutil
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

MCR_REF: dict[str, Any] = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
_FIXTURE_RECORD = Path("tests/_fixtures/s2_e2e_record.jsonl")


def _orch(tmp_path: Path, persistence: Persistence) -> MultiTableOrchestrator:
    return MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=1,
        server_info={"version": "hr-test", "git_sha": "x", "host": "t"},
        between_hand_pause_seconds=0.05,
        persistence=persistence,
    )


def _seed_hand(
    p: Persistence,
    *,
    hand_id: str,
    seat0_account: int | None,
    started_at_ms: int,
    record_path: str,
) -> None:
    """One finalized live hand; *seat0_account* (or None) sits at seat 0, wins."""
    participants = [
        Participant(
            seat=s,
            account_id=seat0_account if s == 0 else None,
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
        record_path=record_path,
        server_version="hr-test",
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


async def _connect_authed(url: str, username: str, password: str) -> Any:
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    await ws.recv()  # HELLO
    await ws.send(json.dumps({"kind": "AUTH_REQUEST", "username": username, "password": password}))
    auth = json.loads(cast(str, await ws.recv()))
    assert auth["ok"] is True, auth
    return ws


async def _send_recv(ws: Any, msg: dict[str, Any]) -> dict[str, Any]:
    await ws.send(json.dumps(msg))
    return json.loads(cast(str, await ws.recv()))


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def test_history_paginates_by_keyset(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    aid = create_account(
        p._conn,
        username="connor",
        display_name="Connor",
        kind="human",
        role="user",
        password="connorpw12",
    )
    for i in range(5):
        _seed_hand(
            p,
            hand_id=f"h{i}",
            seat0_account=aid,
            started_at_ms=1000 + i,
            record_path=f"records/h{i}.jsonl",
        )

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect_authed(url, "connor", "connorpw12") as ws:
            page1 = await _send_recv(ws, {"kind": "GET_HISTORY", "limit": 3})
            assert page1["kind"] == "HISTORY"
            assert len(page1["hands"]) == 3
            assert page1["hands"][0]["won"] is True
            assert page1["hands"][0]["seat"] == 0
            assert page1["next_before_hand_id"] is not None  # more to come

            page2 = await _send_recv(
                ws,
                {"kind": "GET_HISTORY", "limit": 3, "before_hand_id": page1["next_before_hand_id"]},
            )
            assert len(page2["hands"]) == 2  # remaining
            assert page2["next_before_hand_id"] is None  # end of history
    finally:
        await orch.close()
        p.close()


# ---------------------------------------------------------------------------
# Replay authorization
# ---------------------------------------------------------------------------


async def test_participant_replays_own_seat(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    rec = tmp_path / "records" / "h1.jsonl"
    shutil.copy(_FIXTURE_RECORD, rec)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    aid = create_account(
        p._conn,
        username="connor",
        display_name="Connor",
        kind="human",
        role="user",
        password="connorpw12",
    )
    _seed_hand(p, hand_id="h1", seat0_account=aid, started_at_ms=1000, record_path=str(rec))

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect_authed(url, "connor", "connorpw12") as ws:
            r = await _send_recv(ws, {"kind": "GET_REPLAY", "hand_id": "h1"})
            assert r["kind"] == "REPLAY"
            assert r["seat"] == 0
            assert r["snapshot"]
            assert len(r["events"]) > 0
            # Own seat (0) draws keep their tile; other seats' don't.
            draws = [e for e in r["events"] if e.get("event") == "DRAW"]
            assert any(d.get("seat") == 0 and "tile" in d for d in draws)
            assert all("tile" not in d for d in draws if d.get("seat") != 0)
    finally:
        await orch.close()
        p.close()


async def test_non_participant_non_admin_refused(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    rec = tmp_path / "records" / "h1.jsonl"
    shutil.copy(_FIXTURE_RECORD, rec)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    owner = create_account(
        p._conn,
        username="owner",
        display_name="Owner",
        kind="human",
        role="user",
        password="ownerpw1234",
    )
    create_account(
        p._conn,
        username="nosy",
        display_name="Nosy",
        kind="human",
        role="user",
        password="nosypw12345",
    )
    _seed_hand(p, hand_id="h1", seat0_account=owner, started_at_ms=1000, record_path=str(rec))

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect_authed(url, "nosy", "nosypw12345") as ws:
            r = await _send_recv(ws, {"kind": "GET_REPLAY", "hand_id": "h1"})
            assert r["kind"] == "ERROR"
            assert r["code"] == "not_authorized"
    finally:
        await orch.close()
        p.close()


async def test_admin_gets_public_view(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    rec = tmp_path / "records" / "h1.jsonl"
    shutil.copy(_FIXTURE_RECORD, rec)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    owner = create_account(
        p._conn,
        username="owner",
        display_name="Owner",
        kind="human",
        role="user",
        password="ownerpw1234",
    )
    create_account(
        p._conn,
        username="boss",
        display_name="Boss",
        kind="human",
        role="admin",
        password="bosspw12345",
    )
    _seed_hand(p, hand_id="h1", seat0_account=owner, started_at_ms=1000, record_path=str(rec))

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect_authed(url, "boss", "bosspw12345") as ws:
            r = await _send_recv(ws, {"kind": "GET_REPLAY", "hand_id": "h1"})
            assert r["kind"] == "REPLAY"
            assert r["seat"] == -1  # public view
            draws = [e for e in r["events"] if e.get("event") == "DRAW"]
            assert draws and all("tile" not in d for d in draws)  # all hidden
    finally:
        await orch.close()
        p.close()


async def test_unknown_hand_is_not_found(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    create_account(
        p._conn,
        username="connor",
        display_name="Connor",
        kind="human",
        role="user",
        password="connorpw12",
    )
    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect_authed(url, "connor", "connorpw12") as ws:
            r = await _send_recv(ws, {"kind": "GET_REPLAY", "hand_id": "nope"})
            assert r["kind"] == "ERROR"
            assert r["code"] == "hand_not_found"
    finally:
        await orch.close()
        p.close()


async def test_corrupt_record_is_unavailable(tmp_path: Path) -> None:
    (tmp_path / "records").mkdir(exist_ok=True)
    rec = tmp_path / "records" / "h1.jsonl"
    rec.write_text('{"event":"HEADER","seq":0}\n{"garbage really')  # truncated/corrupt
    p = Persistence(tmp_path / "db.sqlite", tmp_path)
    aid = create_account(
        p._conn,
        username="connor",
        display_name="Connor",
        kind="human",
        role="user",
        password="connorpw12",
    )
    _seed_hand(p, hand_id="h1", seat0_account=aid, started_at_ms=1000, record_path=str(rec))

    orch = _orch(tmp_path, p)
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}"
        async with await _connect_authed(url, "connor", "connorpw12") as ws:
            r = await _send_recv(ws, {"kind": "GET_REPLAY", "hand_id": "h1"})
            assert r["kind"] == "ERROR"
            assert r["code"] == "replay_unavailable"
    finally:
        await orch.close()
        p.close()
