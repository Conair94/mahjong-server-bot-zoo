"""Table manager — core happy-path tests.

Spec: docs/specs/seat-port.md § Lifecycle and concurrency model, fixture 1.

These cover the S0 walking-skeleton exit shape: four CannedAdapters complete
a hand, the record writes, replay reproduces the canonical final state.
Timeout/illegal/crash tests live in `test_manager_errors.py`; claim-window
priority tests live in `test_manager_claims.py`.

Layer-8 amendments: ``dealer_seat`` and ``hand_index_in_match`` params propagate
correctly through ``run_hand`` to the engine and the HEADER record.
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


@pytest.mark.asyncio(loop_scope="function")
async def test_run_hand_dealer_seat_propagates_to_header_winds(tmp_path: Path) -> None:
    """Layer-8: dealer_seat=1 → seat 1 is East (F1) in the HEADER, and the
    engine's first actor is seat 1 (verified via the first DISCARD event).

    This is the contract test for the known-limitation fix: run_hand was
    previously hardcoded to dealer_seat=0, so the HEADER winds were always
    seat-0=East regardless of the orchestrator's rotation.
    """
    dealer_seat = 1
    record_path = tmp_path / "hand.jsonl"
    await run_hand(
        adapters=_four_passers(),
        ruleset=MCR_REF,
        seed=12345,
        hand_id="test-dealer-seat",
        record_path=record_path,
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        dealer_seat=dealer_seat,
    )

    events = read_record(record_path)
    header = events[0]
    assert header["event"] == "HEADER"

    # Seat winds in HEADER must rotate with the dealer.
    # dealer_seat=1 → seat 1 = East (F1), seat 2 = South (F2),
    #                   seat 3 = West (F3),  seat 0 = North (F4)
    seats_by_seat = {s["seat"]: s for s in header["seats"]}
    assert seats_by_seat[1]["wind"] == "F1", f"Dealer seat 1 should be East; got {seats_by_seat}"
    assert seats_by_seat[2]["wind"] == "F2"
    assert seats_by_seat[3]["wind"] == "F3"
    assert seats_by_seat[0]["wind"] == "F4"

    # The engine must also start at dealer_seat: first DISCARD comes from seat 1.
    first_discard = next(e for e in events if e["event"] == "DISCARD")
    assert first_discard["seat"] == dealer_seat, (
        f"First DISCARD should be by dealer (seat {dealer_seat}), "
        f"got seat {first_discard['seat']}"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_run_hand_hand_index_in_match_propagates_to_header(tmp_path: Path) -> None:
    """Layer-8: hand_index_in_match param is reflected in the HEADER record."""
    record_path = tmp_path / "hand.jsonl"
    await run_hand(
        adapters=_four_passers(),
        ruleset=MCR_REF,
        seed=12345,
        hand_id="test-hand-index",
        record_path=record_path,
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        hand_index_in_match=3,
    )

    events = read_record(record_path)
    header = events[0]
    assert header["hand_index_in_match"] == 3
