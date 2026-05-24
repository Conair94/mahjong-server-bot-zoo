"""End-to-end S2 fixture: one full hand round-tripped through the real stack.

Spec: docs/specs/tui-client.md fixture 18 / CHECKLIST Step 7.6.ii.

F1 is the byte-identical-record gate. A raw WebSocket client connects to a
running `WebOrchestrator`, ATTACHes seat 0, and echoes `default_action` on
every PROMPT. Seats 1-3 are `CannedAdapter`s with no scripted actions —
their `decide` also returns `default_action`. Net result: the action chosen
at every prompt is the manager's default, the engine path is the same as
the S0 walking-skeleton fixture, and the record is byte-identical to a
checked-in fixture that differs from S0's only in seat-0's identity
(`human` vs `canned`).

This pins the full stack: `WebSocketServer` → `TableSessions` → `SeatSession`
→ `HumanAdapter` → `manager.run_hand` → record writer. If any layer drops
or reorders an event, or projects the wrong shape, or double-emits HAND_END
(see 7.6.i), the byte assertion fails.

Why a raw WS client and not Playwright: the browser UI's wire handling is
already pinned by `tests/web/test_prompt.py`. F1's job is the orchestrator
+ record contract; routing it through the browser would add Chromium-launch
cost and a UI failure mode without strengthening the contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.rulesets import MANIFEST
from mahjong.table import manager as mgr
from mahjong.web.server import WebOrchestrator
from mahjong.wire.server import Connection

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
F1_FIXTURE = Path("tests/_fixtures/s2_e2e_record.jsonl")
F1_HAND_ID = "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f"
F1_SEED = 12345
F1_USER_ID = "u_test"
F1_DISPLAY = "Tester"
F1_SERVER_INFO: dict[str, Any] = {"version": "s0-fixture", "git_sha": "fixed", "host": "fixture"}


def _patch_fixed_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the manager's wall-clock so the record's `ts` fields are stable.

    Mirrors the S0 walking-skeleton pattern (tests/table/test_s0_walking_skeleton.py).
    """
    counter = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-20T00:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)


def _fixed_identity(_conn: Connection) -> HumanIdentity:
    """Inject the same HumanIdentity every connection so the HEADER's
    seats[0].identity is deterministic."""
    return {"kind": "human", "user_id": F1_USER_ID, "display": F1_DISPLAY}


async def _drive_default_echoing_client(
    url: str,
    *,
    hand_end: asyncio.Event,
    captured: list[dict[str, Any]] | None = None,
    drain_after_hand_end_s: float = 0.0,
) -> None:
    """Connect, ATTACH seat 0, echo `default_action` on each PROMPT, stop on
    HAND_END. If `captured` is provided, every received frame is appended to
    it. After HAND_END, optionally drain for `drain_after_hand_end_s` so
    callers can assert no further frames arrive (no-double-emit check)."""
    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
        hello = json.loads(cast(str, await ws.recv()))
        if captured is not None:
            captured.append(hello)
        assert hello["kind"] == "HELLO", hello

        await ws.send(json.dumps({"kind": "ATTACH", "table_id": 1, "seat": 0}))
        attached = json.loads(cast(str, await ws.recv()))
        if captured is not None:
            captured.append(attached)
        assert attached["kind"] == "ATTACHED", attached
        assert attached["seat"] == 0

        while True:
            msg = json.loads(cast(str, await ws.recv()))
            if captured is not None:
                captured.append(msg)
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
                hand_end.set()
                if drain_after_hand_end_s > 0:
                    await _drain_until_close_or_timeout(ws, captured, drain_after_hand_end_s)
                return


async def _drain_until_close_or_timeout(
    ws: Any, captured: list[dict[str, Any]] | None, seconds: float
) -> None:
    """Keep reading until either the timeout elapses or the socket closes.
    Used to assert the post-HAND_END quiescence invariant."""
    deadline = asyncio.get_event_loop().time() + seconds
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except TimeoutError:
            return
        except websockets.exceptions.ConnectionClosed:
            return
        if captured is not None:
            captured.append(json.loads(cast(str, raw)))


async def test_s2_e2e_record_is_byte_identical_to_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The S2 exit gate: full stack hosts one hand, record matches fixture
    byte-for-byte."""
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"

    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=F1_SEED,
        hand_id=F1_HAND_ID,
        record_path=out,
        server_info=F1_SERVER_INFO,
        identity_factory=_fixed_identity,
    )
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        hand_end = asyncio.Event()
        client_task = asyncio.create_task(_drive_default_echoing_client(url, hand_end=hand_end))
        try:
            await orch.wait_hand_complete(timeout=15.0)
            await asyncio.wait_for(hand_end.wait(), timeout=2.0)
        finally:
            client_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client_task
    finally:
        await orch.close()

    assert out.read_bytes() == F1_FIXTURE.read_bytes()


async def test_s2_e2e_no_double_emit_hand_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wire-level invariant: exactly one HAND_END frame per hand. The
    byte-identical record check only constrains record events; this pins
    that `manager._safe_left("HAND_ENDED")` + `HumanAdapter.left` don't
    re-emit a second HAND_END after `SeatSession.observe` already routed
    the engine's HAND_END record event to the wire (Step 7.6.i)."""
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"

    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=F1_SEED,
        hand_id=F1_HAND_ID,
        record_path=out,
        server_info=F1_SERVER_INFO,
        identity_factory=_fixed_identity,
    )
    await orch.start()
    captured: list[dict[str, Any]] = []
    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        hand_end = asyncio.Event()
        client_task = asyncio.create_task(
            _drive_default_echoing_client(
                url,
                hand_end=hand_end,
                captured=captured,
                drain_after_hand_end_s=0.5,
            )
        )
        await orch.wait_hand_complete(timeout=15.0)
        await asyncio.wait_for(hand_end.wait(), timeout=2.0)
        await client_task
    finally:
        await orch.close()

    hand_end_frames = [m for m in captured if m["kind"] == "HAND_END"]
    assert len(hand_end_frames) == 1, (
        f"expected exactly one HAND_END frame, got {len(hand_end_frames)}: {hand_end_frames}"
    )
    # The single HAND_END must carry a non-empty terminal payload (not the
    # empty dict that the pre-7.6.i double-emit path produced).
    assert hand_end_frames[0]["terminal"], hand_end_frames[0]
