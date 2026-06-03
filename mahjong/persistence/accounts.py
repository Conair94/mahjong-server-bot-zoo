"""Low-level account and session CRUD — SQL primitives.

These functions take a raw ``sqlite3.Connection`` and execute a single
logical operation.  Callers manage transaction boundaries.

Spec: docs/specs/persistence-api.md § Public API (accounts + sessions).
Consumed by: ``auth.py`` (validation logic), ``Persistence`` class (__init__.py).
"""

from __future__ import annotations

import sqlite3

from mahjong.persistence.models import Account, SessionRow

# ---------------------------------------------------------------------------
# Account helpers
# ---------------------------------------------------------------------------


def get_account_by_username(conn: sqlite3.Connection, username: str) -> Account | None:
    """Return the Account row whose username matches *username* case-insensitively.

    Returns ``None`` if no match.
    """
    row = conn.execute(
        """
        SELECT account_id, username, display_name, kind, role, password_hash,
               disabled, created_at_ms, last_login_ms
        FROM accounts
        WHERE lower(username) = lower(?)
        """,
        (username,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_account(row)


def get_account_by_id(conn: sqlite3.Connection, account_id: int) -> Account | None:
    """Return the Account row for *account_id*, or ``None``."""
    row = conn.execute(
        """
        SELECT account_id, username, display_name, kind, role, password_hash,
               disabled, created_at_ms, last_login_ms
        FROM accounts
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_account(row)


def insert_account(
    conn: sqlite3.Connection,
    *,
    username: str,
    display_name: str,
    kind: str,
    role: str,
    password_hash: str,
    created_at_ms: int,
) -> int:
    """INSERT a new account row.  Returns the new ``account_id``.

    Does NOT commit — caller is responsible.
    Raises ``sqlite3.IntegrityError`` on constraint violations.
    """
    cursor = conn.execute(
        """
        INSERT INTO accounts
            (username, display_name, kind, role, password_hash, disabled, created_at_ms)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (username, display_name, kind, role, password_hash, created_at_ms),
    )
    return int(cursor.lastrowid)  # type: ignore[arg-type]


def update_account_login(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    password_hash: str | None = None,
    last_login_ms: int | None = None,
) -> None:
    """UPDATE ``password_hash`` and/or ``last_login_ms`` for *account_id*.

    Does NOT commit — caller is responsible.
    """
    if password_hash is not None:
        conn.execute(
            "UPDATE accounts SET password_hash = ? WHERE account_id = ?",
            (password_hash, account_id),
        )
    if last_login_ms is not None:
        conn.execute(
            "UPDATE accounts SET last_login_ms = ? WHERE account_id = ?",
            (last_login_ms, account_id),
        )


def set_account_disabled(
    conn: sqlite3.Connection,
    account_id: int,
    disabled: bool,
) -> None:
    """Flip the ``disabled`` flag for *account_id*.

    Does NOT commit — caller is responsible.
    """
    conn.execute(
        "UPDATE accounts SET disabled = ? WHERE account_id = ?",
        (1 if disabled else 0, account_id),
    )


def set_account_role(
    conn: sqlite3.Connection,
    account_id: int,
    role: str,
) -> None:
    """Set the ``role`` ('user' | 'admin') for *account_id*. Does NOT commit."""
    if role not in ("user", "admin"):
        raise ValueError(f"invalid role: {role!r}")
    conn.execute(
        "UPDATE accounts SET role = ? WHERE account_id = ?",
        (role, account_id),
    )


def list_accounts(conn: sqlite3.Connection) -> list[Account]:
    """All accounts ordered by id.  Read-only; backs the admin console + CLI."""
    rows = conn.execute(
        """
        SELECT account_id, username, display_name, kind, role, password_hash,
               disabled, created_at_ms, last_login_ms
        FROM accounts
        ORDER BY account_id
        """
    ).fetchall()
    return [_row_to_account(r) for r in rows]


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def insert_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    account_id: int,
    issued_at_ms: int,
    expires_at_ms: int,
    last_seen_ms: int | None = None,
    user_agent: str | None = None,
) -> None:
    """INSERT a new session row.  Does NOT commit — caller is responsible."""
    conn.execute(
        """
        INSERT INTO sessions
            (session_id, account_id, issued_at_ms, expires_at_ms,
             last_seen_ms, revoked, user_agent)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (
            session_id,
            account_id,
            issued_at_ms,
            expires_at_ms,
            last_seen_ms if last_seen_ms is not None else issued_at_ms,
            user_agent,
        ),
    )


def get_session(conn: sqlite3.Connection, session_id: str) -> SessionRow | None:
    """Return the SessionRow for *session_id*, or ``None``."""
    row = conn.execute(
        """
        SELECT session_id, account_id, issued_at_ms, expires_at_ms,
               last_seen_ms, revoked, user_agent
        FROM sessions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return SessionRow(
        session_id=row["session_id"],
        account_id=row["account_id"],
        issued_at_ms=row["issued_at_ms"],
        expires_at_ms=row["expires_at_ms"],
        last_seen_ms=row["last_seen_ms"],
        revoked=bool(row["revoked"]),
        user_agent=row["user_agent"],
    )


def renew_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    expires_at_ms: int,
    last_seen_ms: int,
) -> None:
    """Slide the expiry window for *session_id*.  Does NOT commit."""
    conn.execute(
        "UPDATE sessions SET expires_at_ms = ?, last_seen_ms = ? WHERE session_id = ?",
        (expires_at_ms, last_seen_ms, session_id),
    )


def revoke_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Set ``revoked = 1`` for *session_id*.  Does NOT commit."""
    conn.execute(
        "UPDATE sessions SET revoked = 1 WHERE session_id = ?",
        (session_id,),
    )


def delete_expired_sessions(conn: sqlite3.Connection, before_ms: int) -> int:
    """DELETE sessions whose ``expires_at_ms < before_ms``.

    Returns the number of rows deleted.  Does NOT commit.
    """
    cursor = conn.execute(
        "DELETE FROM sessions WHERE expires_at_ms < ?",
        (before_ms,),
    )
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        account_id=row["account_id"],
        username=row["username"],
        display_name=row["display_name"],
        kind=row["kind"],
        role=row["role"],
        password_hash=row["password_hash"],
        disabled=bool(row["disabled"]),
        created_at_ms=row["created_at_ms"],
        last_login_ms=row["last_login_ms"],
    )


__all__ = [
    "delete_expired_sessions",
    "get_account_by_id",
    "get_account_by_username",
    "get_session",
    "insert_account",
    "insert_session",
    "renew_session",
    "revoke_session",
    "set_account_disabled",
    "update_account_login",
]
