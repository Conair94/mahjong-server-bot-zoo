"""Persistence layer for the mahjong server.

Public API:
  ``open_db(path)``             — open (or create) the SQLite DB with all PRAGMAs set.
  ``apply_migrations(conn)``    — advance the schema to the latest migration.
  ``Persistence``               — high-level façade: owns a connection + data_dir.

The record file is the source of truth for the *contents* of a hand; SQLite
is the source of truth for *finding* a hand and *who played it*.
See docs/specs/sqlite-schema.md and docs/specs/persistence-api.md.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from mahjong.persistence import accounts as _accounts
from mahjong.persistence import hands as _hands
from mahjong.persistence import invites as _invites
from mahjong.persistence import rebuild as _rebuild
from mahjong.persistence.db import open_db
from mahjong.persistence.migrations import apply_migrations
from mahjong.persistence.models import (
    Account,
    HandRow,
    IntegrityReport,
    InviteRow,
    Participant,
    RebuildReport,
    SessionRow,
)

if TYPE_CHECKING:
    pass

__all__ = [
    "Account",
    "HandRow",
    "IntegrityReport",
    "InviteRow",
    "Participant",
    "Persistence",
    "RebuildReport",
    "SessionRow",
    "apply_migrations",
    "open_db",
]


class Persistence:
    """High-level persistence façade.

    Holds a ``sqlite3.Connection`` and a ``data_dir`` (root of the record
    file tree).  Every write method commits atomically.  Tests construct it
    with ``db_path=":memory:"``.

    Spec: docs/specs/persistence-api.md § Public API.
    """

    def __init__(self, db_path: str | os.PathLike[str], data_dir: Path) -> None:
        """Open the DB; apply pragmas + migrations; verify schema_version."""
        self._conn: sqlite3.Connection = open_db(db_path)
        apply_migrations(self._conn)
        self._data_dir: Path = data_dir

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> Persistence:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Account helpers  (consumed by auth.md)
    # ------------------------------------------------------------------

    def get_account_by_username(self, username: str) -> Account | None:
        return _accounts.get_account_by_username(self._conn, username)

    def get_account_by_id(self, account_id: int) -> Account | None:
        return _accounts.get_account_by_id(self._conn, account_id)

    def insert_account(
        self,
        *,
        username: str,
        display_name: str,
        kind: str,
        role: str,
        password_hash: str,
    ) -> int:
        """INSERT an account row and return the new ``account_id``."""
        with self._conn:
            return _accounts.insert_account(
                self._conn,
                username=username,
                display_name=display_name,
                kind=kind,
                role=role,
                password_hash=password_hash,
                created_at_ms=int(time.time() * 1000),
            )

    def update_account_login(
        self,
        account_id: int,
        *,
        password_hash: str | None = None,
        last_login_ms: int | None = None,
    ) -> None:
        with self._conn:
            _accounts.update_account_login(
                self._conn,
                account_id,
                password_hash=password_hash,
                last_login_ms=last_login_ms,
            )

    def set_account_disabled(self, account_id: int, disabled: bool) -> None:
        with self._conn:
            _accounts.set_account_disabled(self._conn, account_id, disabled)

    def set_account_role(self, account_id: int, role: str) -> None:
        with self._conn:
            _accounts.set_account_role(self._conn, account_id, role)

    def list_accounts(self) -> list[Account]:
        return _accounts.list_accounts(self._conn)

    # ------------------------------------------------------------------
    # Invite helpers  (consumed by public-deployment.md + admin console)
    # ------------------------------------------------------------------

    def list_invites(self) -> list[InviteRow]:
        return _invites.list_invites(self._conn)

    def mint_invite(
        self,
        *,
        created_by: int,
        max_uses: int = 1,
        expires_at_ms: int | None = None,
    ) -> str:
        return _invites.mint_invite(
            self._conn,
            created_by=created_by,
            created_at_ms=int(time.time() * 1000),
            max_uses=max_uses,
            expires_at_ms=expires_at_ms,
        )

    def revoke_invite(self, code: str) -> None:
        with self._conn:
            _invites.set_invite_disabled(self._conn, code, True)

    # ------------------------------------------------------------------
    # Session helpers  (consumed by auth.md)
    # ------------------------------------------------------------------

    def insert_session(
        self,
        *,
        session_id: str,
        account_id: int,
        issued_at_ms: int,
        expires_at_ms: int,
        last_seen_ms: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        with self._conn:
            _accounts.insert_session(
                self._conn,
                session_id=session_id,
                account_id=account_id,
                issued_at_ms=issued_at_ms,
                expires_at_ms=expires_at_ms,
                last_seen_ms=last_seen_ms,
                user_agent=user_agent,
            )

    def get_session(self, session_id: str) -> SessionRow | None:
        return _accounts.get_session(self._conn, session_id)

    def renew_session(
        self, session_id: str, *, expires_at_ms: int, last_seen_ms: int
    ) -> None:
        with self._conn:
            _accounts.renew_session(
                self._conn,
                session_id,
                expires_at_ms=expires_at_ms,
                last_seen_ms=last_seen_ms,
            )

    def revoke_session(self, session_id: str) -> None:
        with self._conn:
            _accounts.revoke_session(self._conn, session_id)

    def delete_expired_sessions(self, before_ms: int) -> int:
        """Returns rows deleted."""
        with self._conn:
            return _accounts.delete_expired_sessions(self._conn, before_ms)

    # ------------------------------------------------------------------
    # Hand helpers  (consumed by table manager + queries)
    # ------------------------------------------------------------------

    def reserve_hand(
        self,
        *,
        hand_id: str,
        match_id: str | None,
        hand_index_in_match: int,
        ruleset_id: str,
        ruleset_config_hash: str,
        started_at_ms: int,
        master_seed: str,
        record_path: str,
        server_version: str,
        source: str,
        participants: list[Participant],
    ) -> None:
        """Atomic INSERT: hand_index row + participants rows."""
        _hands.reserve_hand(
            self._conn,
            hand_id=hand_id,
            match_id=match_id,
            hand_index_in_match=hand_index_in_match,
            ruleset_id=ruleset_id,
            ruleset_config_hash=ruleset_config_hash,
            started_at_ms=started_at_ms,
            master_seed=master_seed,
            record_path=record_path,
            server_version=server_version,
            source=source,
            participants=participants,
        )
        # Note: hands.reserve_hand wraps its own `with conn:` for atomicity.

    def finalize_hand(
        self,
        hand_id: str,
        *,
        ended_at_ms: int,
        terminal_kind: str,
        winner_seat: int | None,
        fan_total: int | None,
        record_checksum: str,
        participants_scores: dict[int, int],
    ) -> None:
        """Atomic UPDATE: hand_index terminals + per-seat score deltas."""
        _hands.finalize_hand(
            self._conn,
            hand_id,
            ended_at_ms=ended_at_ms,
            terminal_kind=terminal_kind,
            winner_seat=winner_seat,
            fan_total=fan_total,
            record_checksum=record_checksum,
            participants_scores=participants_scores,
        )

    def get_hand(self, hand_id: str) -> HandRow | None:
        return _hands.get_hand(self._conn, hand_id)

    def find_hands_by_account(
        self,
        account_id: int,
        *,
        limit: int = 50,
        before_hand_id: str | None = None,
    ) -> list[HandRow]:
        return _hands.find_hands_by_account(
            self._conn, account_id, limit=limit, before_hand_id=before_hand_id
        )

    def find_hands_by_match(self, match_id: str) -> list[HandRow]:
        return _hands.find_hands_by_match(self._conn, match_id)

    def find_recent_hands(self, limit: int = 50) -> list[HandRow]:
        return _hands.find_recent_hands(self._conn, limit)

    def find_in_progress_hands(self) -> list[HandRow]:
        return _hands.find_in_progress_hands(self._conn)

    # ------------------------------------------------------------------
    # Integrity / rebuild
    # ------------------------------------------------------------------

    def integrity_check(self) -> IntegrityReport:
        return _rebuild.integrity_check(self._conn, self._data_dir)

    def rebuild_index_from_records(self, *, dry_run: bool = False) -> RebuildReport:
        return _rebuild.rebuild_index_from_records(
            self._conn, self._data_dir, dry_run=dry_run
        )
