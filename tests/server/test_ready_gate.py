"""FB-02: the between-hand ready-up gate (TableHandle._await_humans_ready).

The HAND_END summary used to flash for ~1s before the next hand auto-started. The
gate holds the next hand until every LIVE human acknowledges (READY), with a
timeout safety net so a disconnected / walked-away human can't stall forever, and
no gate at all for pure-bot tables.

These exercise the gate logic directly (``_live_human_seats`` patched to simulate a
LIVE human) — no full game/attach setup needed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import RuleSetRef
from mahjong.server.registry import TableHandle
from mahjong.server.seats import SeatComposition

pytestmark = pytest.mark.asyncio

_MCR: RuleSetRef = cast(
    RuleSetRef, {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
)
_SEATS = (
    SeatComposition("human"),
    SeatComposition("bot"),
    SeatComposition("bot"),
    SeatComposition("bot"),
)


def _handle(tmp_path: Path, *, ready_timeout_seconds: float) -> TableHandle:
    return TableHandle(
        table_id="77",
        ruleset=_MCR,
        seed=1,
        hand_id="t77-h0",
        record_path=tmp_path / "hand_0000.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seats=_SEATS,
        ready_timeout_seconds=ready_timeout_seconds,
    )


async def test_no_live_humans_advances_immediately(tmp_path):
    # Fresh handle: no human is LIVE → gate must not block at all.
    handle = _handle(tmp_path, ready_timeout_seconds=999.0)
    await asyncio.wait_for(handle._await_humans_ready(), timeout=1.0)


async def test_gate_times_out_when_human_never_readies(tmp_path, monkeypatch):
    handle = _handle(tmp_path, ready_timeout_seconds=0.1)
    monkeypatch.setattr(handle, "_live_human_seats", lambda: {0})
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await asyncio.wait_for(handle._await_humans_ready(), timeout=2.0)
    elapsed = loop.time() - t0
    # It waited (didn't advance instantly) but did eventually time out.
    assert 0.05 <= elapsed < 1.5


async def test_gate_advances_as_soon_as_human_readies(tmp_path, monkeypatch):
    handle = _handle(tmp_path, ready_timeout_seconds=30.0)  # long: must NOT wait it out
    monkeypatch.setattr(handle, "_live_human_seats", lambda: {0})

    gate = asyncio.ensure_future(handle._await_humans_ready())
    await asyncio.sleep(0.05)  # let the gate clear state + enter the wait
    assert not gate.done()  # still blocking on the un-readied human

    # Seat 0's READY arrives mid-gate.
    handle._ready_seats.add(0)
    handle._ready_changed.set()

    await asyncio.wait_for(gate, timeout=1.0)  # well under the 30s timeout


async def test_mark_ready_ignores_non_human_and_unknown(tmp_path, monkeypatch):
    handle = _handle(tmp_path, ready_timeout_seconds=30.0)

    class _Conn:
        pass

    # Unknown connection → no seat → ignored, no crash.
    handle._mark_ready(_Conn())
    assert handle._ready_seats == set()

    # A recognised human seat → recorded + wakes the gate.
    monkeypatch.setattr(handle, "_seat_for_conn", lambda _c: 0)
    handle._mark_ready(_Conn())
    assert handle._ready_seats == {0}
    assert handle._ready_changed.is_set()
