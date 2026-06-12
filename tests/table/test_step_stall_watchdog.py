"""Table manager — per-step stall watchdog.

Born from the 2026-06-11 live freeze (two tables dead-stopped mid-hand at a
pending prompt with no timeout firing and nothing logged — see
docs/specs/feedback-backlog.md FB-13 / DEF-12). Every decide/observe await in
`run_hand` is individually bounded, yet the live hand task parked forever, so
the contract pinned here is one level up: **no single phase step may exceed a
hard wall-clock cap**. On breach the manager logs `hand_step_stalled [DEF-12]`
with the step's pending coroutine frames (the artifact the parked
investigation needs) and raises `HandStepStalled`, which the orchestrator
crash guards already convert into a graceful table teardown.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mahjong.adapters.canned import CannedAdapter
from mahjong.engine.rulesets import MANIFEST
from mahjong.table import manager as mgr
from mahjong.table.manager import DecideTimeouts, HandStepStalled, run_hand

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


async def _run(tmp_path: Path, **kwargs: Any) -> Any:
    return await run_hand(
        adapters=kwargs.pop("adapters", _four_passers()),
        ruleset=MCR_REF,
        seed=12345,
        hand_id="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        record_path=tmp_path / "hand.jsonl",
        server_info=SERVER,
        decide_timeout_seconds=0.05,
        **kwargs,
    )


# --- Stall detection ---


@pytest.mark.asyncio(loop_scope="function")
async def test_stalled_step_logs_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A step that exceeds the cap aborts the hand with `HandStepStalled` and
    logs `hand_step_stalled [DEF-12]` with hand_id + the stuck frames."""

    async def _hung_step(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30.0)

    monkeypatch.setattr(mgr, "_step_discard", _hung_step)

    with (
        caplog.at_level("ERROR", logger="mahjong.table.manager"),
        pytest.raises(HandStepStalled),
    ):
        await asyncio.wait_for(
            _run(tmp_path, step_stall_seconds=0.1),
            timeout=5.0,  # the watchdog, not this outer guard, must fire
        )

    stall_lines = [r.message for r in caplog.records if "hand_step_stalled" in r.message]
    assert stall_lines, "expected a hand_step_stalled log line"
    assert "[DEF-12]" in stall_lines[0]
    assert "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f" in stall_lines[0]
    # The pending-stack summary must point at the coroutine that wedged.
    assert "_hung_step" in stall_lines[0]


@pytest.mark.asyncio(loop_scope="function")
async def test_stall_that_swallows_cancellation_still_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The watchdog must not trust cancellation: a step that swallows
    CancelledError (the suspected live-freeze shape) still gets reported and
    the hand still aborts — with the uncancellable escalation logged."""

    async def _uncancellable_step(*args: Any, **kwargs: Any) -> Any:
        try:
            await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            # Deliberately hostile: survive the watchdog's cancel and stay
            # pending past its grace window — but finish on our own shortly
            # after, so the test loop can still tear down.
            await asyncio.sleep(0.5)
            raise

    monkeypatch.setattr(mgr, "_step_discard", _uncancellable_step)

    with (
        caplog.at_level("ERROR", logger="mahjong.table.manager"),
        pytest.raises(HandStepStalled),
    ):
        await asyncio.wait_for(_run(tmp_path, step_stall_seconds=0.1), timeout=5.0)

    messages = [r.message for r in caplog.records]
    assert any("hand_step_stalled" in m for m in messages)
    assert any("hand_step_stall_uncancellable" in m for m in messages)


# --- Cap derivation ---


def test_default_cap_scales_with_decide_timeouts() -> None:
    """Default cap = 4 × the largest configured decide deadline + 60 s grace,
    so an operator who raises human timeouts never gets spurious aborts."""
    assert mgr._stall_cap_seconds(DecideTimeouts.uniform(30.0)) == pytest.approx(180.0)
    assert mgr._stall_cap_seconds(
        DecideTimeouts(human_discard_s=60.0, human_claim_s=20.0, bot_s=30.0)
    ) == pytest.approx(300.0)


# --- Happy path is unaffected ---


@pytest.mark.asyncio(loop_scope="function")
async def test_normal_hand_completes_under_watchdog(tmp_path: Path) -> None:
    state = await _run(tmp_path)  # step_stall_seconds defaults (derived cap)
    assert state["terminal"] is not None
