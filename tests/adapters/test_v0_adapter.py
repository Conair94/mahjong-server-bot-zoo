"""V0Adapter conformance + the v0 rollout verification artifact.

Spec: docs/specs/v0-offense-bot.md § Verification fixtures 10 & 11.

Fixture 10 pins the adapter satisfies the seat port and drives a real hand to
TERMINAL with a zero-sum settlement. Fixture 11 is the RL verification
artifact: a seeded four-v0 rollout is byte-reproducible AND actually wins hands
(not all wall-exhaustion draws) — "it ran without crashing" is not the receipt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.v0 import V0Adapter
from mahjong.engine import state_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.table import manager as mgr

pytestmark = pytest.mark.needs_pymjgb

HOUSE_REF: dict[str, Any] = {
    "id": "mcr-house-3fan",
    "version": 1,
    "config_hash": MANIFEST["mcr-house-3fan"],
}
SERVER_INFO = {"version": "v0-test", "git_sha": "test", "host": "test"}


def _adapters() -> list[SeatAdapter]:
    return [cast(SeatAdapter, V0Adapter()) for _ in range(4)]


async def _run(seed: int, out: Path) -> dict[str, Any]:
    return await mgr.run_hand(
        adapters=_adapters(),
        ruleset=HOUSE_REF,
        seed=seed,
        hand_id=f"v0-{seed:08x}-aaaa-bbbb-cccc-000000000000"[:36],
        record_path=out,
        server_info=SERVER_INFO,
    )


# --- Fixture 10: conformance + a real hand to TERMINAL --------------------


def test_v0_adapter_satisfies_seat_port() -> None:
    adapter = V0Adapter()
    assert isinstance(adapter, SeatAdapter)
    assert adapter.kind == "bot"
    assert adapter.identity["bot_id"] == "v0"


@pytest.mark.asyncio(loop_scope="function")
async def test_four_v0_bots_complete_a_hand(tmp_path: Path) -> None:
    final = await _run(12345, tmp_path / "hand.jsonl")
    assert final["phase"] == "TERMINAL"
    assert final["terminal"] is not None
    assert final["terminal"]["kind"] in ("HU", "DRAW")
    assert sum(final["terminal"]["score_delta"]) == 0


# --- Fixture 11: determinism + sanity baseline (the RL artifact) ----------


@pytest.mark.asyncio(loop_scope="function")
async def test_v0_rollout_is_deterministic(tmp_path: Path) -> None:
    first = await _run(777, tmp_path / "a.jsonl")
    second = await _run(777, tmp_path / "b.jsonl")
    # The canonical final-state hash is the determinism receipt; record bytes
    # also carry wall-clock `ts` fields, which are not part of game logic.
    assert state_hash(first) == state_hash(second)


@pytest.mark.slow
@pytest.mark.asyncio(loop_scope="function")
async def test_v0_offense_wins_hands(tmp_path: Path) -> None:
    """Sanity baseline: an offense bot must win a non-trivial fraction of hands,
    not stall every game into a wall-exhaustion draw."""
    kinds = []
    for seed in range(20):
        final = await _run(seed, tmp_path / f"h{seed}.jsonl")
        kinds.append(final["terminal"]["kind"])
    wins = kinds.count("HU")
    assert wins >= 3, f"expected several HU wins across 20 hands, got {wins} ({kinds})"
