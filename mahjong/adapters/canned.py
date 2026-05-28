"""CannedAdapter: scripted-action adapter for tests.

Spec: docs/specs/seat-port.md § CannedAdapter.

No I/O, no subprocess, fully deterministic. The test workhorse — every
table-manager test wires four of these.
"""

from __future__ import annotations

from typing import Any

from mahjong.adapters.base import (
    CannedIdentity,
    LeaveReason,
    Prompt,
    SeatContext,
)
from mahjong.engine.types import Action, SeatView


class CannedAdapter:
    """Returns scripted actions in order, falling back to `prompt.default_action`
    if the script runs out or the next scripted action isn't in `legal_actions`.

    The fallback is deliberately silent (no exception): tests that *need* the
    script to be exhaustively consumed should assert on the resulting record,
    not on adapter internals.
    """

    identity: CannedIdentity
    kind = "canned"

    def __init__(self, identity: CannedIdentity, actions: list[Action]) -> None:
        self.identity = identity
        self._actions: list[Action] = list(actions)
        self._cursor = 0

    async def seated(self, ctx: SeatContext) -> None:
        return None

    async def observe(self, event: dict[str, Any], view: SeatView) -> None:
        return None

    async def decide(self, prompt: Prompt) -> Action:
        if self._cursor >= len(self._actions):
            return prompt["default_action"]
        nxt = self._actions[self._cursor]
        self._cursor += 1
        if nxt not in prompt["legal_actions"]:
            return prompt["default_action"]
        return nxt

    async def left(self, reason: LeaveReason) -> None:
        return None


__all__ = ["CannedAdapter"]
