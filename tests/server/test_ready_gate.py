"""FB-02 / FB-19: the between-hand ready-up gate (TableHandle._await_humans_ready).

The HAND_END summary used to flash for ~1s before the next hand auto-started. The
gate holds the next hand until every *gated* human acknowledges (READY), with a
timeout safety net so a disconnected / walked-away human can't stall forever, and
no gate at all for pure-bot tables.

FB-19 widened "gated" from LIVE-only to **LIVE or HELD**: a player who is
mid-refresh (HELD) at gate time must not be skipped, or the next hand starts the
instant the *other* human readies and steamrolls the returning player. It also
added gate open/advance logging so a long or vacuous gate is attributable.

The gate-mechanics tests exercise the loop directly (``_gated_human_seats``
patched to simulate state); the HELD tests drive a *real* seat to HELD via
attach + socket-drop so they pin the actual predicate.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import RuleSetRef
from mahjong.server.registry import TableHandle
from mahjong.server.seats import SeatComposition
from mahjong.sessions.mux import SeatState

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
_SEATS_2H = (
    SeatComposition("human"),
    SeatComposition("human"),
    SeatComposition("bot"),
    SeatComposition("bot"),
)


class _Sink:
    """Minimal OutboundSink double — swallows sends, tracks closed-ness."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._closed = False

    async def send(self, msg: Mapping[str, Any]) -> None:
        self.messages.append(dict(msg))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


def _handle(
    tmp_path: Path,
    *,
    ready_timeout_seconds: float,
    seats: tuple[SeatComposition, ...] = _SEATS,
) -> TableHandle:
    return TableHandle(
        table_id="77",
        ruleset=_MCR,
        seed=1,
        hand_id="t77-h0",
        record_path=tmp_path / "hand_0000.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seats=seats,
        ready_timeout_seconds=ready_timeout_seconds,
    )


# --- FB-02 gate mechanics ---------------------------------------------------


async def test_no_live_humans_advances_immediately(tmp_path):
    # Fresh handle: no human is LIVE/HELD → gate must not block at all.
    handle = _handle(tmp_path, ready_timeout_seconds=999.0)
    await asyncio.wait_for(handle._await_humans_ready(), timeout=1.0)


async def test_gate_times_out_when_human_never_readies(tmp_path, monkeypatch):
    handle = _handle(tmp_path, ready_timeout_seconds=0.1)
    monkeypatch.setattr(handle, "_gated_human_seats", lambda: {0})
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await asyncio.wait_for(handle._await_humans_ready(), timeout=2.0)
    elapsed = loop.time() - t0
    # It waited (didn't advance instantly) but did eventually time out.
    assert 0.05 <= elapsed < 1.5


async def test_gate_advances_as_soon_as_human_readies(tmp_path, monkeypatch):
    handle = _handle(tmp_path, ready_timeout_seconds=30.0)  # long: must NOT wait it out
    monkeypatch.setattr(handle, "_gated_human_seats", lambda: {0})

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


# --- FB-19 soft spot 2: a HELD (refreshing) human is gated, not skipped -----


async def test_held_human_is_in_the_gated_set(tmp_path):
    """A player mid-refresh shows as HELD; the gate must still wait for them.
    Pre-FB-19 ``_live_human_seats`` returned only {0}, so seat 1 was skipped."""
    handle = _handle(tmp_path, ready_timeout_seconds=30.0, seats=_SEATS_2H)
    s0 = _Sink()
    await handle.sessions.seat(0).attach(s0, user_id="u0")  # LIVE
    s1 = _Sink()
    await handle.sessions.seat(1).attach(s1, user_id="u1")
    await handle.sessions.seat(1).on_socket_dropped(s1)  # LIVE -> HELD

    assert handle.sessions.seat(1).state is SeatState.HELD
    assert handle._gated_human_seats() == {0, 1}
    assert not handle._all_gated_humans_ready()


async def test_gate_holds_for_a_held_human_until_they_ready(tmp_path):
    """With seat 0 LIVE and seat 1 HELD, seat 0 readying alone must NOT advance
    the gate (the pre-FB-19 vacuous-skip bug); it advances only once the
    returning HELD player resumes and readies too."""
    handle = _handle(tmp_path, ready_timeout_seconds=30.0, seats=_SEATS_2H)
    s0 = _Sink()
    await handle.sessions.seat(0).attach(s0, user_id="u0")
    s1 = _Sink()
    await handle.sessions.seat(1).attach(s1, user_id="u1")
    await handle.sessions.seat(1).on_socket_dropped(s1)  # seat 1 -> HELD

    gate = asyncio.ensure_future(handle._await_humans_ready())
    await asyncio.sleep(0.05)

    # Only seat 0 readies. Under the old LIVE-only gate, seat 1 (HELD) was not
    # gated, so this alone would advance — assert it does NOT.
    handle._ready_seats.add(0)
    handle._ready_changed.set()
    await asyncio.sleep(0.05)
    assert not gate.done()

    # The HELD player resumes + readies → gate advances, well under the timeout.
    handle._ready_seats.add(1)
    handle._ready_changed.set()
    await asyncio.wait_for(gate, timeout=1.0)


# --- FB-19 soft spot 3: the gate logs why it advanced ----------------------


async def test_gate_logs_vacuous_advance(tmp_path, caplog):
    handle = _handle(tmp_path, ready_timeout_seconds=30.0)  # no humans
    with caplog.at_level(logging.INFO):
        await handle._await_humans_ready()
    assert any(
        "ready_gate_advanced" in r.getMessage() and "reason=vacuous" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


async def test_gate_logs_timeout_advance(tmp_path, monkeypatch, caplog):
    handle = _handle(tmp_path, ready_timeout_seconds=0.1)
    monkeypatch.setattr(handle, "_gated_human_seats", lambda: {0})
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(handle._await_humans_ready(), timeout=2.0)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("ready_gate_opened" in m and "waiting_on=[0]" in m for m in msgs), msgs
    assert any("ready_gate_advanced" in m and "reason=timeout" in m for m in msgs), msgs
