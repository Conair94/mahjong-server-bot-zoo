"""FB-17 — a reconnect/refresh must receive the *current* game state.

Root cause (2026-06-12 live game `20260612T154802Z-ef44e5-t1-h0`):
``TableHandle._snapshot_provider`` projected ``self._initial_state`` — the
deal — so any mid-hand refresh rebuilt the client from the original 13 tiles
(phantom un-discardable tiles, "new hand dealt the same tiles" after refresh).
Spec contract: docs/specs/session-mux.md fixture 3 — the snapshot "matches
current ``project(state, seat)``".

Three fixtures (feedback-backlog.md § FB-17):

1. Mid-hand drop + resume → snapshot reflects play so far, zero EVENT replay.
2. Mid-hand same-user takeover (refresh race: new socket while old is LIVE)
   → same current-snapshot guarantee.
3. Post-HAND_END reattach → snapshot carries the terminal (with the
   ``final_hands`` settlement reveal), not the long-gone deal.

The table is driven through the real hand loop: one human seat answering
every PROMPT with its ``default_action``, three CannedAdapter PASS bots.
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
from tests.sessions.conftest import FakeSink

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
_IDENTITY: dict[str, Any] = {"kind": "human", "user_id": "u_1", "display": "Ann"}


def _handle(tmp_path: Path) -> TableHandle:
    return TableHandle(
        table_id="9",
        ruleset=_MCR,
        seed=777,
        hand_id="t9-h0",
        record_path=tmp_path / "hand_0000.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
        seats=_SEATS,
        # Empty scripts → CannedAdapter PASS bots: deterministic, no v0 logic.
        canned_seat_actions={1: [], 2: [], 3: []},
        max_hands=1,
    )


async def _pump_until(
    handle: TableHandle,
    sink: FakeSink,
    pred: Any,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Answer every PROMPT on *sink* with its default action until a message
    satisfying *pred* appears (returns it), or fail on timeout."""
    seen = 0
    answered: set[str] = set()
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        while seen < len(sink.messages):
            msg = sink.messages[seen]
            seen += 1
            if msg.get("kind") == "PROMPT" and msg["prompt_id"] not in answered:
                answered.add(msg["prompt_id"])
                await handle.handle_inbound(
                    sink,
                    {
                        "kind": "ACTION",
                        "prompt_id": msg["prompt_id"],
                        "action": msg["default_action"],
                    },
                )
            if pred(msg):
                return msg
        await asyncio.sleep(0.01)
    raise AssertionError("timed out pumping the hand")


def _is_own_discard_event(msg: dict[str, Any]) -> bool:
    return (
        msg.get("kind") == "EVENT"
        and msg.get("event", {}).get("event") == "DISCARD"
        and msg["event"].get("seat") == 0
    )


# ---------------------------------------------------------------------------
# Fixture 1 — mid-hand drop + resume
# ---------------------------------------------------------------------------


async def test_mid_hand_resume_snapshot_is_current_not_the_deal(tmp_path) -> None:
    handle = _handle(tmp_path)
    sink_a = FakeSink()
    assert await handle.attach(sink_a, identity=_IDENTITY, seat=0)
    deal_snapshot = sink_a.by_kind("ATTACHED")[0]["snapshot"]
    assert deal_snapshot["seats"][0]["discards"] == []  # pre-hand: the deal

    outcome = await handle.start_hand(sink_a)
    assert outcome.ok

    # Play until our own first discard is echoed back.
    await _pump_until(handle, sink_a, _is_own_discard_event)

    # Refresh: socket drops, new connection resumes the held seat.
    await handle.on_socket_dropped(sink_a)
    sink_b = FakeSink()
    assert await handle.attach(sink_b, identity=_IDENTITY, seat=0)

    attached = sink_b.by_kind("ATTACHED")[0]
    snap = attached["snapshot"]
    assert snap["seats"][0]["discards"], "resume snapshot must reflect play so far, not the deal"
    assert snap["turn_index"] > 0
    assert attached["resume_buffer_size"] == 0
    assert sink_b.by_kind("EVENT") == []  # no replay on top of a current snapshot

    await handle.close(reason="test_done")


# ---------------------------------------------------------------------------
# Fixture 2 — mid-hand same-user takeover (the refresh race)
# ---------------------------------------------------------------------------


async def test_mid_hand_takeover_snapshot_is_current(tmp_path) -> None:
    handle = _handle(tmp_path)
    sink_a = FakeSink()
    assert await handle.attach(sink_a, identity=_IDENTITY, seat=0)
    assert (await handle.start_hand(sink_a)).ok
    await _pump_until(handle, sink_a, _is_own_discard_event)

    # New socket while the old one is still LIVE → same-user takeover.
    sink_b = FakeSink()
    assert await handle.attach(sink_b, identity=_IDENTITY, seat=0)

    snap = sink_b.by_kind("ATTACHED")[0]["snapshot"]
    assert snap["seats"][0]["discards"], "takeover snapshot must reflect play so far, not the deal"
    assert snap["turn_index"] > 0

    await handle.close(reason="test_done")


# ---------------------------------------------------------------------------
# Fixture 3 — post-HAND_END reattach shows the ended hand, not the deal
# ---------------------------------------------------------------------------


async def test_post_hand_end_resume_snapshot_is_terminal(tmp_path) -> None:
    handle = _handle(tmp_path)
    sink_a = FakeSink()
    assert await handle.attach(sink_a, identity=_IDENTITY, seat=0)
    assert (await handle.start_hand(sink_a)).ok

    # Default actions all the way down → the hand runs to its terminal.
    await _pump_until(
        handle,
        sink_a,
        lambda m: m.get("kind") == "HAND_END",
        timeout=60.0,
    )

    # Refresh after the hand ended (the FB-17 report: this used to re-show
    # the original deal, reading as "new hand with the exact same tiles").
    await handle.on_socket_dropped(sink_a)
    sink_b = FakeSink()
    assert await handle.attach(sink_b, identity=_IDENTITY, seat=0)

    snap = sink_b.by_kind("ATTACHED")[0]["snapshot"]
    assert snap["terminal"] is not None, "post-hand snapshot must be terminal"
    assert snap["phase"] == "TERMINAL"
    hands = snap["terminal"]["final_hands"]
    assert [h["seat"] for h in hands] == [0, 1, 2, 3]

    await handle.close(reason="test_done")
