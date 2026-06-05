"""Replay regression for winning claims and HU terminals.

Spec: docs/specs/record-format.md § replayable.

The pre-existing replay tests (s0 walking skeleton) only exercised DRAW-terminal
records produced by canned-PASS bots: every claim window was all-PASS and the
hand ended by wall exhaustion. That left two whole classes of record unverified
until the v0 offense bot started winning hands:

  - a win declared inside a claim window (a ron / discard win) and a self-draw
    win — both recorded only as HAND_END, which replay used to skip;
  - winning PENG/CHI/EXPOSED-kong claims, whose losing CLAIM_DECISIONs replay
    used to mis-apply.

These drive real four-v0 hands and assert the replayed final state matches the
runtime final state — the records-as-source-of-truth contract that persistence
rebuild and late-join replay depend on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.v0 import V0Adapter
from mahjong.engine import state_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.records.reader import read_record
from mahjong.records.replay import replay
from mahjong.table import manager as mgr

pytestmark = pytest.mark.needs_pymjgb

MCR_REF: dict[str, Any] = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
SERVER = {"version": "test", "git_sha": "test", "host": "test"}


async def _run(seed: int, out: Path) -> dict[str, Any]:
    return await mgr.run_hand(
        adapters=[cast(SeatAdapter, V0Adapter()) for _ in range(4)],
        ruleset=MCR_REF,
        seed=seed,
        hand_id=f"replay-{seed:08d}-0000-0000-0000-000000000000"[:36],
        record_path=out,
        server_info=SERVER,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_v0_records_replay_to_runtime_final_state(tmp_path: Path) -> None:
    seen: set[str] = set()
    for seed in range(16):
        out = tmp_path / f"hand_{seed}.jsonl"
        final = await _run(seed, out)
        seen.add(f"{final['terminal']['kind']}/{final['terminal']['win_type']}")

        states = list(replay(read_record(out)))
        assert state_hash(states[-1]) == state_hash(final), (
            f"seed {seed}: replay final state diverged from runtime"
        )

    # The seed range must actually exercise both win paths — otherwise this
    # test would silently stop covering the regression it exists for.
    assert "HU/DISCARD" in seen, f"no discard-win (ron) in sample: {seen}"
    assert "HU/SELF_DRAW" in seen, f"no self-draw win in sample: {seen}"
