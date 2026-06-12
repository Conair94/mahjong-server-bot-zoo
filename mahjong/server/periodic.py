"""Long-lived periodic housekeeping tasks.

Spec: docs/specs/server-lifecycle.md § Periodic tasks (fixtures 19, 20).

Two background tasks run for the server's lifetime and are cancelled at drain:

- **Session cleanup** — delete session tokens whose ``expires_at_ms`` has
  passed.  Not load-bearing (the token-validation path re-checks expiry); this
  keeps the ``sessions`` table small.
- **WAL checkpoint** — ``PRAGMA wal_checkpoint(PASSIVE)`` to bound WAL growth
  without blocking writers.

Each task is a thin ``while True`` loop around a single-tick helper.  The
helpers are the unit-tested surface; the loops are trivial glue.

Note (deviation from the spec sketch): the spec's
``cutoff_ms = now - lifetime_hours*…`` double-counts the lifetime — a session's
``expires_at_ms`` is already ``issued + lifetime`` at insert time.  Fixture 19
defines "expired" as ``expires_at_ms < now`` and ``delete_expired_sessions``
deletes ``expires_at_ms < before_ms``, so the correct cutoff is simply ``now``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

_logger = logging.getLogger(__name__)


class _SessionStore(Protocol):
    def delete_expired_sessions(self, before_ms: int) -> int: ...


class _WalStore(Protocol):
    def wal_checkpoint(self, *, mode: str = "PASSIVE") -> int: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


# --- session cleanup -------------------------------------------------------


def run_session_cleanup_once(persistence: _SessionStore, *, now_ms: int | None = None) -> int:
    """Delete sessions already past their ``expires_at_ms``.  Returns count."""
    cutoff = _now_ms() if now_ms is None else now_ms
    deleted = persistence.delete_expired_sessions(before_ms=cutoff)
    _logger.info("sessions.cleanup deleted=%d", deleted)
    return deleted


async def periodic_session_cleanup(
    persistence: _SessionStore, *, interval_s: float = 3600.0
) -> None:
    """Run ``run_session_cleanup_once`` every *interval_s*.  Loops forever;
    cancelled at drain."""
    while True:
        await asyncio.sleep(interval_s)
        run_session_cleanup_once(persistence)


# --- WAL checkpoint --------------------------------------------------------


def run_wal_checkpoint_once(persistence: _WalStore, *, mode: str = "PASSIVE") -> int:
    """Run one ``PRAGMA wal_checkpoint(<mode>)``.  Returns pages checkpointed."""
    pages = persistence.wal_checkpoint(mode=mode)
    _logger.debug("db.wal_checkpoint pages_checkpointed=%d", pages)
    return pages


async def periodic_wal_checkpoint(persistence: _WalStore, *, interval_s: float) -> None:
    """Run a PASSIVE checkpoint every *interval_s*.  Loops forever; cancelled
    at drain."""
    while True:
        await asyncio.sleep(interval_s)
        run_wal_checkpoint_once(persistence, mode="PASSIVE")


__all__ = [
    "periodic_session_cleanup",
    "periodic_wal_checkpoint",
    "run_session_cleanup_once",
    "run_wal_checkpoint_once",
]
