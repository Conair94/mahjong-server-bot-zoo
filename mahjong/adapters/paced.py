"""PacedAdapter: wall-clock pacing wrapper for bot / canned adapters.

Spec: docs/specs/layer8-closeout.md § §2 Bot pacing.

Wraps an inner :class:`SeatAdapter` and sleeps a per-prompt uniform-random
delay before delegating ``decide``.  All other Protocol methods pass
through unchanged.  Default range (5.0, 10.0) makes bot turns visible at
human reading speed without slowing the table to a crawl.

Used only when ``ServerConfig.bot_pacing_enabled`` is True; CI suites and
the self-play harness pass-through their bot adapters un-wrapped so unit
tests and training runs aren't slowed.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from mahjong.adapters.base import (
    AdapterKind,
    LeaveReason,
    Prompt,
    SeatAdapter,
    SeatContext,
    SeatIdentity,
)
from mahjong.engine.types import Action, SeatView


class PacedAdapter:
    """Wrap a bot/canned adapter, sleep ``uniform(min_s, max_s)`` before
    each ``decide`` to humanize the pace at a multi-human table.

    The sample is clamped to leave a 0.5 s safety margin under the prompt's
    decide-timeout budget, so a tight deadline never causes the inner
    adapter to self-timeout *because of* pacing.
    """

    identity: SeatIdentity
    kind: AdapterKind

    def __init__(
        self,
        inner: SeatAdapter,
        *,
        min_s: float,
        max_s: float,
        rng: random.Random | None = None,
    ) -> None:
        if min_s < 0 or max_s < min_s:
            raise ValueError(
                f"PacedAdapter requires 0 <= min_s <= max_s; got min_s={min_s}, max_s={max_s}"
            )
        self._inner = inner
        self._min_s = min_s
        self._max_s = max_s
        self._rng = rng if rng is not None else random.Random()
        # Mirror inner identity / kind so call sites (table manager's per-kind
        # decide-timeout lookup, summaries) see the wrapped seat as its
        # underlying kind, not as a wrapper layer.
        self.identity = inner.identity
        self.kind = inner.kind

    @property
    def inner(self) -> SeatAdapter:
        return self._inner

    async def seated(self, ctx: SeatContext) -> None:
        await self._inner.seated(ctx)

    async def observe(self, event: dict[str, Any], view: SeatView) -> None:
        await self._inner.observe(event, view)

    async def decide(self, prompt: Prompt) -> Action:
        delay = self._rng.uniform(self._min_s, self._max_s)
        # Leave a 0.5 s safety margin so the inner adapter still has time
        # to compute its action without self-timeout.
        budget = max(0.0, prompt["deadline"] - prompt["issued_at"] - 0.5)
        delay = min(delay, budget)
        if delay > 0:
            await asyncio.sleep(delay)
        return await self._inner.decide(prompt)

    async def left(self, reason: LeaveReason) -> None:
        await self._inner.left(reason)


__all__ = ["PacedAdapter"]
