"""Step 8.7.f — multi-human end-to-end + persistence cross-check.

Verification fixtures from ``docs/specs/multi-human-seats.md §
Verification fixtures``:

19. Two-human full hand (load-bearing exit gate).  Two authenticated
    clients create a 2H+2B table, both attach, one issues START_HAND,
    the hand runs to terminal.  Persistence ``hand_index`` row has
    ``participants[0..1].account_id`` populated, ``[2..3]`` NULL +
    ``seat_kind == "canned"``; the record file is replayable; per-seat
    projection privacy holds (alice never sees seat-1+ concealed tile
    lists in her *in-hand* event stream, and vice versa — HAND_END's
    ``final_hands`` reveal is exempt: MCR settlements show every hand
    for scoring transparency).

20. Single-human regression: covered by
    ``tests/server/test_persistence_wiring.py::test_persistence_wiring_records_hand_for_account``
    after 8.7.d updated it to send an explicit ``START_HAND``.  The
    seats-omitted default composition is independently exercised by
    ``tests/server/test_seat_composition.py::test_fixture_1_default_composition``.

21. Disconnect of one human mid-hand.  Composes session-mux fixtures 7
    (HELD → resume replay) and 8 (same-user takeover) under a running
    2H+2B hand.  Two sub-cases:
    - 21a: bob drops after his first PROMPT, reconnects inside the
      hold window, the resumed session replays the pending prompt,
      bob acts, the hand reaches HAND_END.
    - 21b: bob drops and never reconnects; the seat-hold expires; the
      table manager's strike path swaps in ``AutoPassAdapter`` for seat
      1 and the hand finishes without him.

22. ``find_hands_by_account`` returns the fixture-19 hand for *both*
    human accounts and an empty list for a nonexistent account_id.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.engine.rulesets import MANIFEST
from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.records.replay import replay
from mahjong.server.orchestrator import MultiTableOrchestrator
from mahjong.table import manager as mgr

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
SEED = 77_777
SERVER_INFO: dict[str, Any] = {
    "version": "mh-e2e",
    "git_sha": "test",
    "host": "test",
}


def _fixed_ts(counter: dict[str, int]):
    def make() -> str:
        counter["i"] += 1
        return f"2026-05-26T18:00:00.{counter['i']:03d}Z"

    return make


async def _auth(ws: Any, *, username: str, password: str) -> dict[str, Any]:
    await ws.send(
        json.dumps(
            {"kind": "AUTH_REQUEST", "username": username, "password": password}
        )
    )
    resp = json.loads(cast(str, await ws.recv()))
    assert resp["kind"] == "AUTH_RESPONSE" and resp.get("ok"), resp
    return cast(dict[str, Any], resp)


def _violates_seat_privacy(
    msg: Any, *, owning_seat: int, found: list[tuple[int, list[str]]]
) -> None:
    """Recursively scan *msg* and append privacy violations to *found*.

    A violation is any seat dict whose ``seat`` is not *owning_seat* but
    whose ``concealed`` field is a list of tile strings (the projection
    should have collapsed it to ``{"count": N}`` for non-owning seats).
    """
    if isinstance(msg, dict):
        seat = msg.get("seat")
        concealed = msg.get("concealed")
        if (
            isinstance(seat, int)
            and seat != owning_seat
            and isinstance(concealed, list)
            and all(isinstance(t, str) for t in concealed)
        ):
            found.append((seat, concealed))
        for v in msg.values():
            _violates_seat_privacy(v, owning_seat=owning_seat, found=found)
    elif isinstance(msg, list):
        for v in msg:
            _violates_seat_privacy(v, owning_seat=owning_seat, found=found)


async def test_fixture_19_and_22_two_human_full_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two humans play a full hand to terminal on a 2H+2B table; persistence
    rows reflect the composition, the record is replayable, and per-seat
    projection privacy is preserved on both clients' event streams.
    """
    monkeypatch.setattr(mgr, "_now_ts", _fixed_ts({"i": 0}))

    (tmp_path / "records").mkdir(exist_ok=True)
    persistence = Persistence(tmp_path / "mahjong.db", tmp_path)

    alice_id = create_account(
        persistence._conn,
        username="alice",
        display_name="Alice",
        kind="human",
        role="admin",  # admin so CREATE_TABLE is permitted
        password="alicealice",
    )
    bob_id = create_account(
        persistence._conn,
        username="bob",
        display_name="Bob",
        kind="human",
        role="user",
        password="bobbobbobbob",
    )

    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=SEED,
        server_info=SERVER_INFO,
        between_hand_pause_seconds=0.05,
        persistence=persistence,
    )
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    try:
        # Alice (admin) creates the 2H+2B table.
        async with websockets.connect(url, subprotocols=["mahjong-v1"]) as setup_ws:
            await setup_ws.recv()  # HELLO
            await _auth(setup_ws, username="alice", password="alicealice")
            await setup_ws.send(
                json.dumps(
                    {
                        "kind": "CREATE_TABLE",
                        "ruleset": "mcr-2006",
                        "seats": [
                            {"kind": "human"},
                            {"kind": "human"},
                            {"kind": "bot"},
                            {"kind": "bot"},
                        ],
                    }
                )
            )
            created = json.loads(cast(str, await setup_ws.recv()))
            assert created["kind"] == "TABLE_CREATED", created
            table_id = int(created["table_id"])

        # Open two play sockets; both AUTH, both ATTACH (gated so alice waits
        # for bob to attach), then alice issues START_HAND.
        alice_received: list[dict[str, Any]] = []
        bob_received: list[dict[str, Any]] = []
        alice_ready = asyncio.Event()
        bob_ready = asyncio.Event()

        async def run_alice() -> None:
            async with websockets.connect(
                url, subprotocols=["mahjong-v1"]
            ) as ws:
                await ws.recv()  # HELLO
                await _auth(ws, username="alice", password="alicealice")
                # Wait for bob to be attached before triggering START_HAND.
                await bob_ready.wait()
                # Now attach; the seat-1 session is already LIVE.
                await ws.send(
                    json.dumps(
                        {"kind": "ATTACH", "table_id": table_id, "seat": 0}
                    )
                )
                attached = json.loads(cast(str, await ws.recv()))
                assert attached["kind"] == "ATTACHED"
                alice_received.append(attached)
                # Both humans LIVE; ignite.
                await ws.send(
                    json.dumps(
                        {"kind": "START_HAND", "table_id": table_id}
                    )
                )
                alice_ready.set()
                deadline = asyncio.get_event_loop().time() + 120.0
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    assert remaining > 0, "alice timed out before HAND_END"
                    msg = json.loads(
                        cast(
                            str,
                            await asyncio.wait_for(ws.recv(), timeout=remaining),
                        )
                    )
                    alice_received.append(msg)
                    if msg.get("kind") == "PROMPT":
                        await ws.send(
                            json.dumps(
                                {
                                    "kind": "ACTION",
                                    "prompt_id": msg["prompt_id"],
                                    "action": msg["default_action"],
                                }
                            )
                        )
                    elif msg.get("kind") == "HAND_END":
                        return

        async def run_bob() -> None:
            async with websockets.connect(
                url, subprotocols=["mahjong-v1"]
            ) as ws:
                await ws.recv()  # HELLO
                await _auth(ws, username="bob", password="bobbobbobbob")
                await ws.send(
                    json.dumps(
                        {"kind": "ATTACH", "table_id": table_id, "seat": 1}
                    )
                )
                attached = json.loads(cast(str, await ws.recv()))
                assert attached["kind"] == "ATTACHED"
                bob_received.append(attached)
                bob_ready.set()
                deadline = asyncio.get_event_loop().time() + 120.0
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    assert remaining > 0, "bob timed out before HAND_END"
                    msg = json.loads(
                        cast(
                            str,
                            await asyncio.wait_for(ws.recv(), timeout=remaining),
                        )
                    )
                    bob_received.append(msg)
                    if msg.get("kind") == "PROMPT":
                        await ws.send(
                            json.dumps(
                                {
                                    "kind": "ACTION",
                                    "prompt_id": msg["prompt_id"],
                                    "action": msg["default_action"],
                                }
                            )
                        )
                    elif msg.get("kind") == "HAND_END":
                        return

        await asyncio.wait_for(
            asyncio.gather(run_alice(), run_bob()),
            timeout=180.0,
        )

        # Let the finally-block in TableHandle finalise the persistence row.
        await asyncio.sleep(0.2)

        # --- Persistence assertions (fixture 19 + 22) -------------------

        hands_alice = persistence.find_hands_by_account(alice_id)
        hands_bob = persistence.find_hands_by_account(bob_id)
        hands_nonexistent = persistence.find_hands_by_account(999_999)

        assert len(hands_alice) == 1, f"alice should have 1 hand: {hands_alice}"
        assert len(hands_bob) == 1, f"bob should have 1 hand: {hands_bob}"
        assert hands_alice[0].hand_id == hands_bob[0].hand_id, (
            "alice and bob should share the same hand_id"
        )
        assert hands_nonexistent == [], (
            f"nonexistent account should have no hands: {hands_nonexistent}"
        )

        row = hands_alice[0]
        assert row.terminal_kind in {"HU", "EXHAUSTIVE_DRAW"}, row
        assert row.ended_at_ms is not None
        assert row.record_checksum and row.record_checksum.startswith("sha256:")

        full = persistence.get_hand(row.hand_id)
        assert full is not None
        seat0 = next(p for p in full.participants if p.seat == 0)
        seat1 = next(p for p in full.participants if p.seat == 1)
        assert seat0.account_id == alice_id and seat0.seat_kind == "human"
        assert seat1.account_id == bob_id and seat1.seat_kind == "human"
        for seat in (2, 3):
            other = next(p for p in full.participants if p.seat == seat)
            assert other.account_id is None
            assert other.seat_kind == "canned"

        # --- Record replayability (fixture 19) --------------------------

        record_path = tmp_path / row.record_path
        assert record_path.exists(), f"record file missing: {record_path}"
        events: list[dict[str, Any]] = []
        with record_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))
        # `replay()` will raise if any event is malformed or applies an
        # illegal transition; consuming the iterator drives all engine steps.
        states = list(replay(events))
        assert len(states) >= 1, "replay produced no states"

        # --- Per-seat projection privacy (fixture 19) -------------------
        #
        # Privacy applies *during the hand*.  ``HAND_END.final_hands`` is a
        # terminal reveal (all four hands are visible for scoring
        # transparency, as in any face-up MCR settlement) so we exclude it
        # from the scan.

        def _scan_for_violations(
            received: list[dict[str, Any]], owning_seat: int
        ) -> list[tuple[int, list[str]]]:
            found: list[tuple[int, list[str]]] = []
            for m in received:
                if m.get("kind") == "HAND_END":
                    continue
                _violates_seat_privacy(m, owning_seat=owning_seat, found=found)
            return found

        alice_violations = _scan_for_violations(alice_received, owning_seat=0)
        assert not alice_violations, (
            f"alice saw {len(alice_violations)} other-seat concealed lists "
            f"during the hand; first: {alice_violations[0]}"
        )

        bob_violations = _scan_for_violations(bob_received, owning_seat=1)
        assert not bob_violations, (
            f"bob saw {len(bob_violations)} other-seat concealed lists "
            f"during the hand; first: {bob_violations[0]}"
        )
    finally:
        await orch.close()
        persistence.close()


# ===========================================================================
# Fixture 21 — Disconnect of one human mid-hand
# ===========================================================================
#
# Shared setup helper: create two accounts, spin up an orchestrator with the
# given decide/hold/strike knobs, create a 2H+2B table.  Each sub-case below
# drives the hand its own way.


async def _setup_2h2b(
    *,
    tmp_path: Path,
    decide_timeout_seconds: float,
    hold_seconds: float,
    strike_limit: int,
) -> tuple[MultiTableOrchestrator, Persistence, int, int, int]:
    """Returns (orch, persistence, alice_id, bob_id, table_id)."""
    (tmp_path / "records").mkdir(exist_ok=True)
    persistence = Persistence(tmp_path / "mahjong.db", tmp_path)

    alice_id = create_account(
        persistence._conn,
        username="alice",
        display_name="Alice",
        kind="human",
        role="admin",
        password="alicealice",
    )
    bob_id = create_account(
        persistence._conn,
        username="bob",
        display_name="Bob",
        kind="human",
        role="user",
        password="bobbobbobbob",
    )

    orch = MultiTableOrchestrator(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        ruleset=MCR_REF,
        seed=SEED,
        server_info=SERVER_INFO,
        between_hand_pause_seconds=0.05,
        decide_timeout_seconds=decide_timeout_seconds,
        hold_seconds=hold_seconds,
        strike_limit=strike_limit,
        persistence=persistence,
    )
    await orch.start()
    url = f"ws://127.0.0.1:{orch.port}"

    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as setup_ws:
        await setup_ws.recv()  # HELLO
        await _auth(setup_ws, username="alice", password="alicealice")
        await setup_ws.send(
            json.dumps(
                {
                    "kind": "CREATE_TABLE",
                    "ruleset": "mcr-2006",
                    "seats": [
                        {"kind": "human"},
                        {"kind": "human"},
                        {"kind": "bot"},
                        {"kind": "bot"},
                    ],
                }
            )
        )
        created = json.loads(cast(str, await setup_ws.recv()))
        assert created["kind"] == "TABLE_CREATED", created
        table_id = int(created["table_id"])

    return orch, persistence, alice_id, bob_id, table_id


async def test_fixture_21a_disconnect_and_reconnect_within_hold_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bob drops after his first PROMPT, reconnects inside the hold window,
    the resumed session re-fires the pending prompt, and the hand finishes.

    The interim assertion that ``handle.sessions.seat(1).state`` is HELD
    between drop and reconnect is the pin that distinguishes this from a
    "never-dropped" path.
    """
    monkeypatch.setattr(mgr, "_now_ts", _fixed_ts({"i": 0}))

    orch, persistence, _alice_id, _bob_id, table_id = await _setup_2h2b(
        tmp_path=tmp_path,
        decide_timeout_seconds=30.0,  # plenty of time to reconnect
        hold_seconds=30.0,             # plenty of hold window
        strike_limit=3,
    )
    url = f"ws://127.0.0.1:{orch.port}"
    handle = orch.registry.get_table(str(table_id))

    bob_first_attached = asyncio.Event()
    bob_reconnected = asyncio.Event()

    async def run_alice() -> None:
        async with websockets.connect(
            url, subprotocols=["mahjong-v1"]
        ) as ws:
            await ws.recv()  # HELLO
            await _auth(ws, username="alice", password="alicealice")
            # Wait for bob to be LIVE before starting the hand.
            await bob_first_attached.wait()
            await ws.send(
                json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 0})
            )
            attached = json.loads(cast(str, await ws.recv()))
            assert attached["kind"] == "ATTACHED"
            await ws.send(
                json.dumps({"kind": "START_HAND", "table_id": table_id})
            )
            deadline = asyncio.get_event_loop().time() + 120.0
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                assert remaining > 0, "alice timed out before HAND_END"
                msg = json.loads(
                    cast(
                        str,
                        await asyncio.wait_for(ws.recv(), timeout=remaining),
                    )
                )
                if msg.get("kind") == "PROMPT":
                    await ws.send(
                        json.dumps(
                            {
                                "kind": "ACTION",
                                "prompt_id": msg["prompt_id"],
                                "action": msg["default_action"],
                            }
                        )
                    )
                elif msg.get("kind") == "HAND_END":
                    return

    async def run_bob() -> None:
        # First connection: attach, wait for the first PROMPT, then drop.
        ws1 = await websockets.connect(url, subprotocols=["mahjong-v1"])
        try:
            await ws1.recv()  # HELLO
            await _auth(ws1, username="bob", password="bobbobbobbob")
            await ws1.send(
                json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 1})
            )
            attached = json.loads(cast(str, await ws1.recv()))
            assert attached["kind"] == "ATTACHED"
            bob_first_attached.set()

            # Wait for at least one PROMPT before dropping so we know the
            # hand is actually running and there's a buffered pending
            # prompt for the resume path to replay.
            while True:
                msg = json.loads(
                    cast(
                        str,
                        await asyncio.wait_for(ws1.recv(), timeout=30.0),
                    )
                )
                if msg.get("kind") == "PROMPT":
                    break
                if msg.get("kind") == "HAND_END":
                    return  # nothing to test if hand finished too fast
        finally:
            await ws1.close()

        # Give the server a tick to mark seat 1 HELD; assert.
        for _ in range(50):
            if handle.sessions.seat(1).state.name == "HELD":
                break
            await asyncio.sleep(0.02)
        assert handle.sessions.seat(1).state.name == "HELD", (
            f"seat 1 should be HELD after drop; "
            f"got {handle.sessions.seat(1).state}"
        )

        # Reconnect with the same account → same-user resume path.
        async with websockets.connect(
            url, subprotocols=["mahjong-v1"]
        ) as ws2:
            await ws2.recv()  # HELLO
            await _auth(ws2, username="bob", password="bobbobbobbob")
            await ws2.send(
                json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 1})
            )
            re_attached = json.loads(cast(str, await ws2.recv()))
            assert re_attached["kind"] == "ATTACHED", re_attached
            bob_reconnected.set()

            # Drive prompts from the resumed session (replayed PROMPT
            # arrives in the ring-buffer flush after ATTACHED).
            deadline = asyncio.get_event_loop().time() + 120.0
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                assert remaining > 0, "bob timed out before HAND_END"
                msg = json.loads(
                    cast(
                        str,
                        await asyncio.wait_for(ws2.recv(), timeout=remaining),
                    )
                )
                if msg.get("kind") == "PROMPT":
                    await ws2.send(
                        json.dumps(
                            {
                                "kind": "ACTION",
                                "prompt_id": msg["prompt_id"],
                                "action": msg["default_action"],
                            }
                        )
                    )
                elif msg.get("kind") == "HAND_END":
                    return

    try:
        await asyncio.wait_for(
            asyncio.gather(run_alice(), run_bob()),
            timeout=180.0,
        )
        # Sanity: we actually did drop + reconnect (not a "never disconnected"
        # path that would invalidate this fixture).
        assert bob_reconnected.is_set()
        # The hand finalised; persistence rows present.
        await asyncio.sleep(0.2)
        hands = persistence.find_hands_by_account(_alice_id)
        assert len(hands) == 1, hands
        assert hands[0].terminal_kind in {"HU", "EXHAUSTIVE_DRAW"}
    finally:
        await orch.close()
        persistence.close()


async def test_fixture_21b_disconnect_no_reconnect_autopass_takeover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bob drops mid-hand and never reconnects; the seat-hold expires; the
    table manager's strike path takes over and the hand finishes.

    ``hold_seconds`` is set short and ``strike_limit=1`` so the takeover
    happens on the first failure (one ``SeatHoldExpired`` raised through
    ``HumanAdapter.decide`` is enough to swap in ``AutoPassAdapter`` for
    seat 1).  The hand finishing at all is the load-bearing assertion;
    that we *also* see seat 1's adapter become ``AutoPassAdapter`` is the
    direct mechanical confirmation.
    """
    monkeypatch.setattr(mgr, "_now_ts", _fixed_ts({"i": 0}))

    orch, persistence, alice_id, bob_id, table_id = await _setup_2h2b(
        tmp_path=tmp_path,
        decide_timeout_seconds=30.0,  # >> hold so SeatHoldExpired fires first
        hold_seconds=1.0,              # short hold → quick expiry after drop
        strike_limit=1,                 # one failure → autopass swap
    )
    url = f"ws://127.0.0.1:{orch.port}"

    bob_attached = asyncio.Event()

    async def run_alice() -> None:
        async with websockets.connect(
            url, subprotocols=["mahjong-v1"]
        ) as ws:
            await ws.recv()  # HELLO
            await _auth(ws, username="alice", password="alicealice")
            await bob_attached.wait()
            await ws.send(
                json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 0})
            )
            attached = json.loads(cast(str, await ws.recv()))
            assert attached["kind"] == "ATTACHED"
            await ws.send(
                json.dumps({"kind": "START_HAND", "table_id": table_id})
            )
            deadline = asyncio.get_event_loop().time() + 120.0
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                assert remaining > 0, "alice timed out before HAND_END"
                msg = json.loads(
                    cast(
                        str,
                        await asyncio.wait_for(ws.recv(), timeout=remaining),
                    )
                )
                if msg.get("kind") == "PROMPT":
                    await ws.send(
                        json.dumps(
                            {
                                "kind": "ACTION",
                                "prompt_id": msg["prompt_id"],
                                "action": msg["default_action"],
                            }
                        )
                    )
                elif msg.get("kind") == "HAND_END":
                    return

    async def run_bob_drop_only() -> None:
        ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
        try:
            await ws.recv()  # HELLO
            await _auth(ws, username="bob", password="bobbobbobbob")
            await ws.send(
                json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 1})
            )
            attached = json.loads(cast(str, await ws.recv()))
            assert attached["kind"] == "ATTACHED"
            bob_attached.set()
            # Wait briefly for the hand to actually start, then drop.
            # Dropping right after START_HAND fires is enough; the strike
            # path will still engage when bob's first decide is called.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.recv(), timeout=2.0)
        finally:
            await ws.close()

    try:
        await asyncio.wait_for(
            asyncio.gather(run_alice(), run_bob_drop_only()),
            timeout=180.0,
        )
        # Hand finalised; persistence row present.
        await asyncio.sleep(0.2)
        hands = persistence.find_hands_by_account(alice_id)
        assert len(hands) == 1, hands
        assert hands[0].terminal_kind in {"HU", "EXHAUSTIVE_DRAW"}, hands[0]

        # Bob never reconnected — the seat 1 participant row still gets
        # filled in from his last-known identity (recorded at attach).
        # The hand is credited to the human who attached, even if their
        # adapter was swapped to AutoPassAdapter mid-play.
        full = persistence.get_hand(hands[0].hand_id)
        assert full is not None
        seat1 = next(p for p in full.participants if p.seat == 1)
        assert seat1.account_id == bob_id, (
            f"seat 1 should still credit bob (account_id={bob_id}): {seat1}"
        )

        # Mechanical confirmation of the strike/swap path: the record
        # should contain at least one seat-1 event annotated with the
        # adapter-failure flags emitted by ``_decide_or_default``
        # (``crashed`` from the SeatHoldExpired path) or by
        # AutoPassAdapter (``auto_pass``).  Without the disconnect, neither
        # flag would appear.
        record_path = tmp_path / hands[0].record_path
        events: list[dict[str, Any]] = []
        with record_path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        seat1_failures = [
            e for e in events
            if e.get("seat") == 1 and (e.get("crashed") or e.get("auto_pass"))
        ]
        assert seat1_failures, (
            "expected at least one crashed/auto_pass event on seat 1 "
            "(no evidence the strike/swap path engaged)"
        )
    finally:
        await orch.close()
        persistence.close()
