"""V1Adapter conformance + the v1 rollout verification artifact.

Spec: docs/specs/v1-rule-bot.md § Verification fixtures 15 (rollout half) & 16.

Mirrors the v0 adapter suite: the adapter satisfies the seat port, four v1
bots drive a real hand to TERMINAL with a zero-sum settlement, a seeded
rollout is byte-reproducible, and the offense still wins hands (defense must
not have lobotomized the bot into all-draws).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.v1 import V1Adapter
from mahjong.engine import state_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.table import manager as mgr

pytestmark = pytest.mark.needs_pymjgb

HOUSE_REF: dict[str, Any] = {
    "id": "mcr-house-3fan",
    "version": 1,
    "config_hash": MANIFEST["mcr-house-3fan"],
}
SERVER_INFO = {"version": "v1-test", "git_sha": "test", "host": "test"}


def _adapters() -> list[SeatAdapter]:
    return [cast(SeatAdapter, V1Adapter()) for _ in range(4)]


async def _run(seed: int, out: Path) -> dict[str, Any]:
    return await mgr.run_hand(
        adapters=_adapters(),
        ruleset=HOUSE_REF,
        seed=seed,
        hand_id=f"v1-{seed:08x}-aaaa-bbbb-cccc-000000000000"[:36],
        record_path=out,
        server_info=SERVER_INFO,
    )


def test_v1_adapter_satisfies_seat_port() -> None:
    adapter = V1Adapter()
    assert isinstance(adapter, SeatAdapter)
    assert adapter.kind == "bot"
    assert adapter.identity["bot_id"] == "v1"
    assert adapter.identity["version"] == "1"


@pytest.mark.asyncio(loop_scope="function")
async def test_four_v1_bots_complete_a_hand(tmp_path: Path) -> None:
    final = await _run(12345, tmp_path / "hand.jsonl")
    assert final["phase"] == "TERMINAL"
    assert final["terminal"] is not None
    assert final["terminal"]["kind"] in ("HU", "DRAW")
    assert sum(final["terminal"]["score_delta"]) == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_v1_rollout_is_deterministic(tmp_path: Path) -> None:
    first = await _run(777, tmp_path / "a.jsonl")
    second = await _run(777, tmp_path / "b.jsonl")
    assert state_hash(first) == state_hash(second)


@pytest.mark.slow
@pytest.mark.asyncio(loop_scope="function")
async def test_v1_still_wins_hands(tmp_path: Path) -> None:
    """Defense must not turn the bot into a perpetual folder: a non-trivial
    fraction of 4x-v1 hands must still reach HU."""
    kinds = []
    for seed in range(20):
        final = await _run(seed, tmp_path / f"h{seed}.jsonl")
        kinds.append(final["terminal"]["kind"])
    wins = kinds.count("HU")
    assert wins >= 3, f"expected several HU wins across 20 hands, got {wins} ({kinds})"
