"""`HumanAdapter`: SeatAdapter implementation against a session-mux seat slot.

Spec: docs/specs/session-mux.md § The HumanAdapter, docs/specs/seat-port.md
§ HumanTuiAdapter.

The adapter is intentionally thin: the heavy lifting (ring buffer, pending
prompt, hold timer, conflict resolution) lives in `SeatSession`. This file's
job is the translation between the seat-port `Prompt` (monotonic deadline,
seat-port `Action` grammar, opaque `context` dict) and the wire-shaped
`SeatPrompt` the mux consumes (stable `prompt_id`, wire `deadline_ms`).

Lifecycle assumption: the seat is already bound (LIVE or HELD) by the time
the table manager constructs this adapter. `seated()` is a no-op — the
`ATTACHED` frame was sent at bind time by `TableSessions.attach`. The
adapter is created and destroyed per hand; the underlying `SeatSession`
persists across hands (going UNBOUND at hand end if no client lingers).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, cast

from mahjong.adapters.base import (
    HumanIdentity,
    LeaveReason,
    Prompt,
    SeatContext,
    SeatError,
)
from mahjong.engine.types import Action, SeatView
from mahjong.sessions import SeatHoldExpired, SeatPrompt, SeatSession

logger = logging.getLogger(__name__)

# Spec 37: decision-time analysis hook. Called with (prompt, seat); the
# returned payload rides the PROMPT frame as `stats`. Bound at the
# composition root so this module never imports pymj/analysis.
StatsProvider = Callable[[Prompt, int], dict[str, Any] | None]


class HumanAdapter:
    """SeatAdapter wrapping a single seat of a `SessionMux` (concretely, one
    `SeatSession`). Implements the five-method seat-port Protocol."""

    identity: HumanIdentity
    kind = "human"

    def __init__(
        self,
        *,
        session: SeatSession,
        identity: HumanIdentity,
        stats_provider: StatsProvider | None = None,
    ) -> None:
        self._session = session
        self.identity = identity
        self._ctx: SeatContext | None = None
        self._stats_provider = stats_provider

    async def seated(self, ctx: SeatContext) -> None:
        # The session is already attached; `ATTACHED` was sent at bind time.
        # We just retain `ctx` for prompt-id derivation.
        self._ctx = ctx

    async def observe(self, event: dict[str, Any], view: SeatView) -> None:
        # `view` is ignored: the session re-projects from the canonical record
        # event via `project_event(event, seat)`. This avoids two sources of
        # truth for "what does this seat see?" — the engine's projection rule
        # is the only one.
        del view
        await self._session.observe(event)

    async def decide(self, prompt: Prompt) -> Action:
        seat_prompt = self._translate_prompt(prompt)
        try:
            result = await self._session.decide(seat_prompt)
        except SeatHoldExpired as exc:
            # Translate the session-mux exception to the seat-port one the
            # table manager's strike path expects.
            raise SeatError(str(exc)) from exc
        return cast(Action, result)

    async def left(self, reason: LeaveReason) -> None:
        # The seat-port spec lists four `LeaveReason`s; map each to the
        # appropriate session-level teardown.
        if reason == "HAND_ENDED":
            # HAND_END was already emitted by `observe()` (Step 7.6.i); do NOT
            # call unbind_after_hand_end() here.
            #
            # In multi-hand mode the orchestrator calls
            # `TableSessions.begin_next_hand()` after `run_hand` returns, which
            # sends DETACH(hand_ended) + ATTACHED(new hand) to still-connected
            # clients.  That path requires the SeatSession to still be LIVE (or
            # HELD for a dropped client); calling unbind_after_hand_end() would
            # tear the session to UNBOUND and make begin_next_hand() a no-op.
            #
            # In single-hand mode the session stays LIVE until orch.close()
            # drops the WebSocket server, which is correct.
            return
        if reason == "TABLE_CLOSED":
            await self._session.shutdown(reason="table_closed")
            return
        if reason == "REPLACED":
            await self._session.shutdown(reason="replaced_by_autopass")
            return
        # "ERROR"
        await self._session.shutdown(reason="internal_error")

    # --- internals ---

    def _translate_prompt(self, prompt: Prompt) -> SeatPrompt:
        loop = asyncio.get_event_loop()
        ctx = prompt["context"]
        seat = self._ctx["seat"] if self._ctx is not None else -1
        # `prompt_id` must survive reconnects (the client echoes it back).
        # Derive from stable identifiers: seat, turn_index, phase.
        prompt_id = f"p_{seat}_{ctx.get('turn_index', 0)}_{prompt['kind']}"

        # Translate monotonic deadline → wire-format absolute Unix epoch ms.
        remaining = max(0.0, prompt["deadline"] - loop.time())
        deadline_ms = int(time.time() * 1000 + remaining * 1000)

        # Spec 37 § Failure containment: stats are garnish — any provider
        # failure is logged and the prompt goes out without them. The decide
        # path must never be blocked by analysis.
        stats: dict[str, Any] | None = None
        if self._stats_provider is not None:
            try:
                stats = self._stats_provider(prompt, seat)
            except Exception:
                logger.warning(
                    "hand_stats_failed prompt_id=%s seat=%d — sending prompt without stats",
                    prompt_id,
                    seat,
                    exc_info=True,
                )

        return SeatPrompt(
            prompt_id=prompt_id,
            phase=prompt["kind"],
            legal_actions=[dict(a) for a in prompt["legal_actions"]],
            default_action=dict(prompt["default_action"]),
            deadline=prompt["deadline"],
            deadline_ms=deadline_ms,
            stats=stats,
        )


__all__ = ["HumanAdapter"]
