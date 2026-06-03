"""Control plane for the mahjong server (Spec 25 — admin console).

A separate process from ``serve``: it supervises the server as a child process,
samples its resource usage, streams its logs, and serves the admin web UI.  See
``docs/specs/admin-console.md``.
"""

from __future__ import annotations

from pathlib import Path


def static_root() -> Path:
    """Filesystem path to the bundled admin-UI static assets."""
    return Path(__file__).parent / "static"


__all__ = ["static_root"]
