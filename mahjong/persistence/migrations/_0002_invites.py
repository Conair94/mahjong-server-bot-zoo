"""Migration 0002 — invites table (public-deployment.md § 24.2).

Adds the ``invites`` table backing invite-code registration. A code is
redeemable iff ``disabled = 0 AND used_count < max_uses AND
(expires_at_ms IS NULL OR expires_at_ms > now)``. Redemption is an atomic
conditional UPDATE (see ``persistence/invites.py``), so the single-use guard
holds under concurrent redemption.

down() drops the table.
"""

from __future__ import annotations

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE invites (
            code              TEXT PRIMARY KEY,
            created_by        INTEGER NOT NULL REFERENCES accounts(account_id),
            created_at_ms     INTEGER NOT NULL,
            expires_at_ms     INTEGER,
            max_uses          INTEGER NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
            used_count        INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
            disabled          INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1))
        );
        """
    )


def down(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS invites;
        """
    )
