"""AutoPassAdapter: degraded fallback adapter.

Spec: docs/specs/seat-port.md § AutoPassAdapter.

The structural guarantee behind "the hand always completes": when an adapter
times out / errors / is otherwise unwilling to play, the table manager
substitutes one of these. `decide` returns `prompt.default_action` immediately;
the rest is no-ops.
"""

from __future__ import annotations

from typing import Any

from mahjong.adapters.base import CannedIdentity, LeaveReason, Prompt, SeatContext
from mahjong.engine.types import Action, SeatView


class AutoPassAdapter:
    """No-op adapter that submits the table's default action every prompt.

    Records produced through this adapter should carry an `auto_pass: true`
    marker on the resulting event (added by the table manager, not here —
    the adapter has no record-write surface).
    """

    identity: CannedIdentity

    def __init__(self) -> None:
        # Marked as `canned`/`autopass` so it surfaces clearly in the record's
        # `HEADER.seats[].identity` slot; the SeatIdentity union doesn't yet
        # carry a dedicated `autopass` variant (would be additive if needed).
        self.identity = {"kind": "canned", "script": "autopass"}

    async def seated(self, ctx: SeatContext) -> None:
        return None

    async def observe(self, event: dict[str, Any], view: SeatView) -> None:
        return None

    async def decide(self, prompt: Prompt) -> Action:
        return prompt["default_action"]

    async def left(self, reason: LeaveReason) -> None:
        return None


__all__ = ["AutoPassAdapter"]
