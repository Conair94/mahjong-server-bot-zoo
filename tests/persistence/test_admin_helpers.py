"""Admin-console persistence helpers: list_invites, list_accounts, set_account_role.

Spec: docs/specs/admin-console.md § 2.  Sync tests against an in-memory DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mahjong.persistence import Persistence
from mahjong.persistence.auth import create_account
from mahjong.persistence.invites import mint_invite


def _p() -> Persistence:
    return Persistence(":memory:", Path("/tmp/mj_admin_helpers"))


def test_list_invites_newest_first() -> None:
    p = _p()
    try:
        admin = create_account(
            p._conn, username="root", display_name="Root", kind="human",
            role="admin", password="pw-12345678",
        )
        mint_invite(p._conn, created_by=admin, created_at_ms=1000, code="inv_a")
        mint_invite(p._conn, created_by=admin, created_at_ms=2000, code="inv_b")
        codes = [iv.code for iv in p.list_invites()]
        assert codes == ["inv_b", "inv_a"]  # newest first
    finally:
        p.close()


def test_list_accounts_and_set_role() -> None:
    p = _p()
    try:
        create_account(
            p._conn, username="alice", display_name="Alice", kind="human",
            role="user", password="pw-12345678",
        )
        bob = create_account(
            p._conn, username="bob", display_name="Bob", kind="human",
            role="user", password="pw-12345678",
        )
        names = [a.username for a in p.list_accounts()]
        assert names == ["alice", "bob"]  # ordered by id

        p.set_account_role(bob, "admin")
        assert p.get_account_by_id(bob).role == "admin"  # type: ignore[union-attr]
    finally:
        p.close()


def test_set_account_role_rejects_garbage() -> None:
    p = _p()
    try:
        uid = create_account(
            p._conn, username="xavier", display_name="X", kind="human",
            role="user", password="pw-12345678",
        )
        with pytest.raises(ValueError):
            p.set_account_role(uid, "superuser")
    finally:
        p.close()
