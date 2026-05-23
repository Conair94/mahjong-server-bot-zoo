"""Browser client for the mahjong server.

The static assets live in `mahjong/web/static/` and are served by
`mahjong.wire.server.WebSocketServer` when constructed with
`static_dir=static_root()`. See `docs/specs/tui-client.md` for the
client architecture.
"""

from __future__ import annotations

from pathlib import Path


def static_root() -> Path:
    """Filesystem path to the bundled static assets directory.

    Resolves at call time rather than at import time so that test code can
    monkeypatch the package location if needed.
    """
    return Path(__file__).parent / "static"


__all__ = ["static_root"]
