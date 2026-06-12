"""Layer 8 multi-hand orchestration end-to-end fixtures.

F1: Two consecutive hands — the client plays through both.  After hand 0's
    HAND_END the client sees DETACH { reason: 'hand_ended' } then
    ATTACHED { hand_index: 1 }.  After hand 1's HAND_END the match is done.

F2: Spectator stays subscribed across the hand boundary (session-mux.md
    fixture 20).  Spectator joins before hand 0, sees HAND_END for hand 0,
    then sees EVENTs with hand_index=1 without re-subscribing.

F3: ATTACHED frames carry the correct, incrementing hand_index for a three-
    hand match.
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
from mahjong.table import manager as mgr
from mahjong.web.server import WebOrchestrator
from mahjong.wire.server import Connection

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
L8_SEED = 99_999
L8_USER_ID = "u_layer8"
L8_SERVER_INFO: dict[str, Any] = {"version": "l8-test", "git_sha": "test", "host": "test"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixed_identity(_conn: Connection) -> HumanIdentity:
    return {"kind": "human", "user_id": L8_USER_ID, "display": "Layer8Tester"}


def _make_orch(
    tmp_path: Path,
    *,
    max_hands: int,
    between_hand_pause_seconds: float = 0.05,
) -> WebOrchestrator:
    return WebOrchestrator(
        ruleset=MCR_REF,
        seed=L8_SEED,
        hand_id="l8-test-hand",
        record_path=tmp_path / "record.jsonl",
        server_info=L8_SERVER_INFO,
        identity_factory=_fixed_identity,
        max_hands=max_hands,
        between_hand_pause_seconds=between_hand_pause_seconds,
    )


async def _drive_multi_hand_client(
    url: str,
    *,
    num_hands: int,
    captured: list[dict[str, Any]] | None = None,
    timeout_per_hand: float = 30.0,
) -> None:
    """Connect, ATTACH seat 0, echo default_action on every PROMPT, play
    through ``num_hands`` consecutive hands.

    Between hands the client reads DETACH { reason: 'hand_ended' } then
    ATTACHED { hand_index: N }.  The client does not disconnect between hands.
    Stops after the Nth HAND_END.
    """
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
        assert attached["hand_index"] == 0

        hands_seen = 0
        deadline = asyncio.get_event_loop().time() + timeout_per_hand * num_hands
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            assert remaining > 0, "timed out driving multi-hand client"
            raw = cast(str, await asyncio.wait_for(ws.recv(), timeout=remaining))
            msg: dict[str, Any] = json.loads(raw)
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
                hands_seen += 1
                if hands_seen >= num_hands:
                    break
                # More hands coming; continue reading DETACH + ATTACHED
            elif kind == "DETACH":
                # Between-hand boundary signal; stay connected
                pass
            # EVENT: ignore


# ---------------------------------------------------------------------------
# F1: Two-hand loop — hand_index increments, DETACH(hand_ended) appears
# ---------------------------------------------------------------------------


async def test_l8_f1_two_hand_loop_hand_index_increments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: Play 2 hands.  Verify frame ordering:
    HAND_END(hand_index=0) → DETACH(reason='hand_ended') → ATTACHED(hand_index=1)
    → ... → HAND_END(hand_index=1).
    """
    # Pin timestamps so records are byte-stable (not checked here, but good practice)
    counter: dict[str, int] = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-24T00:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)

    orch = _make_orch(tmp_path, max_hands=2)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    captured: list[dict[str, Any]] = []
    try:
        await asyncio.wait_for(
            _drive_multi_hand_client(url, num_hands=2, captured=captured),
            timeout=90.0,
        )
        await orch.wait_hand_complete(timeout=10.0)
    finally:
        await orch.close()

    # --- ATTACHED frames ---
    all_attached = [m for m in captured if m["kind"] == "ATTACHED"]
    assert len(all_attached) == 2, (
        f"Expected 2 ATTACHED frames (one per hand), got {len(all_attached)}: "
        f"{[m.get('hand_index') for m in all_attached]}"
    )
    assert all_attached[0]["hand_index"] == 0
    assert all_attached[1]["hand_index"] == 1

    # --- HAND_END frames ---
    all_hand_ends = [m for m in captured if m["kind"] == "HAND_END"]
    assert len(all_hand_ends) == 2, f"Expected 2 HAND_END frames, got {len(all_hand_ends)}"
    assert all_hand_ends[0]["hand_index"] == 0
    assert all_hand_ends[1]["hand_index"] == 1

    # --- DETACH { reason: 'hand_ended' } between the two hands ---
    hand_ended_detaches = [
        m for m in captured if m["kind"] == "DETACH" and m.get("reason") == "hand_ended"
    ]
    assert len(hand_ended_detaches) >= 1, (
        f"Expected at least one DETACH(reason='hand_ended'); got: "
        f"{[m for m in captured if m['kind'] == 'DETACH']}"
    )

    # --- Ordering: HAND_END(0) → DETACH(hand_ended) → ATTACHED(1) ---
    idx_of = {m["kind"]: [] for m in captured}
    for i, m in enumerate(captured):
        idx_of[m["kind"]].append(i)

    first_hand_end = next(i for i, m in enumerate(captured) if m["kind"] == "HAND_END")
    first_hand_ended_detach = next(
        (
            i
            for i, m in enumerate(captured)
            if i > first_hand_end and m["kind"] == "DETACH" and m.get("reason") == "hand_ended"
        ),
        None,
    )
    second_attached = next(
        (i for i, m in enumerate(captured) if i > first_hand_end and m["kind"] == "ATTACHED"),
        None,
    )
    assert first_hand_ended_detach is not None, "No DETACH(hand_ended) after HAND_END(0)"
    assert second_attached is not None, "No ATTACHED(hand_index=1) after HAND_END(0)"
    assert (
        first_hand_ended_detach < second_attached
    ), "DETACH(hand_ended) must precede ATTACHED(hand_index=1)"


# ---------------------------------------------------------------------------
# F2: Spectator stays subscribed across the hand boundary
# ---------------------------------------------------------------------------


async def test_l8_f2_spectator_stays_subscribed_across_hand_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2 (session-mux.md fixture 20): Spectator subscribed during hand 0 sees
    HAND_END for hand 0, then EVENTs with hand_index=1 without re-subscribing.
    """
    counter: dict[str, int] = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-24T01:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)

    orch = _make_orch(tmp_path, max_hands=2)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    spectator_frames: list[dict[str, Any]] = []
    spectator_ready = asyncio.Event()
    hand1_events_seen = asyncio.Event()

    async def spectator_task() -> None:
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
            hello = json.loads(cast(str, await ws.recv()))
            spectator_frames.append(hello)
            assert hello["kind"] == "HELLO"

            await ws.send(json.dumps({"kind": "SPECTATE", "table_id": 1}))
            spectating = json.loads(cast(str, await ws.recv()))
            spectator_frames.append(spectating)
            assert spectating["kind"] == "SPECTATING"
            spectator_ready.set()

            # Collect until we've seen HAND_END(0) and ≥4 EVENTs with hand_index=1
            hand1_count = 0
            async for raw in ws:
                msg = json.loads(cast(str, raw))
                spectator_frames.append(msg)
                if msg["kind"] == "EVENT" and msg.get("hand_index") == 1:
                    hand1_count += 1
                    if hand1_count >= 4:
                        hand1_events_seen.set()
                        return

    async def player_task() -> None:
        await spectator_ready.wait()
        await _drive_multi_hand_client(url, num_hands=2)

    try:
        await asyncio.wait_for(
            asyncio.gather(spectator_task(), player_task()),
            timeout=90.0,
        )
        await orch.wait_hand_complete(timeout=10.0)
    finally:
        await orch.close()

    # Spectator saw HAND_END for hand 0
    hand_ends_0 = [
        m for m in spectator_frames if m["kind"] == "HAND_END" and m.get("hand_index") == 0
    ]
    assert (
        len(hand_ends_0) == 1
    ), f"Spectator should see exactly one HAND_END(hand_index=0); got {len(hand_ends_0)}"

    # Spectator saw EVENTs with hand_index=1 without re-subscribing
    hand1_events = [
        m for m in spectator_frames if m["kind"] == "EVENT" and m.get("hand_index") == 1
    ]
    assert (
        len(hand1_events) >= 4
    ), f"Spectator should see ≥4 hand-1 EVENTs (no re-subscribe needed); got {len(hand1_events)}"

    # Only one SPECTATING frame (no re-subscribe)
    spectating_frames = [m for m in spectator_frames if m["kind"] == "SPECTATING"]
    assert (
        len(spectating_frames) == 1
    ), f"Spectator should only receive SPECTATING once; got {len(spectating_frames)}"


# ---------------------------------------------------------------------------
# F3: Three-hand loop — ATTACHED carries hand_index 0, 1, 2
# ---------------------------------------------------------------------------


async def test_l8_f3_three_hand_loop_hand_indices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: Play 3 hands.  Every ATTACHED frame carries the correct hand_index."""
    counter: dict[str, int] = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-24T02:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)

    orch = _make_orch(tmp_path, max_hands=3)
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    captured: list[dict[str, Any]] = []
    try:
        await asyncio.wait_for(
            _drive_multi_hand_client(url, num_hands=3, captured=captured),
            timeout=120.0,
        )
        await orch.wait_hand_complete(timeout=10.0)
    finally:
        await orch.close()

    all_attached = [m for m in captured if m["kind"] == "ATTACHED"]
    assert len(all_attached) == 3, (
        f"Expected 3 ATTACHED frames (hands 0-2), got {len(all_attached)}: "
        f"{[m.get('hand_index') for m in all_attached]}"
    )
    for i, frame in enumerate(all_attached):
        assert (
            frame["hand_index"] == i
        ), f"ATTACHED #{i}: expected hand_index={i}, got {frame['hand_index']}"

    # Dealer rotates: each hand's initial snapshot should differ
    # (confirmed indirectly: different dealer_seat → different concealed tile counts per seat)
    # Weak check: the three snapshots are not all identical
    snapshots = [m["snapshot"] for m in all_attached]
    assert not all(
        s == snapshots[0] for s in snapshots
    ), "All three hands have identical snapshots — dealer rotation or seed derivation broken"
