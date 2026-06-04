"""Step 8.8.b — graceful drain + timeout escalation (server-lifecycle.md
fixture 14).

The drain is two-phase:

1. **Graceful** — ``drain_all`` refuses new tables and signals each table to
   finish its *current* hand and stop.  ``await_tables_drained`` then waits up
   to the shutdown timeout for the hand loops to exit naturally.
2. **Escalation** — any table still running at the deadline is the "pending"
   set; the lifecycle layer cancels it via ``close``.  A hung bot (a ``decide``
   that never resolves) is the scenario that exercises this: the hand never
   reaches its FOOTER, so the graceful wait times out and the task is cancelled.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from mahjong.engine.rulesets import MANIFEST
from mahjong.server.registry import TableRegistry

pytestmark = pytest.mark.asyncio

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}
SERVER_INFO: dict[str, Any] = {"version": "drain-test", "git_sha": "t", "host": "t"}


def _new_table(reg: TableRegistry, tmp_path: Path) -> str:
    return reg.create_table_direct(
        ruleset=MCR_REF,
        seed=1,
        server_info=SERVER_INFO,
        data_dir=tmp_path,
        max_hands=None,  # play indefinitely — like the live serve config
    )


async def test_drain_all_refuses_new_and_signals_stop(tmp_path: Path) -> None:
    reg = TableRegistry()
    tid = _new_table(reg, tmp_path)
    handle = reg.get_table(tid)
    assert reg.accepting_new

    await reg.drain_all()

    assert not reg.accepting_new
    assert reg.drain_started_monotonic is not None
    assert handle._stop_event.is_set()  # finish-current-hand-and-stop signal


async def test_await_tables_drained_empty_when_no_running_hand(
    tmp_path: Path,
) -> None:
    """A table that never started a hand (or whose hand finished) has nothing to
    wait on — the graceful phase returns immediately with no pending tables."""
    reg = TableRegistry()
    tid = _new_table(reg, tmp_path)
    handle = reg.get_table(tid)
    # A hand task that has already completed.
    handle._hand_task = asyncio.create_task(asyncio.sleep(0))
    await handle._hand_task

    await reg.drain_all()
    pending = await reg.await_tables_drained(timeout_s=0.5)
    assert pending == []


async def test_hung_table_times_out_then_escalation_cancels(
    tmp_path: Path,
) -> None:
    """Fixture 14: a hand that never finishes (hung bot) blocks the graceful
    wait until the timeout, is reported pending, and is then cancelled by the
    escalation step — the whole thing bounded by timeout + a small buffer."""
    reg = TableRegistry()
    tid = _new_table(reg, tmp_path)
    handle = reg.get_table(tid)
    # Simulate a hand stuck on a decide that never resolves.
    handle._hand_task = asyncio.create_task(asyncio.sleep(3600))

    await reg.drain_all()

    # Phase 2: graceful wait returns at ~timeout (does NOT hang for 3600s).
    t0 = time.monotonic()
    pending = await reg.await_tables_drained(timeout_s=0.2)
    elapsed = time.monotonic() - t0
    assert pending == [tid]
    assert 0.2 <= elapsed < 1.0, f"graceful wait took {elapsed:.2f}s"

    # The hung task is still alive (not cancelled by the graceful wait).
    assert not handle._hand_task.done()

    # Phase 3: escalation cancels it.
    await reg.close_table(tid)
    assert handle._hand_task.cancelled()
    assert tid not in reg._tables


async def test_drained_table_not_in_pending(tmp_path: Path) -> None:
    """A table whose hand finishes within the window is not in the pending set,
    so the escalation never touches it."""
    reg = TableRegistry()
    tid = _new_table(reg, tmp_path)
    handle = reg.get_table(tid)
    # A hand that finishes well within the drain timeout.
    handle._hand_task = asyncio.create_task(asyncio.sleep(0.05))

    await reg.drain_all()
    pending = await reg.await_tables_drained(timeout_s=1.0)
    assert pending == []
    assert handle._hand_task.done() and not handle._hand_task.cancelled()
