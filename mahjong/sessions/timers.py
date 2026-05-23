"""Idempotent `asyncio.call_later` wrappers used by the session multiplexer.

Spec: docs/specs/session-mux.md § Timers and clocks.

`IdempotentTimer` holds at most one scheduled callback. Repeated `arm()` calls
cancel the previous schedule; `cancel()` on an unarmed timer is a no-op. The
handler runs in the asyncio event loop; if the callback itself is a coroutine
factory, the caller must wrap it in `asyncio.ensure_future` — this wrapper is
deliberately synchronous so handlers that just flip a flag don't pay for task
creation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class IdempotentTimer:
    """A one-shot asyncio timer that can be re-armed atomically.

    Invariants:
    - At most one `asyncio.TimerHandle` exists at any moment.
    - `cancel()` and `arm()` are safe to call regardless of current state.
    - A fired-and-completed handler leaves `armed` False.
    """

    __slots__ = ("_handle", "_loop")

    def __init__(self) -> None:
        self._handle: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def arm(self, delay: float, callback: Callable[[], None]) -> None:
        """Schedule `callback` to fire after `delay` seconds.

        Cancels any previously-armed schedule. The callback is wrapped so the
        timer marks itself as un-armed before the user's callback runs (so
        callbacks that re-arm the same timer work correctly).
        """
        self.cancel()
        loop = asyncio.get_event_loop()
        self._loop = loop

        def _fire() -> None:
            self._handle = None
            callback()

        self._handle = loop.call_later(max(0.0, delay), _fire)

    def cancel(self) -> None:
        """Cancel any pending schedule. Safe to call when unarmed."""
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    @property
    def armed(self) -> bool:
        return self._handle is not None


__all__ = ["IdempotentTimer"]
