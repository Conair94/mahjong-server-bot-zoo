"""Demo runner for the web client.

Boots a `WebSocketServer` that serves the bundled static assets *and* runs
a scripted WebSocket handler so a browser opening the URL gets a real
ATTACHED snapshot to render. The snapshot is produced by `initial_state`
projected to seat 0 — no real table manager, no engine event loop. Step
7.6 wires the full stack end-to-end.

The deal is seeded (DEMO_SEED) so the snapshot is byte-stable across
restarts — useful while iterating the renderer.

Run with: `python -m mahjong.web.demo` (defaults to 127.0.0.1:8400)
or       `python -m mahjong.web.demo --host 0.0.0.0 --port 8400`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from typing import Any, cast

from mahjong.engine.legality import legal_actions
from mahjong.engine.state import initial_state, project, project_event
from mahjong.engine.transition import apply_action
from mahjong.engine.types import GameState, RuleSetRef
from mahjong.records.diff import diff_to_events
from mahjong.web import static_root
from mahjong.wire.errors import WireError
from mahjong.wire.server import Connection, WebSocketServer

_logger = logging.getLogger(__name__)

DEMO_SEED = 42
DEMO_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})
DEMO_TS = "2026-05-23T00:00:00Z"
DEMO_TURNS = 12  # how many engine ticks to drive before idling
DEMO_INTER_EVENT_DELAY_S = 0.5


def _build_demo_snapshot(own_seat: int = 0) -> dict[str, Any]:
    """Deal a fresh hand and project to `own_seat`. Pure, deterministic."""
    state = initial_state(DEMO_RULESET, seed=DEMO_SEED)
    return cast(dict[str, Any], project(state, own_seat))


def _drive_one_tick(state: GameState) -> tuple[GameState, list[dict[str, Any]]]:
    """Apply the first legal action for the engine's current_actor, falling
    back to other seats only if current_actor has none.

    In CLAIM_WINDOW, current_actor is the seat the engine is waiting on; we
    must respect that or we'll loop forever applying seat-0's PASS without
    ever giving seat 2 / 3 a turn.

    Returns `(state_after, events_emitted)`. Empty events on terminal.
    """
    order = [state["current_actor"]] + [
        s for s in range(4) if s != state["current_actor"]
    ]
    for seat in order:
        actions = legal_actions(state, seat)
        if not actions:
            continue
        action = actions[0]
        state_after = apply_action(state, seat, action)
        events = diff_to_events(state, seat, action, state_after, ts=DEMO_TS)
        return state_after, events
    return state, []


async def _scripted_handler(conn: Connection) -> None:
    """Send HELLO, ATTACHED, then drive `DEMO_TURNS` engine ticks and stream
    the resulting events with a small delay between each so the renderer
    animates through them. Inbound frames after that are echoed as
    `not_implemented` errors; PROMPT/ACTION round-trip lands in 7.5c.iii.
    """
    await conn.send(
        {
            "kind": "HELLO",
            "seq": 1,
            "protocol_version": 1,
            "server_id": "mahjong-server-demo",
        }
    )

    own_seat = 0
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
        for _ in range(DEMO_TURNS):
            state, events = _drive_one_tick(state)
            if not events:
                break
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
            if state["phase"] == "TERMINAL":
                break

        async for msg in conn:
            _logger.info("inbound: %s", msg)
            await conn.send(
                {
                    "kind": "ERROR",
                    "seq": seq,
                    "code": "not_implemented",
                    "message": f"demo server received {msg.get('kind', '?')}",
                }
            )
            seq += 1
    except WireError:
        return


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
