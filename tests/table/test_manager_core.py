"""Table manager — core happy-path tests.

Spec: docs/specs/seat-port.md § Lifecycle and concurrency model, fixture 1.

These cover the S0 walking-skeleton exit shape: four CannedAdapters complete
a hand, the record writes, replay reproduces the canonical final state.
Timeout/illegal/crash tests live in `test_manager_errors.py`; claim-window
priority tests live in `test_manager_claims.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mahjong.adapters.canned import CannedAdapter
from mahjong.engine import state_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.records.reader import read_record
from mahjong.records.replay import replay
from mahjong.table.manager import run_hand

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _four_passers() -> list[CannedAdapter]:
    """Four CannedAdapters with empty scripts: every prompt resolves to the
    table's default action (tsumogiri on own turn, PASS in claim windows)."""
    return [
        CannedAdapter(identity={"kind": "canned", "script": "pass"}, actions=[]) for _ in range(4)
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_run_hand_completes_and_writes_record(tmp_path: Path) -> None:
    record_path = tmp_path / "hand.jsonl"
    final_state = await run_hand(
        adapters=_four_passers(),
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=record_path,
        server_info={"version": "test", "git_sha": "test", "host": "test"},
    )

    assert record_path.exists()
    events = read_record(record_path)
    assert events[0]["event"] == "HEADER"
    assert events[-1]["event"] == "FOOTER"
    # Footer's state_hash_final matches the engine's final state.
    assert events[-1]["state_hash_final"] == state_hash(final_state)  # type: ignore[arg-type]


@pytest.mark.asyncio(loop_scope="function")
async def test_run_hand_record_replays_to_matching_final_hash(tmp_path: Path) -> None:
    """The S0 exit contract: the record is replayable byte-identically."""
    record_path = tmp_path / "hand.jsonl"
    final_state = await run_hand(
        adapters=_four_passers(),
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=record_path,
        server_info={"version": "test", "git_sha": "test", "host": "test"},
    )

    events = read_record(record_path)
    states = list(replay(events))
    assert state_hash(states[-1]) == state_hash(final_state)  # type: ignore[arg-type]


@pytest.mark.asyncio(loop_scope="function")
async def test_run_hand_fans_observe_to_every_seat(tmp_path: Path) -> None:
    """Each adapter's observe is invoked at least once per non-DEAL event."""

    class CountingCanned(CannedAdapter):
        def __init__(self) -> None:
            super().__init__(identity={"kind": "canned", "script": "counter"}, actions=[])
            self.observe_count = 0

        async def observe(self, event: dict[str, Any], view: dict[str, Any]) -> None:  # type: ignore[override]
            self.observe_count += 1

    adapters = [CountingCanned() for _ in range(4)]
    await run_hand(
        adapters=adapters,  # type: ignore[arg-type]
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
    )
    for a in adapters:
        assert a.observe_count > 0


@pytest.mark.asyncio(loop_scope="function")
async def test_run_hand_default_for_discard_is_tsumogiri(tmp_path: Path) -> None:
    """An adapter that always plays default during DISCARD plays its
    `last_drawn.tile` every turn → first DISCARD's tile is the dealer's
    14th-tile (set by `initial_state`)."""
    from mahjong.engine import state as state_module

    initial = state_module.initial_state(MCR_REF, seed=12345)
    last_drawn = initial["last_drawn"]
    assert last_drawn is not None
    expected_first_tile = last_drawn["tile"]

    record_path = tmp_path / "hand.jsonl"
    await run_hand(
        adapters=_four_passers(),
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=record_path,
        server_info={"version": "test", "git_sha": "test", "host": "test"},
    )
    events = read_record(record_path)
    first_discard = next(e for e in events if e["event"] == "DISCARD")
    assert first_discard["tile"] == expected_first_tile
    assert first_discard["from_hand"] is False  # tsumogiri
