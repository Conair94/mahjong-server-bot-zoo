"""Invite-code persistence tests — docs/specs/public-deployment.md § 24.2.

Step-1 (persistence) slice of the public-deployment spec. Covers the invite
table mechanics and the atomic single-use redemption guard:

  - Fixture 9  (persistence part): redeem a fresh single-use invite → used_count = 1.
  - Fixture 10: re-using a spent invite is rejected; used_count does not move.
  - Fixture 11: an expired invite is rejected.
  - Fixture 12: a disabled invite is rejected.
  - Fixture 14: concurrent redemption of a single-use code → exactly one winner
                (the load-bearing race test — pins the in-transaction increment).

Fixtures 13 (username-taken does not consume the invite) and 15 (display_name
sanitisation) live with ``handle_register`` in step 2, where the username check
composes ahead of the redeem inside one transaction.

In-memory DBs for the fast cases; a file-backed DB for the threaded race test
(``:memory:`` connections can't be shared across threads).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from mahjong.persistence import apply_migrations, open_db
from mahjong.persistence.auth import create_account
from mahjong.persistence.invites import (
    get_invite,
    mint_invite,
    redeem_invite,
    set_invite_disabled,
)

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _open_memory() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # mirror open_db; persistence uses named access
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    apply_migrations(conn)
    return conn


def _make_admin(conn: sqlite3.Connection) -> int:
    """An admin account to satisfy the invites.created_by FK."""
    return create_account(
        conn,
        username="rootadmin",
        display_name="Root Admin",
        kind="human",
        role="admin",
        password="adminpw123",
    )


@pytest.fixture()
def db() -> sqlite3.Connection:
    return _open_memory()


@pytest.fixture()
def admin_id(db: sqlite3.Connection) -> int:
    return _make_admin(db)


# ---------------------------------------------------------------------------
# Mint + read-back
# ---------------------------------------------------------------------------


def test_mint_returns_prefixed_code(db: sqlite3.Connection, admin_id: int) -> None:
    code = mint_invite(db, created_by=admin_id, created_at_ms=1000)
    assert code.startswith("inv_")
    assert len(code) == len("inv_") + 16  # 8 bytes hex


def test_mint_defaults_single_use_not_disabled(
    db: sqlite3.Connection, admin_id: int
) -> None:
    code = mint_invite(db, created_by=admin_id, created_at_ms=1000)
    row = get_invite(db, code)
    assert row is not None
    assert row.created_by == admin_id
    assert row.max_uses == 1
    assert row.used_count == 0
    assert row.disabled is False
    assert row.expires_at_ms is None


def test_mint_unique_codes(db: sqlite3.Connection, admin_id: int) -> None:
    codes = {mint_invite(db, created_by=admin_id, created_at_ms=1000) for _ in range(50)}
    assert len(codes) == 50


def test_get_unknown_code_returns_none(db: sqlite3.Connection) -> None:
    assert get_invite(db, "inv_does_not_exist") is None


# ---------------------------------------------------------------------------
# Fixture 9 (persistence part): fresh single-use redemption succeeds
# ---------------------------------------------------------------------------


def test_redeem_fresh_single_use_succeeds(
    db: sqlite3.Connection, admin_id: int
) -> None:
    code = mint_invite(db, created_by=admin_id, created_at_ms=1000)
    assert redeem_invite(db, code, now_ms=2000) is True
    db.commit()
    assert get_invite(db, code).used_count == 1


# ---------------------------------------------------------------------------
# Fixture 10: re-using a spent single-use invite is rejected
# ---------------------------------------------------------------------------


def test_redeem_spent_invite_rejected(db: sqlite3.Connection, admin_id: int) -> None:
    code = mint_invite(db, created_by=admin_id, created_at_ms=1000)
    assert redeem_invite(db, code, now_ms=2000) is True
    db.commit()
    # Second redemption must fail and must not move the counter.
    assert redeem_invite(db, code, now_ms=2001) is False
    db.commit()
    assert get_invite(db, code).used_count == 1


# ---------------------------------------------------------------------------
# Fixture 11: expired invite rejected
# ---------------------------------------------------------------------------


def test_redeem_expired_invite_rejected(db: sqlite3.Connection, admin_id: int) -> None:
    code = mint_invite(
        db, created_by=admin_id, created_at_ms=1000, expires_at_ms=1500
    )
    assert redeem_invite(db, code, now_ms=2000) is False  # now > expiry
    db.commit()
    assert get_invite(db, code).used_count == 0


def test_redeem_unexpired_invite_succeeds(
    db: sqlite3.Connection, admin_id: int
) -> None:
    code = mint_invite(
        db, created_by=admin_id, created_at_ms=1000, expires_at_ms=5000
    )
    assert redeem_invite(db, code, now_ms=2000) is True  # now < expiry


# ---------------------------------------------------------------------------
# Fixture 12: disabled invite rejected
# ---------------------------------------------------------------------------


def test_redeem_disabled_invite_rejected(
    db: sqlite3.Connection, admin_id: int
) -> None:
    code = mint_invite(db, created_by=admin_id, created_at_ms=1000)
    set_invite_disabled(db, code, True)
    db.commit()
    assert redeem_invite(db, code, now_ms=2000) is False
    db.commit()
    assert get_invite(db, code).used_count == 0


# ---------------------------------------------------------------------------
# Multi-use invites + unknown code
# ---------------------------------------------------------------------------


def test_redeem_multi_use_allows_up_to_max(
    db: sqlite3.Connection, admin_id: int
) -> None:
    code = mint_invite(db, created_by=admin_id, created_at_ms=1000, max_uses=3)
    assert [redeem_invite(db, code, now_ms=2000) for _ in range(3)] == [True, True, True]
    db.commit()
    assert redeem_invite(db, code, now_ms=2000) is False  # 4th over budget
    db.commit()
    assert get_invite(db, code).used_count == 3


def test_redeem_unknown_code_returns_false(db: sqlite3.Connection) -> None:
    assert redeem_invite(db, "inv_nope", now_ms=2000) is False


# ---------------------------------------------------------------------------
# Fixture 14: concurrent redemption of a single-use code → exactly one winner
# ---------------------------------------------------------------------------


def test_concurrent_single_use_redemption_one_winner(tmp_path: Path) -> None:
    """The load-bearing race test.

    Eight threads, each on its own connection to a shared file DB, race to
    redeem one single-use invite. The conditional UPDATE
    (``WHERE used_count < max_uses``) is a single atomic statement, so under
    SQLite's single-writer serialisation exactly one thread can move the
    counter 0 → 1; the rest see ``used_count = max_uses`` and fail.
    """
    db_path = tmp_path / "race.db"
    setup = open_db(db_path)
    apply_migrations(setup)
    admin = _make_admin(setup)
    code = mint_invite(setup, created_by=admin, created_at_ms=1000, max_uses=1)
    setup.close()

    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = open_db(db_path)
        try:
            ok = redeem_invite(conn, code, now_ms=2000)
            conn.commit()
            with lock:
                results.append(ok)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1, f"expected exactly one winner, got {sum(results)}"

    check = open_db(db_path)
    used = check.execute(
        "SELECT used_count FROM invites WHERE code = ?", (code,)
    ).fetchone()[0]
    check.close()
    assert used == 1
