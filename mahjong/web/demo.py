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

from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef
from mahjong.web import static_root
from mahjong.wire.errors import WireError
from mahjong.wire.server import Connection, WebSocketServer

_logger = logging.getLogger(__name__)

DEMO_SEED = 42
DEMO_RULESET: RuleSetRef = cast(RuleSetRef, {"id": "mcr-2006", "version": 1})


def _build_demo_snapshot(own_seat: int = 0) -> dict[str, Any]:
    """Deal a fresh hand and project to `own_seat`. Pure, deterministic."""
    state = initial_state(DEMO_RULESET, seed=DEMO_SEED)
    return cast(dict[str, Any], project(state, own_seat))


async def _scripted_handler(conn: Connection) -> None:
    """Send HELLO, then a real ATTACHED with a fixture snapshot.

    After ATTACHED the handler echoes inbound frames as `not_implemented`
    errors. The PROMPT / ACTION round-trip lands in step 7.5c.iii.
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
    snapshot = _build_demo_snapshot(own_seat)
    await conn.send(
        {
            "kind": "ATTACHED",
            "seq": 2,
            "table_id": 1,
            "seat": own_seat,
            "hand_index": 0,
            "snapshot": snapshot,
            "resume_buffer_size": 0,
        }
    )

    try:
        async for msg in conn:
            _logger.info("inbound: %s", msg)
            await conn.send(
                {
                    "kind": "ERROR",
                    "seq": 3,
                    "code": "not_implemented",
                    "message": f"demo server received {msg.get('kind', '?')}",
                }
            )
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
