"""Persistence layer — schema + migration tests.

Spec: docs/specs/sqlite-schema.md § Verification fixtures (fixtures 1-10).

Fixtures 11-12 (round-trip hand record, rebuild from records) live in
``test_persistence_api.py`` alongside the Step-8.3 persistence API.

These tests use in-memory SQLite DBs for speed.  Each test gets a fresh DB
via the ``fresh_db`` fixture.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mahjong.persistence import apply_migrations, open_db

# ---------------------------------------------------------------------------
# Helpers / shared fixture
# ---------------------------------------------------------------------------


def _open_memory() -> sqlite3.Connection:
    """In-memory DB with all PRAGMAs set (foreign_keys, WAL not applicable
    for :memory: but busy_timeout is).  Used for fast schema tests."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@pytest.fixture()
def fresh_db() -> sqlite3.Connection:
    conn = _open_memory()
    apply_migrations(conn)
    return conn


# ---------------------------------------------------------------------------
# Fixture 1: Fresh-apply produces version=1 and all expected tables
# ---------------------------------------------------------------------------


def test_fresh_apply_schema_version_is_latest(fresh_db: sqlite3.Connection) -> None:
    """Fixture 1a: After apply_migrations() on an empty DB, version is the latest.

    Bump the expected number when adding a migration. 2 == _0001_initial +
    _0002_invites (public-deployment.md § 24.2)."""
    row = fresh_db.execute("SELECT version FROM schema_version").fetchone()
    assert row is not None
    assert row[0] == 2


def test_fresh_apply_all_tables_exist(fresh_db: sqlite3.Connection) -> None:
    """Fixture 1b: Every table required by the spec exists after fresh apply."""
    expected_tables = {"schema_version", "accounts", "sessions", "hand_index", "hand_participants", "invites"}
    rows = fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    actual_tables = {r[0] for r in rows}
    assert expected_tables <= actual_tables, (
        f"Missing tables: {expected_tables - actual_tables}"
    )


def test_fresh_apply_all_indexes_exist(fresh_db: sqlite3.Connection) -> None:
    """Fixture 1c: Every index required by the spec exists after fresh apply."""
    expected_indexes = {
        "accounts_username_lower",
        "sessions_account_active",
        "sessions_expires",
        "hand_index_started",
        "hand_index_match",
        "hand_index_winner",
        "hand_participants_account",
    }
    rows = fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    actual_indexes = {r[0] for r in rows}
    assert expected_indexes <= actual_indexes, (
        f"Missing indexes: {expected_indexes - actual_indexes}"
    )


# ---------------------------------------------------------------------------
# Fixture 2: Forward-from-previous (collapses to fixture 1 for v1)
# ---------------------------------------------------------------------------


def test_idempotent_on_already_migrated_db() -> None:
    """Fixture 2: Calling apply_migrations() on an already-current DB is a no-op."""
    conn = _open_memory()
    apply_migrations(conn)  # first apply
    apply_migrations(conn)  # second apply — must not raise or double-insert
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == 2
    count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 1, "schema_version must have exactly one row"


# ---------------------------------------------------------------------------
# Fixture 3: Foreign-key enforcement
# ---------------------------------------------------------------------------


def test_foreign_key_enforcement_on(fresh_db: sqlite3.Connection) -> None:
    """Fixture 3a: PRAGMA foreign_keys returns 1 on this connection."""
    row = fresh_db.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_foreign_key_enforcement_sessions(fresh_db: sqlite3.Connection) -> None:
    """Fixture 3b: Inserting a session for a non-existent account raises IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            """
            INSERT INTO sessions
                (session_id, account_id, issued_at_ms, expires_at_ms, last_seen_ms)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("tok_abc", 999, 1000, 2000, 1000),
        )


# ---------------------------------------------------------------------------
# Fixture 4: Username case-sensitivity at column, case-insensitive at lookup
# ---------------------------------------------------------------------------


def _insert_account(
    conn: sqlite3.Connection,
    username: str,
    display: str = "Display",
    kind: str = "human",
    password_hash: str = "hash",
) -> None:
    conn.execute(
        """
        INSERT INTO accounts
            (username, display_name, kind, password_hash, created_at_ms)
        VALUES (?, ?, ?, ?, ?)
        """,
        (username, display, kind, password_hash, 1_000_000),
    )


def test_username_case_sensitive_at_column(fresh_db: sqlite3.Connection) -> None:
    """Fixture 4a: Inserting 'Alice' and 'alice' succeeds (different bytes)."""
    _insert_account(fresh_db, "Alice", display="Alice")
    _insert_account(fresh_db, "alice", display="alice")  # must NOT raise
    count = fresh_db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    assert count == 2


def test_username_case_insensitive_index_lookup(fresh_db: sqlite3.Connection) -> None:
    """Fixture 4b: lower(username) index returns rows matching case-insensitively."""
    _insert_account(fresh_db, "Alice", display="Alice")
    _insert_account(fresh_db, "alice", display="alice")
    rows = fresh_db.execute(
        "SELECT username FROM accounts WHERE lower(username) = lower(?)", ("ALICE",)
    ).fetchall()
    usernames = {r[0] for r in rows}
    assert "Alice" in usernames
    assert "alice" in usernames


# ---------------------------------------------------------------------------
# Fixture 5: CHECK (kind IN ('human', 'bot'))
# ---------------------------------------------------------------------------


def test_accounts_kind_check_constraint(fresh_db: sqlite3.Connection) -> None:
    """Fixture 5: Inserting an account with kind='ghost' raises IntegrityError."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert_account(fresh_db, "ghost_user", kind="ghost")


# ---------------------------------------------------------------------------
# Fixture 6: PRIMARY KEY (hand_id, seat) on hand_participants
# ---------------------------------------------------------------------------


def _insert_hand_index(conn: sqlite3.Connection, hand_id: str) -> None:
    conn.execute(
        """
        INSERT INTO hand_index
            (hand_id, hand_index_in_match, ruleset_id, ruleset_config_hash,
             started_at_ms, master_seed, record_path, record_checksum, server_version)
        VALUES (?, 0, 'mcr-2006', 'abc123', 1000, 'seed_hex',
                'records/2026/01/test.jsonl', 'checksum_hex', '0.0.0')
        """,
        (hand_id,),
    )


def test_hand_participants_pk_rejects_duplicate(fresh_db: sqlite3.Connection) -> None:
    """Fixture 6a: Two rows with the same (hand_id, seat) raise IntegrityError."""
    _insert_hand_index(fresh_db, "hand-001")
    fresh_db.execute(
        "INSERT INTO hand_participants (hand_id, seat, seat_kind, wind) VALUES (?, 0, 'canned', 'F1')",
        ("hand-001",),
    )
    with pytest.raises(sqlite3.IntegrityError):
        fresh_db.execute(
            "INSERT INTO hand_participants (hand_id, seat, seat_kind, wind) VALUES (?, 0, 'canned', 'F1')",
            ("hand-001",),
        )


def test_hand_participants_pk_allows_four_seats(fresh_db: sqlite3.Connection) -> None:
    """Fixture 6b: Four rows with distinct seats succeed."""
    _insert_hand_index(fresh_db, "hand-002")
    for seat, wind in enumerate(["F1", "F2", "F3", "F4"]):
        fresh_db.execute(
            "INSERT INTO hand_participants (hand_id, seat, seat_kind, wind) VALUES (?, ?, 'canned', ?)",
            ("hand-002", seat, wind),
        )
    count = fresh_db.execute(
        "SELECT COUNT(*) FROM hand_participants WHERE hand_id = 'hand-002'"
    ).fetchone()[0]
    assert count == 4


# ---------------------------------------------------------------------------
# Fixture 7: CASCADE delete — hand_index → hand_participants
# ---------------------------------------------------------------------------


def test_cascade_delete_hand_index_to_participants(fresh_db: sqlite3.Connection) -> None:
    """Fixture 7: Deleting from hand_index cascades to hand_participants."""
    _insert_hand_index(fresh_db, "hand-003")
    fresh_db.execute(
        "INSERT INTO hand_participants (hand_id, seat, seat_kind, wind) VALUES ('hand-003', 0, 'human', 'F1')"
    )
    fresh_db.execute("DELETE FROM hand_index WHERE hand_id = 'hand-003'")
    count = fresh_db.execute(
        "SELECT COUNT(*) FROM hand_participants WHERE hand_id = 'hand-003'"
    ).fetchone()[0]
    assert count == 0, "CASCADE DELETE should remove hand_participants rows"


# ---------------------------------------------------------------------------
# Fixture 8: SET NULL on account deletion in hand_participants
# ---------------------------------------------------------------------------


def test_set_null_on_account_deletion(fresh_db: sqlite3.Connection) -> None:
    """Fixture 8: Deleting an account sets account_id NULL in hand_participants."""
    _insert_account(fresh_db, "player1")
    account_id = fresh_db.execute(
        "SELECT account_id FROM accounts WHERE username = 'player1'"
    ).fetchone()[0]

    _insert_hand_index(fresh_db, "hand-004")
    fresh_db.execute(
        "INSERT INTO hand_participants (hand_id, seat, account_id, seat_kind, wind) "
        "VALUES ('hand-004', 0, ?, 'human', 'F1')",
        (account_id,),
    )

    fresh_db.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))

    row = fresh_db.execute(
        "SELECT account_id FROM hand_participants WHERE hand_id = 'hand-004' AND seat = 0"
    ).fetchone()
    assert row is not None, "hand_participants row should NOT be deleted"
    assert row[0] is None, "account_id should be SET NULL, not deleted"


# ---------------------------------------------------------------------------
# Fixture 9: WAL mode + busy_timeout applied
# ---------------------------------------------------------------------------


def test_wal_mode_applied(tmp_path: Path) -> None:
    """Fixture 9a: A file-backed DB opened via open_db() is in WAL mode."""
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    apply_migrations(conn)
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal", f"Expected WAL mode, got {row[0]!r}"
    conn.close()


def test_busy_timeout_applied(tmp_path: Path) -> None:
    """Fixture 9b: busy_timeout is 5000 ms after open_db()."""
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 5000
    conn.close()


def test_foreign_keys_enabled_via_open_db(tmp_path: Path) -> None:
    """Fixture 9c: open_db() enables foreign_keys on the connection."""
    db_path = tmp_path / "test.db"
    conn = open_db(db_path)
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# Fixture 10: Schema snapshot stability
# ---------------------------------------------------------------------------


_EXPECTED_SCHEMA_PATH = Path(__file__).parent / "expected_schema.sql"


def test_schema_snapshot_stability(tmp_path: Path) -> None:
    """Fixture 10: After fresh-apply, the .schema output matches the checked-in snapshot.

    This is the regression gate against accidental schema drift.  If this test
    fails, either update the snapshot (intentional schema change) or revert the
    accidental change.
    """
    if not _EXPECTED_SCHEMA_PATH.exists():
        pytest.skip("expected_schema.sql not yet generated — run generate_schema_snapshot()")

    db_path = tmp_path / "snap.db"
    conn = open_db(db_path)
    apply_migrations(conn)

    rows = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type DESC, name ASC"
    ).fetchall()
    actual_sql = "\n".join(r[0].strip() for r in rows) + "\n"
    conn.close()

    expected_sql = _EXPECTED_SCHEMA_PATH.read_text()
    assert actual_sql == expected_sql, (
        "Schema snapshot mismatch — update tests/persistence/expected_schema.sql "
        "if this is an intentional change."
    )
