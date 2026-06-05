"""End-to-end S2 fixture: one full hand round-tripped through the real stack.

Spec: docs/specs/tui-client.md fixture 18 / CHECKLIST Step 7.6.ii.

F1 is the byte-identical-record gate. A raw WebSocket client connects to a
running `WebOrchestrator`, ATTACHes seat 0, and echoes `default_action` on
every PROMPT. Seats 1-3 are `CannedAdapter`s with no scripted actions —
their `decide` also returns `default_action`. Net result: the action chosen
at every prompt is the manager's default, the engine path is the same as
the S0 walking-skeleton fixture, and the record is byte-identical to a
checked-in fixture that differs from S0's only in seat-0's identity
(`human` vs `canned`).

This pins the full stack: `WebSocketServer` → `TableSessions` → `SeatSession`
→ `HumanAdapter` → `manager.run_hand` → record writer. If any layer drops
or reorders an event, or projects the wrong shape, or double-emits HAND_END
(see 7.6.i), the byte assertion fails.

Why a raw WS client and not Playwright: the browser UI's wire handling is
already pinned by `tests/web/test_prompt.py`. F1's job is the orchestrator
+ record contract; routing it through the browser would add Chromium-launch
cost and a UI failure mode without strengthening the contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
import websockets

from mahjong.adapters.base import HumanIdentity
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.state import project_event
from mahjong.records.reader import read_record
from mahjong.table import manager as mgr
from mahjong.web.server import WebOrchestrator
from mahjong.wire.server import Connection

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
F1_FIXTURE = Path("tests/_fixtures/s2_e2e_record.jsonl")
F1_HAND_ID = "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f"
F1_SEED = 12345
F1_USER_ID = "u_test"
F1_DISPLAY = "Tester"
F1_SERVER_INFO: dict[str, Any] = {"version": "s0-fixture", "git_sha": "fixed", "host": "fixture"}


def _patch_fixed_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the manager's wall-clock so the record's `ts` fields are stable.

    Mirrors the S0 walking-skeleton pattern (tests/table/test_s0_walking_skeleton.py).
    """
    counter = {"i": 0}

    def fixed() -> str:
        counter["i"] += 1
        return f"2026-05-20T00:00:00.{counter['i']:03d}Z"

    monkeypatch.setattr(mgr, "_now_ts", fixed)


def _fixed_identity(_conn: Connection) -> HumanIdentity:
    """Inject the same HumanIdentity every connection so the HEADER's
    seats[0].identity is deterministic."""
    return {"kind": "human", "user_id": F1_USER_ID, "display": F1_DISPLAY}


async def _drive_default_echoing_client(
    url: str,
    *,
    hand_end: asyncio.Event,
    captured: list[dict[str, Any]] | None = None,
    drain_after_hand_end_s: float = 0.0,
) -> None:
    """Connect, ATTACH seat 0, echo `default_action` on each PROMPT, stop on
    HAND_END. If `captured` is provided, every received frame is appended to
    it. After HAND_END, optionally drain for `drain_after_hand_end_s` so
    callers can assert no further frames arrive (no-double-emit check)."""
    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
        hello = json.loads(cast(str, await ws.recv()))
        if captured is not None:
            captured.append(hello)
        assert hello["kind"] == "HELLO", hello

        await ws.send(json.dumps({"kind": "ATTACH", "table_id": 1, "seat": 0}))
        attached = json.loads(cast(str, await ws.recv()))
        if captured is not None:
            captured.append(attached)
        assert attached["kind"] == "ATTACHED", attached
        assert attached["seat"] == 0

        while True:
            msg = json.loads(cast(str, await ws.recv()))
            if captured is not None:
                captured.append(msg)
            kind = msg["kind"]
            if kind == "PROMPT":
                await ws.send(
                    json.dumps(
                        {
                            "kind": "ACTION",
                            "prompt_id": msg["prompt_id"],
                            "action": msg["default_action"],
                        }
                    )
                )
            elif kind == "HAND_END":
                hand_end.set()
                if drain_after_hand_end_s > 0:
                    await _drain_until_close_or_timeout(ws, captured, drain_after_hand_end_s)
                return


async def _drain_until_close_or_timeout(
    ws: Any, captured: list[dict[str, Any]] | None, seconds: float
) -> None:
    """Keep reading until either the timeout elapses or the socket closes.
    Used to assert the post-HAND_END quiescence invariant."""
    deadline = asyncio.get_event_loop().time() + seconds
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except TimeoutError:
            return
        except websockets.exceptions.ConnectionClosed:
            return
        if captured is not None:
            captured.append(json.loads(cast(str, raw)))


async def test_s2_e2e_record_is_byte_identical_to_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The S2 exit gate: full stack hosts one hand, record matches fixture
    byte-for-byte."""
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"

    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=F1_SEED,
        hand_id=F1_HAND_ID,
        record_path=out,
        server_info=F1_SERVER_INFO,
        identity_factory=_fixed_identity,
        # Pin deterministic canned-PASS bots: these fixtures record wire/session
        # behaviour, not bot play, and predate the v0 default bot.
        canned_seat_actions={1: [], 2: [], 3: []},
    )
    await orch.start()
    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        hand_end = asyncio.Event()
        client_task = asyncio.create_task(_drive_default_echoing_client(url, hand_end=hand_end))
        try:
            await orch.wait_hand_complete(timeout=15.0)
            await asyncio.wait_for(hand_end.wait(), timeout=2.0)
        finally:
            client_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client_task
    finally:
        await orch.close()

    assert out.read_bytes() == F1_FIXTURE.read_bytes()


async def test_s2_e2e_no_double_emit_hand_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wire-level invariant: exactly one HAND_END frame per hand. The
    byte-identical record check only constrains record events; this pins
    that `manager._safe_left("HAND_ENDED")` + `HumanAdapter.left` don't
    re-emit a second HAND_END after `SeatSession.observe` already routed
    the engine's HAND_END record event to the wire (Step 7.6.i)."""
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"

    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=F1_SEED,
        hand_id=F1_HAND_ID,
        record_path=out,
        server_info=F1_SERVER_INFO,
        identity_factory=_fixed_identity,
        # Pin deterministic canned-PASS bots: these fixtures record wire/session
        # behaviour, not bot play, and predate the v0 default bot.
        canned_seat_actions={1: [], 2: [], 3: []},
    )
    await orch.start()
    captured: list[dict[str, Any]] = []
    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        hand_end = asyncio.Event()
        client_task = asyncio.create_task(
            _drive_default_echoing_client(
                url,
                hand_end=hand_end,
                captured=captured,
                drain_after_hand_end_s=0.5,
            )
        )
        await orch.wait_hand_complete(timeout=15.0)
        await asyncio.wait_for(hand_end.wait(), timeout=2.0)
        await client_task
    finally:
        await orch.close()

    hand_end_frames = [m for m in captured if m["kind"] == "HAND_END"]
    assert len(hand_end_frames) == 1, (
        f"expected exactly one HAND_END frame, got {len(hand_end_frames)}: {hand_end_frames}"
    )
    # The single HAND_END must carry a non-empty terminal payload (not the
    # empty dict that the pre-7.6.i double-emit path produced).
    assert hand_end_frames[0]["terminal"], hand_end_frames[0]


# --- F4 — spectator subscription ---


async def _drive_spectator_client(
    url: str,
    *,
    ready: asyncio.Event,
    captured: list[dict[str, Any]],
    hand_end: asyncio.Event,
    drain_after_hand_end_s: float = 0.3,
) -> None:
    """Connect, SPECTATE, capture every frame received. Signal `ready` once
    SPECTATING arrives so the player client can proceed. Stop on HAND_END,
    then drain briefly so a stray follow-up frame would surface."""
    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
        hello = json.loads(cast(str, await ws.recv()))
        captured.append(hello)
        assert hello["kind"] == "HELLO", hello

        await ws.send(json.dumps({"kind": "SPECTATE", "table_id": 1}))
        spectating = json.loads(cast(str, await ws.recv()))
        captured.append(spectating)
        assert spectating["kind"] == "SPECTATING", spectating
        ready.set()

        while True:
            try:
                raw = await ws.recv()
            except websockets.exceptions.ConnectionClosed:
                return
            msg = json.loads(cast(str, raw))
            captured.append(msg)
            if msg["kind"] == "HAND_END":
                hand_end.set()
                await _drain_until_close_or_timeout(ws, captured, drain_after_hand_end_s)
                return


async def _drive_player_after_event(
    url: str,
    *,
    wait_for: asyncio.Event,
    hand_end: asyncio.Event,
) -> None:
    """Like `_drive_default_echoing_client` but waits for `wait_for` to fire
    before sending ATTACH — ensures the spectator is already subscribed
    when the hand kicks off."""
    await wait_for.wait()
    await _drive_default_echoing_client(url, hand_end=hand_end)


async def test_s2_e2e_spectator_sees_public_events_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4 — the S2 closing fixture.

    A spectator subscribes before the hand starts and asserts:
      1. Receives `SPECTATING` first (with a public snapshot — no concealed
         hands).
      2. Receives one `EVENT` per record event the engine emits, each
         payload byte-equal to `project_event(record_event, seat=None)`
         (the public projection — no own-seat concealed leaks).
      3. Receives the engine's HAND_END as a top-level `HAND_END` wire
         frame (not EVENT-wrapped).
      4. Never receives a `PROMPT`, even when seat 0 is on-turn.
    """
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"

    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=F1_SEED,
        hand_id=F1_HAND_ID,
        record_path=out,
        server_info=F1_SERVER_INFO,
        identity_factory=_fixed_identity,
        # Pin deterministic canned-PASS bots: these fixtures record wire/session
        # behaviour, not bot play, and predate the v0 default bot.
        canned_seat_actions={1: [], 2: [], 3: []},
    )
    await orch.start()

    spectator_frames: list[dict[str, Any]] = []
    spectator_ready = asyncio.Event()
    spectator_hand_end = asyncio.Event()
    player_hand_end = asyncio.Event()
    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        spec_task = asyncio.create_task(
            _drive_spectator_client(
                url,
                ready=spectator_ready,
                captured=spectator_frames,
                hand_end=spectator_hand_end,
            )
        )
        player_task = asyncio.create_task(
            _drive_player_after_event(url, wait_for=spectator_ready, hand_end=player_hand_end)
        )
        try:
            await orch.wait_hand_complete(timeout=15.0)
            await asyncio.wait_for(player_hand_end.wait(), timeout=2.0)
            await asyncio.wait_for(spectator_hand_end.wait(), timeout=2.0)
            await asyncio.wait_for(spec_task, timeout=2.0)
            await asyncio.wait_for(player_task, timeout=2.0)
        finally:
            for task in (spec_task, player_task):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
    finally:
        await orch.close()

    # (4) no PROMPT to a spectator, ever.
    prompts = [m for m in spectator_frames if m["kind"] == "PROMPT"]
    assert not prompts, f"spectator received PROMPT frames: {prompts}"

    # (3) exactly one HAND_END, non-empty terminal.
    hand_ends = [m for m in spectator_frames if m["kind"] == "HAND_END"]
    assert len(hand_ends) == 1, f"expected 1 HAND_END frame, got {len(hand_ends)}: {hand_ends}"
    assert hand_ends[0]["terminal"], hand_ends[0]

    # (1) SPECTATING is the FIRST non-HELLO frame.
    kinds = [m["kind"] for m in spectator_frames]
    assert kinds[0] == "HELLO", kinds[:5]
    assert kinds[1] == "SPECTATING", kinds[:5]

    # (2) each EVENT payload matches `project_event(record_event, seat=None)`.
    record = read_record(out)
    record_events_excl_meta = [
        e for e in record if e["event"] not in {"HEADER", "FOOTER", "HAND_END"}
    ]
    spectator_events = [m for m in spectator_frames if m["kind"] == "EVENT"]
    assert len(spectator_events) == len(record_events_excl_meta), (
        f"event-count mismatch: spectator={len(spectator_events)} record="
        f"{len(record_events_excl_meta)}"
    )
    for wire_frame, record_event in zip(spectator_events, record_events_excl_meta, strict=True):
        # The record writer stamps `seq` onto each event when persisting;
        # the in-memory event passed to `event_callback` (and thus to the
        # spectator fanout) doesn't carry it. Strip before projecting.
        engine_event = {k: v for k, v in record_event.items() if k != "seq"}
        expected = project_event(engine_event, seat=None)
        assert wire_frame["event"] == expected, (
            f"spectator EVENT payload diverged from public projection:\n"
            f"  expected: {expected}\n  got: {wire_frame['event']}"
        )

    # And the HAND_END `terminal` payload matches the record's HAND_END
    # event stripped of wrapper fields.
    hand_end_record = next(e for e in record if e["event"] == "HAND_END")
    stripped = {
        k: v
        for k, v in hand_end_record.items()
        if k not in {"event", "seq", "turn_index", "phase", "ts"}
    }
    assert hand_ends[0]["terminal"] == stripped, (
        f"HAND_END terminal mismatch:\n  expected: {stripped}\n  got: {hand_ends[0]['terminal']}"
    )


# --- F2 — drop and reconnect within hold window ---


async def _connect_attach_recv_until_prompt(
    url: str,
) -> tuple[Any, dict[str, Any]]:
    """Open a WS, exchange HELLO/ATTACH/ATTACHED, then read frames until
    the first PROMPT arrives. Returns `(ws, prompt_msg)` with the socket
    still open — the caller decides whether to ACTION-reply or drop."""
    ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
    json.loads(cast(str, await ws.recv()))  # HELLO
    await ws.send(json.dumps({"kind": "ATTACH", "table_id": 1, "seat": 0}))
    json.loads(cast(str, await ws.recv()))  # ATTACHED
    while True:
        msg = json.loads(cast(str, await ws.recv()))
        if msg["kind"] == "PROMPT":
            return ws, msg


async def _reconnect_and_finish(url: str, *, hand_end: asyncio.Event) -> None:
    """Reconnect (same identity injected at server side), wait through the
    resume replay until the re-emitted PROMPT, then echo defaults to
    completion. Mirrors `_drive_default_echoing_client` but skips ATTACHED
    state assertions because the test body already pinned the resume
    semantics."""
    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
        json.loads(cast(str, await ws.recv()))  # HELLO
        await ws.send(json.dumps({"kind": "ATTACH", "table_id": 1, "seat": 0}))
        # ATTACHED (resume), then any buffer-replay EVENTs, then a re-prompt.
        while True:
            msg = json.loads(cast(str, await ws.recv()))
            kind = msg["kind"]
            if kind == "PROMPT":
                await ws.send(
                    json.dumps(
                        {
                            "kind": "ACTION",
                            "prompt_id": msg["prompt_id"],
                            "action": msg["default_action"],
                        }
                    )
                )
            elif kind == "HAND_END":
                hand_end.set()
                return


async def test_s2_e2e_drop_and_reconnect_within_hold_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F2 — the mid-hand drop with prompt-pending should be invisible to
    the record. Player connects, receives first PROMPT, drops WITHOUT
    answering, reconnects with the same user_id while the seat is HELD,
    receives the re-emitted PROMPT (same prompt_id), and finishes the
    hand. Resulting record must be byte-identical to F1's fixture: no
    auto_pass markers, identical engine path.
    """
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"

    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=F1_SEED,
        hand_id=F1_HAND_ID,
        record_path=out,
        server_info=F1_SERVER_INFO,
        identity_factory=_fixed_identity,  # both connections get u_test
        hold_seconds=5.0,  # generous; we reconnect in milliseconds
        # Deterministic canned-PASS bots: this asserts reconnect doesn't leak
        # into the record (bot play is incidental, fixture predates v0).
        canned_seat_actions={1: [], 2: [], 3: []},
    )
    await orch.start()

    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        ws_a, prompt_a = await _connect_attach_recv_until_prompt(url)

        # Drop abruptly — no DETACH, just close the underlying socket. The
        # orchestrator's inbound loop will exit and call on_socket_dropped,
        # which transitions the SeatSession to HELD with prompt still pending.
        original_prompt_id = prompt_a["prompt_id"]
        await ws_a.close()

        # Give the server a beat to notice the drop and HELD-transition.
        await asyncio.sleep(0.1)

        # Reconnect; resume path replays buffer (empty for this scenario —
        # nothing happened post-drop) and re-emits the same prompt.
        hand_end = asyncio.Event()
        client_task = asyncio.create_task(_reconnect_and_finish(url, hand_end=hand_end))
        try:
            await orch.wait_hand_complete(timeout=15.0)
            await asyncio.wait_for(hand_end.wait(), timeout=2.0)
            await asyncio.wait_for(client_task, timeout=2.0)
        finally:
            client_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client_task

        # The re-emitted prompt MUST carry the same stable id (seat / turn_index /
        # phase, per HumanAdapter._translate_prompt) so the client can echo
        # the same ACTION. Test infra didn't capture it on the second
        # connection, but the byte-identical record assertion below implicitly
        # verifies the same engine state was driven; we also pin the prompt_id
        # shape here for documentation.
        assert original_prompt_id.startswith("p_0_"), original_prompt_id
    finally:
        await orch.close()

    assert out.read_bytes() == F1_FIXTURE.read_bytes(), (
        "F2 record diverged from F1 — drop/reconnect leaked into the record"
    )


# --- F3 — drop without reconnect; hand completes via prompt-deadline defaults ---


async def test_s2_e2e_drop_without_reconnect_strikes_then_autopasses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F3 — drop past the hold window; hand completes via the strike →
    autopass escalation path per `seat-port.md` § Failure modes.

    The client connects + ATTACHes, then drops without replying to any
    PROMPT. With `decide_timeout_seconds` short, each subsequent seat-0
    prompt times out at the manager level; per spec lines 144-149 the
    table manager submits `prompt.default_action`, writes the resulting
    event with `timeout: true`, and counts a strike. After
    `strike_limit=3` timeouts, the seat is replaced by `AutoPassAdapter`
    (spec line 181) and from that point seat-0 events carry
    `auto_pass: true`. The hand still completes normally — the table is
    never wedged.

    What this pins:
      - Hand reaches HAND_END without deadlock.
      - Seat-0 events after the drop carry the documented failure markers
        (`timeout: true` for the first N where N ≤ strike_limit, then
        `auto_pass: true` for the remainder).
      - Other seats' events are unaffected (no spurious markers on
        seats 1-3).

    Note: this is the actual spec behavior. An earlier framing assumed a
    'defaults-without-marker' shape (option (a) in 7.6.iii prep); rereading
    spec § Failure modes confirmed every default-via-deadline gets a
    `timeout: true` marker by design, so 'no markers' was never reachable
    without spec changes. The strike escalation path already produces the
    right shape; no new plumbing was needed.
    """
    _patch_fixed_ts(monkeypatch)
    out = tmp_path / "regenerated.jsonl"

    orch = WebOrchestrator(
        host="127.0.0.1",
        port=0,
        ruleset=cast(Any, MCR_REF),
        seed=F1_SEED,
        hand_id=F1_HAND_ID,
        record_path=out,
        server_info=F1_SERVER_INFO,
        identity_factory=_fixed_identity,
        decide_timeout_seconds=0.1,  # ~30 seat-0 prompts at 0.1s each is ~3s
        hold_seconds=30.0,  # well above hand duration; hold timer is moot
        strike_limit=3,  # default; pinned here so the assertions below are clear
    )
    await orch.start()

    try:
        url = f"ws://127.0.0.1:{orch.port}/socket"
        ws = await websockets.connect(url, subprotocols=["mahjong-v1"])
        try:
            json.loads(cast(str, await ws.recv()))  # HELLO
            await ws.send(json.dumps({"kind": "ATTACH", "table_id": 1, "seat": 0}))
            json.loads(cast(str, await ws.recv()))  # ATTACHED
        finally:
            await ws.close()

        # Give the orchestrator a beat to notice the drop.
        await asyncio.sleep(0.1)
        await orch.wait_hand_complete(timeout=10.0)
    finally:
        await orch.close()

    record = read_record(out)
    events = [e for e in record if e["event"] not in {"HEADER", "FOOTER"}]

    # Hand reached HAND_END.
    assert events[-1]["event"] == "HAND_END", events[-1]

    # Seat-0 action events: walk through DISCARD/CLAIM_DECISION events for
    # seat 0 and check the documented escalation sequence. First ≤
    # strike_limit get `timeout: true`; once swapped, the rest get
    # `auto_pass: true`. (The exact split depends on the ordering of seat-0
    # prompts in the engine path; we assert the pattern, not the count.)
    seat0_actions = [
        e for e in events if e.get("seat") == 0 and e.get("event") in {"DISCARD", "CLAIM_DECISION"}
    ]
    assert seat0_actions, "expected at least one seat-0 action event"

    timeout_count = sum(1 for e in seat0_actions if e.get("timeout"))
    autopass_count = sum(1 for e in seat0_actions if e.get("auto_pass"))
    assert timeout_count > 0, (
        f"expected at least one timeout marker on seat-0 events; got: {seat0_actions[:3]}"
    )
    assert autopass_count > 0, (
        f"expected at least one auto_pass marker (strike→autopass swap); "
        f"got: timeouts={timeout_count}, autopass={autopass_count}"
    )
    # After the autopass swap, no events should still carry `timeout: True`
    # (autopass returns synchronously — no deadline to miss).
    first_autopass_idx = next(i for i, e in enumerate(seat0_actions) if e.get("auto_pass"))
    for e in seat0_actions[first_autopass_idx:]:
        assert not e.get("timeout"), f"timeout marker after autopass swap: {e}"

    # Other seats are unaffected — no spurious markers on seats 1-3.
    for e in events:
        if e.get("seat") in (1, 2, 3):
            assert not e.get("timeout"), f"unexpected timeout on non-seat-0 event: {e}"
            assert not e.get("auto_pass"), f"unexpected auto_pass on non-seat-0 event: {e}"
