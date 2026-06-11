"""V1Adapter — the seat-port shell around the v1 rule-based policy.

Spec: docs/specs/v1-rule-bot.md § Adapter, registry, website.

Byte-for-byte the V0Adapter pattern: `decide` delegates to the pure
`mahjong.bots.v1` policy, lifecycle methods are no-ops apart from caching the
seat index. All logic and tests live in the pure policy.
"""

from __future__ import annotations

from typing import Any

from mahjong.adapters.base import BotIdentity, LeaveReason, Prompt, SeatContext
from mahjong.bots.v1 import decide_action
from mahjong.engine.types import Action, SeatView


class V1Adapter:
    """In-process rule-based bot: v0's offense + hard accounting + defense."""

    identity: BotIdentity
    kind = "bot"

    def __init__(self) -> None:
        self.identity = {
            "kind": "bot",
            "bot_id": "v1",
            "version": "1",
            "runtime": "in_process",
        }
        self._seat: int = 0

    async def seated(self, ctx: SeatContext) -> None:
        self._seat = ctx["seat"]

    async def observe(self, event: dict[str, Any], view: SeatView) -> None:
        return None

    async def decide(self, prompt: Prompt) -> Action:
        return decide_action(
            prompt["view"],
            prompt["legal_actions"],
            self._seat,
            prompt["kind"],
        )

    async def left(self, reason: LeaveReason) -> None:
        return None


__all__ = ["V1Adapter"]
