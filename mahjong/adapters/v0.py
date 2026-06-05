"""V0Adapter — the seat-port shell around the v0 offense policy.

Spec: docs/specs/v0-offense-bot.md § Architecture & placement.

A thin async wrapper: `decide` delegates to the pure `mahjong.bots.v0`
policy, lifecycle methods are no-ops apart from caching the seat index from
`seated`. All the logic (and all the tests) live in the pure policy; this shell
exists only to satisfy the `SeatAdapter` protocol so the table manager can drive
a v0 bot like any other seat.
"""

from __future__ import annotations

from typing import Any

from mahjong.adapters.base import BotIdentity, LeaveReason, Prompt, SeatContext
from mahjong.bots.v0 import decide_action
from mahjong.engine.types import Action, SeatView


class V0Adapter:
    """In-process greedy offense bot. Replaces the `CannedAdapter`-PASS
    placeholder on `kind: "bot"` seats."""

    identity: BotIdentity
    kind = "bot"

    def __init__(self) -> None:
        self.identity = {
            "kind": "bot",
            "bot_id": "v0",
            "version": "0",
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


__all__ = ["V0Adapter"]
