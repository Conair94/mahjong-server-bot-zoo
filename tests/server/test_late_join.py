"""Late-join refusal (Layer-8 close-out §4, spec: ``docs/specs/late-join-replay.md``
Alternative A).

When a table's hand is ``IN_PROGRESS`` and someone attempts ``ATTACH`` to a
human seat that is currently ``UNBOUND`` (never bound, or HELD-then-expired),
the server replies with ``ERROR { code: "hand_in_progress" }`` and does not
proceed to ``ATTACHED``.

Why a unit-level test rather than a wire-level e2e: the ``UNBOUND``-while-
``IN_PROGRESS`` state requires either (a) the seat-hold to expire mid-hand,
which would force the test to wait on a real hold timer, or (b) a future
"start hand without all humans present" feature that doesn't exist yet.
A unit test against ``TableHandle.attach`` directly is sharper and faster
than reaching that state via the wire.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import RuleSetRef
from mahjong.server.registry import TableHandle
from mahjong.server.seats import SeatComposition

pytestmark = pytest.mark.asyncio


_MCR: RuleSetRef = cast(
    RuleSetRef,
    {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]},
)

_SEATS_2H2B = (
    SeatComposition("human"),
    SeatComposition("human"),
    SeatComposition("bot"),
    SeatComposition("bot"),
)


class _RecordingConn:
    """Minimal Connection stand-in: captures everything sent via ``send``."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, frame: dict[str, Any]) -> None:
        self.sent.append(frame)


def _make_handle(tmp_path: Path) -> TableHandle:
    return TableHandle(
        table_id="42",
        ruleset=_MCR,
        seed=1,
        hand_id="t42-h0",
        record_path=tmp_path / "hand_0000.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seats=_SEATS_2H2B,
    )


def _mark_in_progress(handle: TableHandle) -> asyncio.Future[Any]:
    """Force the handle's phase to IN_PROGRESS by attaching a never-completing
    Future as the hand task.  Returns the future so the test can cancel it
    during teardown."""
    loop = asyncio.get_event_loop()
    forever: asyncio.Future[Any] = loop.create_future()
    handle._hand_task = forever  # type: ignore[attr-defined]
    return forever


# --- Fixture A: attach refused on UNBOUND seat while IN_PROGRESS ----------


async def test_attach_during_in_progress_returns_hand_in_progress(
    tmp_path: Path,
) -> None:
    handle = _make_handle(tmp_path)
    forever = _mark_in_progress(handle)
    try:
        assert handle.summary().phase == "IN_PROGRESS"

        conn = _RecordingConn()
        result = await handle.attach(
            conn,
            identity={"kind": "human", "user_id": "alice", "display": "Alice"},
            seat=1,
        )

        assert result is False
        assert len(conn.sent) == 1, conn.sent
        err = conn.sent[0]
        assert err["kind"] == "ERROR"
        assert err["code"] == "hand_in_progress"
        # The message is informational but mentioning the table_id + seat
        # gives the client something to surface; keep it pinned.
        assert "42" in err.get("message", "")
        assert "1" in err.get("message", "")
    finally:
        forever.cancel()


# --- Fixture B: attach succeeds while WAITING_FOR_PLAYERS -----------------


async def test_attach_succeeds_when_waiting_for_players(tmp_path: Path) -> None:
    """The same attach succeeds when the table is not IN_PROGRESS — proves
    the check is gated on phase, not always firing."""
    handle = _make_handle(tmp_path)
    assert handle.summary().phase == "WAITING_FOR_PLAYERS"

    conn = _RecordingConn()
    result = await handle.attach(
        conn,
        identity={"kind": "human", "user_id": "alice", "display": "Alice"},
        seat=1,
    )

    # Real session-mux ATTACH path runs; we don't assert on its outcome
    # frame contents (covered by attach_widening tests).  What matters is
    # that the late-join gate did *not* fire — no `hand_in_progress` error.
    sent_codes = [f.get("code") for f in conn.sent if f.get("kind") == "ERROR"]
    assert "hand_in_progress" not in sent_codes
    # And in this scenario the attach actually completes successfully:
    assert result is True


# --- Fixture C: bot-seat rejection still wins over late-join check --------


async def test_bot_seat_rejection_precedes_in_progress_check(
    tmp_path: Path,
) -> None:
    """A bot-seat attach attempt during IN_PROGRESS returns ``seat_not_yours``
    (the bot-seat guard fires first), not ``hand_in_progress``.  Order of
    checks is load-bearing: bot seats are *never* attachable, regardless of
    phase, and the existing error code is the right one for that case."""
    handle = _make_handle(tmp_path)
    forever = _mark_in_progress(handle)
    try:
        conn = _RecordingConn()
        result = await handle.attach(
            conn,
            identity={"kind": "human", "user_id": "alice", "display": "Alice"},
            seat=2,  # bot seat
        )

        assert result is False
        assert len(conn.sent) == 1
        err = conn.sent[0]
        assert err["code"] == "seat_not_yours"
    finally:
        forever.cancel()
