"""Regression: the Persistence façade serializes its shared SQLite connection.

The server opens ONE ``sqlite3.Connection`` with ``check_same_thread=False`` and
touches it from both the event-loop thread and the ``run_in_executor`` pool
(auth/resume/register, profile/history/replay builders). A single SQLite
connection is not safe for concurrent use: two threads stepping statements on it
at once corrupt each other, which surfaced as the flaky multi-human e2e tests
DEF-14 (``sqlite3.InterfaceError`` → a 1011 connection crash) and DEF-23 (a
valid login read back as ``ok=False``).

``Persistence._synchronize_facade`` makes every façade call atomic under one
re-entrant lock. These tests hammer the façade from a thread pool and assert no
exception is raised and no valid login is ever rejected. Without the lock they
fail the great majority of runs (verified by temporarily disabling the
decorator); with it they are deterministic.
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest

from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account

# argon2 in handle_auth_request widens the read→write window, so the auth path
# is the most reliable reproducer. Enough concurrency to make the pre-fix race
# fire near-certainly while staying fast under the lock (serialized, ~instant).
_WORKERS = 8
_CALLS = 400

pytestmark = pytest.mark.slow


def _make_persistence(tmp_path: Path) -> tuple[Persistence, list[tuple[str, str]]]:
    p = Persistence(tmp_path / "mahjong.db", tmp_path)
    users = [(f"user{i}", f"password{i:04d}xx") for i in range(6)]
    for name, pw in users:
        create_account(
            p._conn, username=name, display_name=name, kind="human", role="user", password=pw
        )
    return p, users


def test_concurrent_authenticate_is_serialized(tmp_path: Path) -> None:
    """Many concurrent valid logins on the shared connection: all succeed, none
    raise. Pins the auth-path half of the DEF-14 / DEF-23 fix."""
    p, users = _make_persistence(tmp_path)
    rejected: list[str] = []
    raised: list[str] = []

    def auth_once(idx: int) -> None:
        name, pw = users[idx % len(users)]
        try:
            if not p.authenticate(name, pw).ok:
                rejected.append(name)
        except Exception as exc:  # any raise here is a race-induced failure
            raised.append(f"{type(exc).__name__}: {exc}")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            list(ex.map(auth_once, range(_CALLS)))
    finally:
        p.close()

    assert not raised, f"connection race raised {len(raised)}: {raised[:5]}"
    assert not rejected, f"valid login(s) read back as ok=False {len(rejected)}: {rejected[:5]}"


def test_concurrent_mixed_reads_and_writes_are_serialized(tmp_path: Path) -> None:
    """A read (get_account_by_username) interleaved with a write (insert_session)
    across threads: the façade methods must not corrupt the connection. Pins the
    façade-decorator half of the fix for the non-auth executor paths."""
    p, users = _make_persistence(tmp_path)
    raised: list[str] = []

    def churn(idx: int) -> None:
        name, _ = users[idx % len(users)]
        try:
            acct = p.get_account_by_username(name)
            assert acct is not None
            p.insert_session(
                session_id=f"s_{idx}",
                account_id=acct.account_id,
                issued_at_ms=1,
                expires_at_ms=10_000_000,
            )
            assert p.get_session(f"s_{idx}") is not None
        except Exception as exc:  # any raise here is a race-induced failure
            raised.append(f"{type(exc).__name__}: {exc}")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            list(ex.map(churn, range(_CALLS)))
    finally:
        p.close()

    assert not raised, f"connection race raised {len(raised)}: {raised[:5]}"
