"""Steps 8.8.c / 8.8.d — periodic WAL checkpoint + session cleanup.

Spec: docs/specs/server-lifecycle.md § Periodic tasks (fixtures 19, 20).

The single-tick helpers carry the logic and are unit-tested against a real DB;
a lighter test confirms the ``while True`` loop wires sleep → tick.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.server import periodic

pytestmark = pytest.mark.asyncio


def _make_persistence(tmp_path: Path) -> Persistence:
    (tmp_path / "records").mkdir(exist_ok=True)
    return Persistence(tmp_path / "mahjong.db", tmp_path)


def _seed_session(p: Persistence, *, session_id: str, expires_at_ms: int) -> int:
    account_id = create_account(
        p._conn,  # type: ignore[attr-defined]
        username=f"u_{session_id}",
        display_name="U",
        kind="human",
        role="user",
        password="passpass",
    )
    p.insert_session(
        session_id=session_id,
        account_id=account_id,
        issued_at_ms=0,
        expires_at_ms=expires_at_ms,
    )
    return account_id


# --- 8.8.d session cleanup (fixture 19) ------------------------------------


async def test_cleanup_deletes_expired_keeps_fresh(tmp_path: Path) -> None:
    p = _make_persistence(tmp_path)
    now = 1_000_000
    try:
        _seed_session(p, session_id="expired", expires_at_ms=now - 1)
        _seed_session(p, session_id="fresh", expires_at_ms=now + 10_000)

        deleted = periodic.run_session_cleanup_once(p, now_ms=now)

        assert deleted == 1
        assert p.get_session("expired") is None
        assert p.get_session("fresh") is not None
    finally:
        p.close()


async def test_cleanup_loop_invokes_tick(tmp_path: Path, monkeypatch) -> None:
    """The while-loop fires the cleanup once, then we cancel it."""
    p = _make_persistence(tmp_path)
    try:
        _seed_session(p, session_id="expired", expires_at_ms=0)

        # Fire the body exactly once: first sleep returns, second raises to break.
        state = {"slept": 0}

        async def fake_sleep(_s: float) -> None:
            state["slept"] += 1
            if state["slept"] >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await periodic.periodic_session_cleanup(p, interval_s=3600.0)

        # The single tick used the real clock (now_ms=None) → expires_at_ms=0
        # is in the past → deleted.
        assert p.get_session("expired") is None
    finally:
        p.close()


# --- 8.8.c WAL checkpoint (fixture 20) -------------------------------------


async def test_wal_checkpoint_passive_returns_int(tmp_path: Path) -> None:
    p = _make_persistence(tmp_path)
    try:
        # Generate WAL traffic.
        for i in range(5):
            _seed_session(p, session_id=f"s{i}", expires_at_ms=10_000_000)

        pages = periodic.run_wal_checkpoint_once(p, mode="PASSIVE")
        assert isinstance(pages, int)
        assert pages >= 0
    finally:
        p.close()


async def test_wal_checkpoint_truncate_collapses_wal(tmp_path: Path) -> None:
    """TRUNCATE (drain-time mode) leaves the -wal file zero-length."""
    p = _make_persistence(tmp_path)
    wal_path = tmp_path / "mahjong.db-wal"
    try:
        for i in range(5):
            _seed_session(p, session_id=f"s{i}", expires_at_ms=10_000_000)
        # WAL should have grown.
        assert wal_path.exists()

        p.wal_checkpoint(mode="TRUNCATE")

        # After TRUNCATE the WAL is collapsed to zero bytes (file may remain).
        assert (not wal_path.exists()) or wal_path.stat().st_size == 0
    finally:
        p.close()


async def test_wal_checkpoint_rejects_bad_mode(tmp_path: Path) -> None:
    p = _make_persistence(tmp_path)
    try:
        with pytest.raises(ValueError):
            p.wal_checkpoint(mode="DROP TABLE")
    finally:
        p.close()
