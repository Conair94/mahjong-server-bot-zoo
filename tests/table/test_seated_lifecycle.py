"""Failed seat initialization must degrade the seat without wedging a hand."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from mahjong.adapters.base import LeaveReason, Prompt, SeatAdapter, SeatContext
from mahjong.adapters.canned import CannedAdapter
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import Action, GameState
from mahjong.records.reader import read_record
from mahjong.table.manager import run_hand


async def _run(tmp_path: Path, adapter: CannedAdapter) -> GameState:
    return await run_hand(
        adapters=cast(
            list[SeatAdapter],
            [adapter]
            + [
                CannedAdapter(identity={"kind": "canned", "script": "pass"}, actions=[])
                for _ in range(3)
            ],
        ),
        ruleset={"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]},
        seed=12345,
        hand_id="seated-lifecycle",
        record_path=tmp_path / "hand.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seated_timeout_seconds=0.02,
        decide_timeout_seconds=0.1,
        step_stall_seconds=1,
    )


@pytest.mark.asyncio
async def test_failed_initialization_replaces_adapter_before_deciding(tmp_path: Path) -> None:
    decisions: list[Prompt] = []
    departures: list[LeaveReason] = []

    class FailedAdapter(CannedAdapter):
        async def seated(self, ctx: SeatContext) -> None:
            raise RuntimeError("initialization failed")

        async def decide(self, prompt: Prompt) -> Action:
            decisions.append(prompt)
            return prompt["default_action"]

        async def left(self, reason: LeaveReason) -> None:
            departures.append(reason)

    state = await _run(
        tmp_path, FailedAdapter(identity={"kind": "canned", "script": "failed"}, actions=[])
    )
    assert not decisions, "an adapter whose initialization failed must never be prompted"
    assert departures == ["REPLACED"]
    assert state["terminal"] is not None
    assert read_record(tmp_path / "hand.jsonl")[-1]["event"] == "FOOTER"


@pytest.mark.asyncio
async def test_cancellation_resistant_initialization_cannot_wedge_hand(tmp_path: Path) -> None:
    cancellation_seen = asyncio.Event()
    release_initialization = asyncio.Event()
    initialization_finished = asyncio.Event()
    departures: list[LeaveReason] = []

    class StalledAdapter(CannedAdapter):
        async def seated(self, ctx: SeatContext) -> None:
            try:
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancellation_seen.set()
                    await release_initialization.wait()
            finally:
                initialization_finished.set()

        async def left(self, reason: LeaveReason) -> None:
            departures.append(reason)

    hand = asyncio.create_task(
        _run(
            tmp_path,
            StalledAdapter(identity={"kind": "canned", "script": "stalled"}, actions=[]),
        )
    )
    try:
        await asyncio.wait_for(cancellation_seen.wait(), 1)
        done, _ = await asyncio.wait({hand}, timeout=1)
        assert hand in done, "seated() ignored cancellation and blocked the hand past its deadline"
        assert hand.result()["terminal"] is not None
        assert departures == ["REPLACED"]
    finally:
        release_initialization.set()
        await asyncio.wait_for(initialization_finished.wait(), 1)
        await asyncio.wait_for(hand, 2)
