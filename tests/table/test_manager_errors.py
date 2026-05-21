"""Table manager — timeout / illegal / crash + strike counter.

Spec: docs/specs/seat-port.md § Error model, fixtures 2, 3, 4, 8.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mahjong.adapters.autopass import AutoPassAdapter
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

SERVER = {"version": "test", "git_sha": "test", "host": "test"}


def _four_passers() -> list[CannedAdapter]:
    return [
        CannedAdapter(identity={"kind": "canned", "script": "pass"}, actions=[]) for _ in range(4)
    ]


# --- Timeout ---


class _SlowAdapter(CannedAdapter):
    """Sleeps past the decide deadline every call."""

    def __init__(self) -> None:
        super().__init__(identity={"kind": "canned", "script": "slow"}, actions=[])

    async def decide(self, prompt: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        await asyncio.sleep(10.0)
        return prompt["default_action"]


@pytest.mark.asyncio(loop_scope="function")
async def test_timeout_yields_timeout_marker(tmp_path: Path) -> None:
    """A `decide` that doesn't return fires `default_action` and the
    resulting event carries `timeout: true`."""
    adapters: list[Any] = _four_passers()
    adapters[0] = _SlowAdapter()
    await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
        decide_timeout_seconds=0.05,
        strike_limit=99,  # don't swap during this test
    )
    events = read_record(tmp_path / "hand.jsonl")
    # Seat 0's first DISCARD (turn 0) is the timeout one.
    first_discard_by_seat_0 = next(e for e in events if e["event"] == "DISCARD" and e["seat"] == 0)
    assert first_discard_by_seat_0.get("timeout") is True


# --- Illegal action ---


class _BadActionAdapter(CannedAdapter):
    """Returns an action not in legal_actions."""

    def __init__(self) -> None:
        super().__init__(identity={"kind": "canned", "script": "bad"}, actions=[])

    async def decide(self, prompt: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        return {"type": "PLAY", "tile": "Z9"}  # not a valid tile in any legal set


@pytest.mark.asyncio(loop_scope="function")
async def test_illegal_action_yields_illegal_marker(tmp_path: Path) -> None:
    """An action not in `legal_actions` triggers `default_action` and the
    event carries `illegal: true, attempted_action: ...`."""
    adapters: list[Any] = _four_passers()
    adapters[0] = _BadActionAdapter()
    await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
        strike_limit=99,
    )
    events = read_record(tmp_path / "hand.jsonl")
    first = next(e for e in events if e["event"] == "DISCARD" and e["seat"] == 0)
    assert first.get("illegal") is True
    assert first.get("attempted_action") == {"type": "PLAY", "tile": "Z9"}


# --- Crash ---


class _CrashAdapter(CannedAdapter):
    """Raises on every decide."""

    def __init__(self) -> None:
        super().__init__(identity={"kind": "canned", "script": "crash"}, actions=[])

    async def decide(self, prompt: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        raise RuntimeError("boom")


@pytest.mark.asyncio(loop_scope="function")
async def test_crash_records_marker_and_does_not_wedge_table(tmp_path: Path) -> None:
    adapters: list[Any] = _four_passers()
    adapters[0] = _CrashAdapter()
    state = await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
        strike_limit=99,
    )
    assert state["phase"] == "TERMINAL"
    events = read_record(tmp_path / "hand.jsonl")
    first = next(e for e in events if e["event"] == "DISCARD" and e["seat"] == 0)
    assert first.get("crashed") is True


# --- Strike counter -> AutoPassAdapter ---


@pytest.mark.asyncio(loop_scope="function")
async def test_strike_limit_swaps_in_autopass(tmp_path: Path) -> None:
    """After `strike_limit` failures, the offending seat is replaced by
    AutoPassAdapter and subsequent events carry `auto_pass: true`."""
    adapters: list[Any] = _four_passers()
    adapters[0] = _CrashAdapter()
    await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
        strike_limit=2,
    )
    events = read_record(tmp_path / "hand.jsonl")
    seat_0_discards = [e for e in events if e["event"] == "DISCARD" and e["seat"] == 0]
    # After 2 strikes, seat 0 was swapped; subsequent discards carry auto_pass.
    assert any(e.get("auto_pass") is True for e in seat_0_discards[2:])


# --- AutoPassAdapter substitution preserves replay (fixture 8) ---


@pytest.mark.asyncio(loop_scope="function")
async def test_autopass_substitution_preserves_replay(tmp_path: Path) -> None:
    """A hand with one seat swapped to AutoPassAdapter mid-game still
    produces a record that replays to the same final state."""
    adapters: list[Any] = _four_passers()
    adapters[2] = AutoPassAdapter()  # always default from turn 0
    final = await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
    )
    events = read_record(tmp_path / "hand.jsonl")
    states = list(replay(events))
    assert state_hash(states[-1]) == state_hash(final)  # type: ignore[arg-type]
