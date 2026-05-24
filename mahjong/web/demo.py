"""Demo runner for the web client.

Boots a `WebSocketServer` that serves the bundled static assets *and* runs
a scripted WebSocket handler driving a real engine instance. The other
three seats auto-play (first-legal-action policy); when the engine is
waiting on seat 0 (the browser), the handler sends a real PROMPT frame
and blocks until a matching ACTION arrives. Illegal actions get rejected
with an `ERROR { code: "illegal_action" }` and the same PROMPT stays open.

This is still not a real `TableManager` — it's a hand-rolled mini-orchestrator
intended to exercise the 7.5c.iii PROMPT/ACTION round-trip end-to-end. Step
7.6 wires the real stack.

Run with: `python -m mahjong.web.demo` (defaults to 127.0.0.1:8400)
or       `python -m mahjong.web.demo --host 0.0.0.0 --port 8400`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import time
from typing import Any, cast

from mahjong.engine.errors import IllegalAction
from mahjong.engine.legality import legal_actions
from mahjong.engine.state import initial_state, project, project_event
from mahjong.engine.transition import apply_action
from mahjong.engine.types import Action, GameState, RuleSetRef
from mahjong.records.diff import diff_to_events
from mahjong.web import static_root
from mahjong.wire.errors import WireError
from mahjong.wire.server import Connection, WebSocketServer

_logger = logging.getLogger(__name__)

DEMO_SEED = 42
DEMO_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})
DEMO_TS = "2026-05-23T00:00:00Z"
DEMO_OWN_SEAT = 0
DEMO_INTER_EVENT_DELAY_S = 0.4
DEMO_PROMPT_DEADLINE_S = 30.0


def _build_demo_snapshot(own_seat: int = 0) -> dict[str, Any]:
    """Deal a fresh hand and project to `own_seat`. Pure, deterministic."""
    state = initial_state(DEMO_RULESET, seed=DEMO_SEED)
    return cast(dict[str, Any], project(state, own_seat))


def _autoplay_one_tick(state: GameState) -> tuple[GameState, int, list[dict[str, Any]]]:
    """Apply the first legal action for the engine's current_actor, falling
    back to other seats only if current_actor has none.

    In CLAIM_WINDOW, current_actor is the seat the engine is waiting on; we
    must respect that or we'll loop forever applying seat-0's PASS without
    ever giving seat 2 / 3 a turn.

    Returns `(state_after, acting_seat, events_emitted)`. Empty events on terminal.
    """
    order = [state["current_actor"]] + [s for s in range(4) if s != state["current_actor"]]
    for seat in order:
        actions = legal_actions(state, seat)
        if not actions:
            continue
        action = actions[0]
        state_after = apply_action(state, seat, action)
        events = diff_to_events(state, seat, action, state_after, ts=DEMO_TS)
        return state_after, seat, events
    return state, -1, []


def _prompt_id_for(seat: int, state: GameState) -> str:
    """Stable id derived the same way HumanAdapter does — `seat`,
    `turn_index`, and `phase`. Lets the client echo it back unchanged."""
    return f"p_{seat}_{state['turn_index']}_{state['phase']}"


def _build_prompt(seat: int, state: GameState, legal: list[Action], seq: int) -> dict[str, Any]:
    return {
        "kind": "PROMPT",
        "seq": seq,
        "table_id": 1,
        "hand_index": state["hand_index"],
        "seat": seat,
        "phase": state["phase"],
        "legal_actions": [dict(a) for a in legal],
        "default_action": dict(legal[0]),
        "deadline_ms": int(time.time() * 1000 + DEMO_PROMPT_DEADLINE_S * 1000),
        "prompt_id": _prompt_id_for(seat, state),
    }


async def _broadcast_events(
    conn: Connection,
    events: list[dict[str, Any]],
    own_seat: int,
    seq: int,
) -> int:
    """Send each event (projected to `own_seat`) with a small inter-event
    delay so the client animates through the sequence. Returns the next seq."""
    for event in events:
        await asyncio.sleep(DEMO_INTER_EVENT_DELAY_S)
        projected = project_event(event, own_seat)
        await conn.send(
            {
                "kind": "EVENT",
                "seq": seq,
                "table_id": 1,
                "hand_index": 0,
                "event": projected,
            }
        )
        seq += 1
    return seq


async def _await_action_for_prompt(
    conn: Connection, expected_prompt_id: str
) -> dict[str, Any] | None:
    """Block until an `ACTION` frame with the expected `prompt_id` arrives.

    Non-ACTION inbound and ACTIONs for stale prompts are ignored (the spec
    leaves the precise behavior here to the server; for the demo we just
    wait for the right one). Returns `None` if the connection closes.
    """
    try:
        while True:
            msg = await conn.recv()
            if msg.get("kind") != "ACTION":
                continue
            if msg.get("prompt_id") != expected_prompt_id:
                continue
            return msg
    except (WireError, asyncio.CancelledError):
        return None


async def _scripted_handler(conn: Connection) -> None:
    """Run a hand against a real engine: auto-play three seats, prompt seat
    0 (the browser) for its turn, accept ACTION back via `apply_action`.
    Reject illegal actions with `ERROR { code: "illegal_action" }` and
    re-prompt with the same `prompt_id`."""
    await conn.send(
        {
            "kind": "HELLO",
            "seq": 1,
            "protocol_version": 1,
            "server_id": "mahjong-server-demo",
        }
    )

    own_seat = DEMO_OWN_SEAT
    state = initial_state(DEMO_RULESET, seed=DEMO_SEED)
    await conn.send(
        {
            "kind": "ATTACHED",
            "seq": 2,
            "table_id": 1,
            "seat": own_seat,
            "hand_index": 0,
            "snapshot": cast(dict[str, Any], project(state, own_seat)),
            "resume_buffer_size": 0,
        }
    )

    seq = 3
    try:
        while state["phase"] != "TERMINAL":
            own_legal = legal_actions(state, own_seat)
            if state["current_actor"] == own_seat and own_legal:
                state, seq = await _prompt_seat_zero(conn, state, own_legal, own_seat, seq)
                continue

            state, acting_seat, events = _autoplay_one_tick(state)
            if not events:
                break
            seq = await _broadcast_events(conn, events, own_seat, seq)
            del acting_seat  # only used for logging in future iterations
    except WireError:
        return


async def _prompt_seat_zero(
    conn: Connection,
    state: GameState,
    legal: list[Action],
    own_seat: int,
    seq: int,
) -> tuple[GameState, int]:
    """Send PROMPT, wait for matching ACTION, apply (or ERROR + re-loop).

    Loops on `IllegalAction` so a rejected attempt stays with the same
    `prompt_id` until the player picks a legal action (matches the spec
    fixture 9 expectation that the prompt remains open after an error).
    """
    prompt = _build_prompt(own_seat, state, legal, seq)
    seq += 1
    await conn.send(prompt)

    while True:
        msg = await _await_action_for_prompt(conn, prompt["prompt_id"])
        if msg is None:
            return state, seq  # connection closed
        action = cast(Action, msg["action"])
        try:
            state_after = apply_action(state, own_seat, action)
        except IllegalAction as exc:
            await conn.send(
                {
                    "kind": "ERROR",
                    "seq": seq,
                    "code": "illegal_action",
                    "message": str(exc),
                }
            )
            seq += 1
            continue
        events = diff_to_events(state, own_seat, action, state_after, ts=DEMO_TS)
        seq = await _broadcast_events(conn, events, own_seat, seq)
        return state_after, seq


def _health() -> tuple[int, bytes]:
    return 200, b'{"status": "ok", "demo": true}\n'


async def _run(host: str, port: int) -> None:
    server = WebSocketServer(
        host=host,
        port=port,
        handler=_scripted_handler,
        health_handler=_health,
        static_dir=static_root(),
    )
    await server.start()
    print(f"web client demo: http://{host}:{server.port}/")
    print(f"health:          http://{host}:{server.port}/health")
    print("Press Ctrl-C to stop.")
    try:
        # Sleep indefinitely; the server runs in the background asyncio loop.
        await asyncio.Event().wait()
    finally:
        await server.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mahjong web-client walking-skeleton demo.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8400)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s"
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args.host, args.port))


if __name__ == "__main__":
    main()
