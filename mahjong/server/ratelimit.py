"""In-process sliding-window rate limiter — public-deployment.md § 24.3.

Keyed by client IP (resolved proxy-aware in ``wire/server.py``). One home box,
one process: an in-memory limiter is enough — a network round-trip per login
check would be slower than the argon2 verify it's protecting (YAGNI on a Redis
store until there's a second process). The window resets on restart, which is
acceptable: an attacker gains at most one fresh window per restart, and restarts
are rare.

Two usage shapes:

- ``allow(key)`` — check-and-record in one call. For surfaces where *every*
  attempt counts (REGISTER, FEEDBACK).
- ``would_allow(key)`` + ``record(key)`` — peek without consuming, then record
  conditionally. For the login path, where only *failed* AUTH_REQUESTs count
  and the budget check must short-circuit before the argon2 verify.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """Per-key sliding-window event counter.

    A key is allowed while it has fewer than ``max_events`` recorded events in
    the trailing ``window_s`` seconds. Old events are pruned lazily on access;
    ``sweep()`` drops fully-idle keys so the dict doesn't grow without bound.
    """

    def __init__(
        self,
        *,
        max_events: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_events
        self._window = window_s
        self._clock = clock
        self._events: dict[str, deque[float]] = {}

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self._window
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def would_allow(self, key: str) -> bool:
        """True if *key* is under budget. Does NOT record or create an entry."""
        dq = self._events.get(key)
        if dq is None:
            return True
        self._prune(dq, self._clock())
        return len(dq) < self._max

    def record(self, key: str) -> None:
        """Record one event for *key* (consumes budget)."""
        now = self._clock()
        dq = self._events.get(key)
        if dq is None:
            dq = deque()
            self._events[key] = dq
        self._prune(dq, now)
        dq.append(now)

    def allow(self, key: str) -> bool:
        """Check-and-record: True (and records) if under budget, else False."""
        if not self.would_allow(key):
            return False
        self.record(key)
        return True

    def sweep(self) -> None:
        """Drop keys whose entire window has expired. Bounds memory."""
        now = self._clock()
        stale = []
        for key, dq in self._events.items():
            self._prune(dq, now)
            if not dq:
                stale.append(key)
        for key in stale:
            del self._events[key]

    def active_keys(self) -> int:
        """Number of keys currently tracked (for tests / introspection)."""
        return len(self._events)


__all__ = ["SlidingWindowLimiter"]
