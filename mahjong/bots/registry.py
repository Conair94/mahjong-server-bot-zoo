"""In-memory bot registry.

Spec: docs/specs/bot-runner-protocol.md (validation runs at registration);
implementation-order.md Step 5.1.

Single-process registry. Multi-process / persistent registration is deferred
until S3 (accounts + persistence); for S1 the registry is built up at startup
from on-disk manifests.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mahjong.bots.errors import BotError
from mahjong.bots.manifest import DEFAULT_SERVER_CAPS, BotManifest, ServerCaps, parse_manifest


class BotAlreadyRegistered(BotError):
    """A bot with this `bot_id` is already registered. Pass `replace=True`
    to overwrite."""

    def __init__(self, bot_id: str) -> None:
        super().__init__(f"BotAlreadyRegistered({bot_id!r})")
        self.bot_id = bot_id


class BotNotFound(BotError):
    """No bot registered under this `bot_id`."""

    def __init__(self, bot_id: str) -> None:
        super().__init__(f"BotNotFound({bot_id!r})")
        self.bot_id = bot_id


class BotRegistry:
    """Map of bot_id -> BotManifest. In-memory; not thread-safe (the table
    manager loop is single-threaded asyncio)."""

    def __init__(self, *, server_caps: ServerCaps = DEFAULT_SERVER_CAPS) -> None:
        self._server_caps = server_caps
        self._bots: dict[str, BotManifest] = {}

    def register(self, manifest: BotManifest, *, replace: bool = False) -> None:
        if not replace and manifest.bot_id in self._bots:
            raise BotAlreadyRegistered(manifest.bot_id)
        self._bots[manifest.bot_id] = manifest

    def register_dict(self, raw: dict[str, Any], *, replace: bool = False) -> BotManifest:
        """Validate `raw` against the manifest schema and register. Invalid
        manifests never enter the registry (fixture 10)."""
        manifest = parse_manifest(raw, server_caps=self._server_caps)
        self.register(manifest, replace=replace)
        return manifest

    def unregister(self, bot_id: str) -> None:
        if bot_id not in self._bots:
            raise BotNotFound(bot_id)
        del self._bots[bot_id]

    def lookup(self, bot_id: str) -> BotManifest:
        if bot_id not in self._bots:
            raise BotNotFound(bot_id)
        return self._bots[bot_id]

    def list_ids(self) -> Iterable[str]:
        return list(self._bots)

    def __contains__(self, bot_id: object) -> bool:
        return isinstance(bot_id, str) and bot_id in self._bots

    def __len__(self) -> int:
        return len(self._bots)


__all__ = [
    "BotAlreadyRegistered",
    "BotNotFound",
    "BotRegistry",
]
