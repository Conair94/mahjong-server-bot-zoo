"""Walking-skeleton demo runner for the web client (Step 7.5a).

Boots a `WebSocketServer` that serves the bundled static assets *and* runs
a minimal scripted WebSocket handler so a browser opening the URL can see
the wire handshake render in the page's wire-log pane.

This is intentionally tiny — not a real table manager, not authentication,
not a session multiplexer. Step 7.6 wires the full stack end-to-end. For
now, the goal is to prove the browser ↔ server loop closes.

Run with: `python -m mahjong.web.demo` (defaults to 127.0.0.1:8400)
or       `python -m mahjong.web.demo --host 0.0.0.0 --port 8400`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging

from mahjong.web import static_root
from mahjong.wire.errors import WireError
from mahjong.wire.server import Connection, WebSocketServer

_logger = logging.getLogger(__name__)


async def _scripted_handler(conn: Connection) -> None:
    """Send a HELLO frame, then echo whatever the client sends back.

    Just enough wire traffic for the walking-skeleton UI to render something.
    """
    await conn.send(
        {
            "kind": "HELLO",
            "seq": 1,
            "protocol_version": 1,
            "server_id": "mahjong-server-demo",
        }
    )
    try:
        async for msg in conn:
            _logger.info("inbound: %s", msg)
            await conn.send(
                {
                    "kind": "ERROR",
                    "seq": 2,
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
