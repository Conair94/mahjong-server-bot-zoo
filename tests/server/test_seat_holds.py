"""FB-03 rejoin discovery — ``TableRegistry.seat_holds_for`` (reconnect-rejoin.md).

The seat-hold state machine itself is covered by the session-mux suite; these
fixtures pin the *discovery* layer: after a client binds (and later drops) a
seat, the registry can tell a returning account which seats it holds and in
what state, so ``AUTH_RESPONSE.seat_holds[]`` can drive the lobby rejoin flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.server.registry import TableRegistry

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
SERVER_INFO: dict[str, Any] = {"version": "sh-test", "git_sha": "t", "host": "t"}


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


def _new_table(reg: TableRegistry, tmp_path: Path) -> str:
    # Default composition = 1 human (seat 0) + 3 bots.
    return reg.create_table_direct(
        ruleset=MCR_REF,
        seed=1,
        server_info=SERVER_INFO,
        data_dir=tmp_path,
        max_hands=None,
    )


async def test_no_holds_for_unknown_user(tmp_path: Path) -> None:
    reg = TableRegistry()
    _new_table(reg, tmp_path)
    assert reg.seat_holds_for("u_999") == []


async def test_live_seat_is_a_hold(tmp_path: Path) -> None:
    reg = TableRegistry()
    tid = _new_table(reg, tmp_path)
    handle = reg.get_table(tid)
    await handle.sessions.seat(0).attach(_Sink(), user_id="u_7")

    holds = reg.seat_holds_for("u_7")
    assert len(holds) == 1
    h = holds[0].to_wire()
    assert h["table_id"] == int(tid)
    assert h["seat"] == 0
    assert h["state"] == "LIVE"
    assert "rejoin_deadline_ms" not in h  # LIVE has no rejoin deadline


async def test_dropped_seat_becomes_held_with_deadline(tmp_path: Path) -> None:
    reg = TableRegistry()
    tid = _new_table(reg, tmp_path)
    handle = reg.get_table(tid)
    sink = _Sink()
    await handle.sessions.seat(0).attach(sink, user_id="u_7")

    # Socket drops → LIVE -> HELD, hold timer + deadline armed.
    await handle.sessions.seat(0).on_socket_dropped(sink)

    holds = reg.seat_holds_for("u_7")
    assert len(holds) == 1
    h = holds[0].to_wire()
    assert h["state"] == "HELD"
    assert isinstance(h["rejoin_deadline_ms"], int)
    assert h["rejoin_deadline_ms"] > 0


async def test_holds_span_multiple_tables(tmp_path: Path) -> None:
    reg = TableRegistry()
    t1 = _new_table(reg, tmp_path)
    t2 = _new_table(reg, tmp_path)
    await reg.get_table(t1).sessions.seat(0).attach(_Sink(), user_id="u_7")
    await reg.get_table(t2).sessions.seat(0).attach(_Sink(), user_id="u_7")
    # A different user at t2 should not bleed into u_7's holds — but seat 0 is
    # already taken, so bind a fresh table for the other user instead.
    t3 = _new_table(reg, tmp_path)
    await reg.get_table(t3).sessions.seat(0).attach(_Sink(), user_id="u_8")

    holds = reg.seat_holds_for("u_7")
    table_ids = sorted(h.table_id for h in holds)
    assert table_ids == sorted([int(t1), int(t2)])
    assert all(h.state == "LIVE" for h in holds)


async def test_resume_clears_the_hold_deadline(tmp_path: Path) -> None:
    reg = TableRegistry()
    tid = _new_table(reg, tmp_path)
    handle = reg.get_table(tid)
    sink = _Sink()
    await handle.sessions.seat(0).attach(sink, user_id="u_7")
    await handle.sessions.seat(0).on_socket_dropped(sink)
    assert reg.seat_holds_for("u_7")[0].state == "HELD"

    # Same user re-attaches (rejoin) → back to LIVE, no deadline.
    await handle.sessions.seat(0).attach(_Sink(), user_id="u_7")
    holds = reg.seat_holds_for("u_7")
    assert holds[0].state == "LIVE"
    assert holds[0].rejoin_deadline_ms is None
