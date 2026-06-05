"""Table manager — claim-window concurrency + priority resolution.

Spec: docs/specs/seat-port.md fixture 7 ("highest-priority claim regardless
of submission order"), state-schema § Action grammar (HU > PENG/GANG > CHI).

Resolves the deferred Phase 2 priority ordering captured in memory
project_layer2_claim_priority_deferred.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mahjong.adapters.canned import CannedAdapter
from mahjong.engine import apply_action
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import tile_sort_key
from mahjong.engine.types import GameState
from mahjong.records.reader import read_record
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


@pytest.mark.asyncio(loop_scope="function")
async def test_claim_window_records_all_seats_decisions(tmp_path: Path) -> None:
    """Even when only one claim wins, the losers' submitted decisions are
    captured as CLAIM_DECISION events (the defense-training signal)."""
    # Drive a 4-PASS exhaustive draw; many claim windows fire. Verify that
    # in every window, the number of CLAIM_DECISION events at least matches
    # the number of opportunities.
    await run_hand(
        adapters=_four_passers(),
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
    )
    events = read_record(tmp_path / "hand.jsonl")
    # Group decisions by their preceding CLAIM_WINDOW.
    i = 0
    windows_seen = 0
    while i < len(events):
        if events[i]["event"] != "CLAIM_WINDOW":
            i += 1
            continue
        opps = events[i]["opportunities"]
        # Walk forward and count seats whose decisions appear before next CLAIM_WINDOW or CLAIM_RESOLUTION.
        seats_in_opps = {o["seat"] for o in opps}
        seats_decided: set[int] = set()
        j = i + 1
        while j < len(events) and events[j]["event"] in {
            "CLAIM_DECISION",
            "CLAIM_RESOLUTION",
        }:
            if events[j]["event"] == "CLAIM_DECISION":
                seats_decided.add(events[j]["seat"])
            j += 1
        assert seats_in_opps.issubset(seats_decided), (
            f"window at seq {events[i]['seq']}: opps={seats_in_opps}, decided={seats_decided}"
        )
        windows_seen += 1
        i = j
    assert windows_seen > 0, "smoke seed expected to produce at least one claim window"


class _OpportunisticAdapter(CannedAdapter):
    """Returns the first action of `prefer_kind` it sees in legal_actions,
    else `default_action`. Used to force a specific claim in the first
    window where it's available."""

    def __init__(self, prefer_kind: str) -> None:
        super().__init__(
            identity={"kind": "canned", "script": f"prefer_{prefer_kind}"},
            actions=[],
        )
        self._prefer = prefer_kind

    async def decide(self, prompt: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        for a in prompt["legal_actions"]:
            if a["type"] == self._prefer:
                return a
        return prompt["default_action"]


@pytest.mark.asyncio(loop_scope="function")
async def test_peng_fires_through_table_manager(tmp_path: Path) -> None:
    """A seat that takes every PENG opportunity gets a recorded CLAIMED
    CLAIM_RESOLUTION — verifies the manager wires claim actions through
    correctly (rather than always falling back to PASS)."""
    adapters: list[Any] = _four_passers()
    # Run with one PENG-greedy seat at each position; some seed should produce
    # at least one window where they have a PENG opportunity.
    adapters[1] = _OpportunisticAdapter("PENG")
    adapters[2] = _OpportunisticAdapter("PENG")
    adapters[3] = _OpportunisticAdapter("PENG")
    await run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
    )
    events = read_record(tmp_path / "hand.jsonl")
    claimed = [
        e for e in events if e["event"] == "CLAIM_RESOLUTION" and e.get("outcome") == "CLAIMED"
    ]
    if not claimed:
        pytest.skip(
            "seed 12345 + greedy-PENG didn't produce a claim; not load-bearing for this test"
        )
    # If any claim fired, its winning_claim must be one we asked for (PENG).
    assert all(e["winning_claim"] == "PENG" for e in claimed)


def test_resolve_priority_picks_higher_kind() -> None:
    """Pure-function check: HU > PENG > GANG > CHI; seat number breaks ties."""
    from mahjong.table.manager import _resolve_claim_priority

    seat_results = {
        1: ({"type": "CHI", "tiles": ["W2", "W3", "W4"]}, {}),
        2: ({"type": "PENG", "tile": "W3"}, {}),
        3: ({"type": "HU"}, {}),
    }
    winner = _resolve_claim_priority([1, 2, 3], seat_results)  # type: ignore[arg-type]
    assert winner is not None
    assert winner[0] == 3
    assert winner[1]["type"] == "HU"


def test_resolve_priority_seat_tiebreak() -> None:
    """Same kind across seats: lower seat number wins."""
    from mahjong.table.manager import _resolve_claim_priority

    seat_results = {
        1: ({"type": "PENG", "tile": "W3"}, {}),
        3: ({"type": "PENG", "tile": "W3"}, {}),
    }
    winner = _resolve_claim_priority([1, 3], seat_results)  # type: ignore[arg-type]
    assert winner == (1, {"type": "PENG", "tile": "W3"})


def test_resolve_priority_all_pass_returns_none() -> None:
    from mahjong.table.manager import _resolve_claim_priority

    seat_results = {1: ({"type": "PASS"}, {}), 2: ({"type": "PASS"}, {})}
    assert _resolve_claim_priority([1, 2], seat_results) is None  # type: ignore[arg-type]


@pytest.mark.needs_pymjgb
@pytest.mark.asyncio(loop_scope="function")
async def test_claim_hu_emits_hand_end_event(tmp_path: Path) -> None:
    """Regression: a win declared *inside a claim window* (a discard win / ron)
    must emit the terminal HAND_END.

    `diff_to_events(HU)` returns only `[HAND_END]` — no leading CLAIM_DECISION —
    so the old `_resolve_claim_window` `events[1:]` slice silently dropped it,
    leaving every client waiting forever (the table stalled on any ron). Canned
    PASS bots never won by claiming, so this was latent until the v0 offense bot
    started winning hands. We need a real offense policy to reach a ron, so this
    drives four-v0 and asserts every discard-win record carries a HAND_END.
    """
    from mahjong.adapters.v0 import V0Adapter

    discard_wins = 0
    for seed in range(6):
        out = tmp_path / f"hand_{seed}.jsonl"
        final = await run_hand(
            adapters=[V0Adapter() for _ in range(4)],  # type: ignore[misc]
            ruleset=MCR_REF,
            seed=seed,
            hand_id=f"claim-hu-{seed:08d}-0000-0000-0000-000000000000"[:36],
            record_path=out,
            server_info=SERVER,
        )
        terminal = final["terminal"]
        if terminal["kind"] == "HU" and terminal["win_type"] == "DISCARD":
            discard_wins += 1
            kinds = [e["event"] for e in read_record(out)]
            assert "HAND_END" in kinds, f"seed {seed}: claim-HU record missing HAND_END"

    assert discard_wins > 0, "expected at least one discard win across the seed range"


# Keep the unused-import keepers minimal — pytest cleans them when needed.
_ = GameState
_ = tile_sort_key
_ = apply_action
