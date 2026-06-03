"""``python -m mahjong control`` — run the admin control console.

Spec: docs/specs/admin-console.md § Bootstrapping.

Launches the control plane (which supervises the ``serve`` child).  The console
is the *only* thing you start from a terminal; everything else — including
starting/stopping the server — happens from its web UI.

Flags:
  --open               open the dashboard in a browser once it's up
  --autostart-server   start the game server immediately (one launch → server up)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from mahjong.control.app import ControlApp, ControlConfig
from mahjong.server.config import load_config_from_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mahjong control",
        description="Run the admin control console (supervises `serve`).",
    )
    parser.add_argument(
        "--open", action="store_true", help="open the dashboard in a browser"
    )
    parser.add_argument(
        "--autostart-server",
        action="store_true",
        help="start the game server immediately on launch",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # Reuse the server config loader so the console and the child agree on the
    # listen address / data dir.
    server_cfg, _unknown = load_config_from_env()
    ctl_cfg = ControlConfig.from_env(os.environ)

    app = ControlApp(
        config=ctl_cfg,
        server_env=dict(os.environ),
        server_listen_addr=server_cfg.listen_addr,
    )
    return asyncio.run(
        app.run(autostart_server=args.autostart_server, open_browser=args.open)
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
