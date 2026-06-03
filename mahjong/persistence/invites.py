"""Invite-code CRUD — SQL primitives for invite-gated registration.

Spec: docs/specs/public-deployment.md § 24.2.

``mint_invite`` is a standalone admin operation (commits). ``redeem_invite``
is designed to be composed *inside* the registration transaction
(``handle_register``, step 2): it does NOT commit, so a rolled-back
registration also rolls back the claim.

The redemption guard is a single conditional UPDATE rather than a
SELECT-then-UPDATE. That makes the check-and-increment atomic: under SQLite's
single-writer serialisation, two racing redemptions of a single-use code can't
both succeed — the second sees ``used_count = max_uses`` in its WHERE clause and
matches no row. (A naive check-then-act would have a TOCTOU race here.)
"""

from __future__ import annotations

import secrets
import sqlite3

from mahjong.persistence.models import InviteRow

INVITE_PREFIX = "inv_"


def _new_code() -> str:
    """Generate a fresh invite code: ``inv_`` + 16 hex chars (64 bits)."""
    return INVITE_PREFIX + secrets.token_hex(8)


def mint_invite(
    conn: sqlite3.Connection,
    *,
    created_by: int,
    created_at_ms: int,
    max_uses: int = 1,
    expires_at_ms: int | None = None,
    code: str | None = None,
) -> str:
    """INSERT a new invite and return its code. Commits.

    *code* is overridable only for tests; production callers let it default to
    a fresh random code.
    """
    if code is None:
        code = _new_code()
    with conn:
        conn.execute(
            """
            INSERT INTO invites
                (code, created_by, created_at_ms, expires_at_ms,
                 max_uses, used_count, disabled)
            VALUES (?, ?, ?, ?, ?, 0, 0)
            """,
            (code, created_by, created_at_ms, expires_at_ms, max_uses),
        )
    return code


def get_invite(conn: sqlite3.Connection, code: str) -> InviteRow | None:
    """Return the InviteRow for *code*, or ``None`` if no such code."""
    row = conn.execute(
        """
        SELECT code, created_by, created_at_ms, expires_at_ms,
               max_uses, used_count, disabled
        FROM invites
        WHERE code = ?
        """,
        (code,),
    ).fetchone()
    if row is None:
        return None
    return InviteRow(
        code=row["code"],
        created_by=row["created_by"],
        created_at_ms=row["created_at_ms"],
        expires_at_ms=row["expires_at_ms"],
        max_uses=row["max_uses"],
        used_count=row["used_count"],
        disabled=bool(row["disabled"]),
    )


def redeem_invite(conn: sqlite3.Connection, code: str, *, now_ms: int) -> bool:
    """Atomically claim one use of *code* iff it is redeemable.

    Returns True iff the claim succeeded (``used_count`` was incremented).
    Does NOT commit — the caller composes this inside the registration
    transaction so a later failure (e.g. duplicate username) rolls the claim
    back too.

    Redeemable iff: not disabled, ``used_count < max_uses``, and either no
    expiry or ``expires_at_ms > now_ms``.
    """
    cursor = conn.execute(
        """
        UPDATE invites
           SET used_count = used_count + 1
         WHERE code = ?
           AND disabled = 0
           AND used_count < max_uses
           AND (expires_at_ms IS NULL OR expires_at_ms > ?)
        """,
        (code, now_ms),
    )
    return cursor.rowcount == 1


def set_invite_disabled(
    conn: sqlite3.Connection, code: str, disabled: bool
) -> None:
    """Flip the ``disabled`` flag for *code*. Does NOT commit — caller commits.

    Backs the ``account invite revoke`` CLI (step 2).
    """
    conn.execute(
        "UPDATE invites SET disabled = ? WHERE code = ?",
        (1 if disabled else 0, code),
    )


__all__ = [
    "INVITE_PREFIX",
    "get_invite",
    "mint_invite",
    "redeem_invite",
    "set_invite_disabled",
]
