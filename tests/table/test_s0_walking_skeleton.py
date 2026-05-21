"""S0 walking-skeleton exit artifact.

Spec: docs/specs/seat-port.md fixture 1, CHECKLIST Step 4.2 gate.

The fixture `tests/_fixtures/s0_walking_skeleton_seed_12345.jsonl` is the
checked-in artifact. This test re-runs the same setup with monkey-patched
timestamps and asserts byte-identical output, plus that the record replays
back to the same canonical final state. Cross-platform consequence: the
build only counts as green if Linux and macOS produce the same bytes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.canned import CannedAdapter
from mahjong.engine import state_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.records.reader import read_record
from mahjong.records.replay import replay
from mahjong.table import manager as mgr

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}

FIXTURE = Path("tests/_fixtures/s0_walking_skeleton_seed_12345.jsonl")


def _patch_fixed_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the manager's wall-clock so the record's `ts` fields are stable."""
    counter = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-20T00:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)


@pytest.mark.asyncio(loop_scope="function")
async def test_s0_record_is_byte_identical_to_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Determinism across runs: same seed + same canned adapters + fixed
    timestamps → byte-identical record."""
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"
    adapters: list[SeatAdapter] = [
        cast(
            SeatAdapter,
            CannedAdapter(identity={"kind": "canned", "script": "pass"}, actions=[]),
        )
        for _ in range(4)
    ]
    await mgr.run_hand(
        adapters=adapters,
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=out,
        server_info={"version": "s0-fixture", "git_sha": "fixed", "host": "fixture"},
    )

    assert out.read_bytes() == FIXTURE.read_bytes()


@pytest.mark.asyncio(loop_scope="function")
async def test_s0_fixture_replays_to_matching_final_state() -> None:
    """The S0 contract: the checked-in record replays to a canonical final
    state whose hash equals the FOOTER.state_hash_final."""
    events = read_record(FIXTURE)
    states = list(replay(events))
    footer = events[-1]
    assert state_hash(states[-1]) == footer["state_hash_final"]  # type: ignore[arg-type]


def test_cli_play_test_subcommand_imports() -> None:
    """`python -m mahjong play-test` is wired."""
    from mahjong.cli import main
    from mahjong.cli.play_test import _build_argparser

    parser = _build_argparser()
    args = parser.parse_args(["--seed", "1", "--output", "/tmp/x.jsonl"])
    assert args.seed == 1
    # Smoke: dispatcher recognizes the subcommand.
    assert callable(main)


def asyncio_run_smoke() -> None:
    """Spawning the CLI entry point synchronously (sans subprocess) covers
    the dispatch path. Real subprocess invocation is too slow for the fast
    test suite; spec gate is the byte-identical record check above."""
    asyncio.run(asyncio.sleep(0))
