"""handle_register tests — docs/specs/public-deployment.md § 24.2 (step 2).

Covers the invite-gated registration flow that composes the step-1 invite
primitives with account creation inside one transaction:

  - Fixture 9:  fresh single-use invite + fresh username → ok, account created,
                session issued (resumable), used_count = 1.
  - Fixture 10: spent invite rejected.
  - Fixture 11: expired invite rejected.
  - Fixture 12: disabled invite rejected.
  - Fixture 13: duplicate (case-insensitive) username rejected, invite NOT consumed.
  - Fixture 15: display_name with HTML / control chars is sanitised before storage.

Plus username/password format validation and the generic-vs-specific error
messages (invite problems stay generic to avoid an invite-code oracle; a taken
username is surfaced specifically).
"""

from __future__ import annotations

import sqlite3

import pytest

from mahjong.persistence import apply_migrations
from mahjong.persistence.accounts import get_account_by_username
from mahjong.persistence.auth import (
    GENERIC_INVITE_MESSAGE,
    RegisterError,
    create_account,
    handle_register,
    handle_resume,
)
from mahjong.persistence.invites import get_invite, mint_invite, set_invite_disabled

NOW = 1_000_000


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _admin(conn: sqlite3.Connection) -> int:
    return create_account(
        conn,
        username="rootadmin",
        display_name="Root",
        kind="human",
        role="admin",
        password="adminpw123",
    )


def _account_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


# ---------------------------------------------------------------------------
# Fixture 9: happy path
# ---------------------------------------------------------------------------


def test_register_success_creates_account_and_session() -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)

    res = handle_register(
        conn,
        username="alice",
        password="alicepw123",
        display_name="Alice",
        invite_code=code,
        now_ms=NOW + 1,
    )

    assert res.ok is True
    assert res.session_token is not None and res.session_token.startswith("s_")
    assert res.display_name == "Alice"
    assert res.user_id is not None and res.user_id.startswith("u_")

    # Account really exists with the human/user defaults.
    acct = get_account_by_username(conn, "alice")
    assert acct is not None
    assert acct.kind == "human"
    assert acct.role == "user"

    # Invite was consumed exactly once.
    assert get_invite(conn, code).used_count == 1

    # Session is real — it resumes.
    assert handle_resume(conn, res.session_token).ok is True


# ---------------------------------------------------------------------------
# Format validation (reject before touching the DB / hashing the password)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("username", ["ab", "x" * 33, "bad name", "no!bang", ""])
def test_register_rejects_bad_username(username: str) -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)
    with pytest.raises(RegisterError):
        handle_register(
            conn,
            username=username,
            password="alicepw123",
            display_name="Alice",
            invite_code=code,
            now_ms=NOW + 1,
        )
    # No account, invite untouched.
    assert _account_count(conn) == 1  # just the admin
    assert get_invite(conn, code).used_count == 0


def test_register_rejects_short_password() -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)
    with pytest.raises(RegisterError):
        handle_register(
            conn,
            username="alice",
            password="short",
            display_name="Alice",
            invite_code=code,
            now_ms=NOW + 1,
        )
    assert get_account_by_username(conn, "alice") is None
    assert get_invite(conn, code).used_count == 0


# ---------------------------------------------------------------------------
# Invite-state rejections (fixtures 10, 11, 12) — all generic message
# ---------------------------------------------------------------------------


def test_register_unknown_invite_rejected_generic() -> None:
    conn = _db()
    _admin(conn)
    with pytest.raises(RegisterError) as exc:
        handle_register(
            conn,
            username="alice",
            password="alicepw123",
            display_name="Alice",
            invite_code="inv_nope",
            now_ms=NOW + 1,
        )
    assert exc.value.message == GENERIC_INVITE_MESSAGE
    assert get_account_by_username(conn, "alice") is None


def test_register_spent_invite_rejected() -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)
    handle_register(
        conn,
        username="alice",
        password="alicepw123",
        display_name="Alice",
        invite_code=code,
        now_ms=NOW + 1,
    )
    with pytest.raises(RegisterError) as exc:
        handle_register(
            conn,
            username="bob",
            password="bobpw12345",
            display_name="Bob",
            invite_code=code,
            now_ms=NOW + 2,
        )
    assert exc.value.message == GENERIC_INVITE_MESSAGE
    assert get_account_by_username(conn, "bob") is None
    assert get_invite(conn, code).used_count == 1  # unchanged


def test_register_expired_invite_rejected() -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(
        conn, created_by=admin, created_at_ms=NOW, expires_at_ms=NOW + 10
    )
    with pytest.raises(RegisterError) as exc:
        handle_register(
            conn,
            username="alice",
            password="alicepw123",
            display_name="Alice",
            invite_code=code,
            now_ms=NOW + 100,  # past expiry
        )
    assert exc.value.message == GENERIC_INVITE_MESSAGE
    assert get_account_by_username(conn, "alice") is None
    assert get_invite(conn, code).used_count == 0


def test_register_disabled_invite_rejected() -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)
    set_invite_disabled(conn, code, True)
    conn.commit()
    with pytest.raises(RegisterError):
        handle_register(
            conn,
            username="alice",
            password="alicepw123",
            display_name="Alice",
            invite_code=code,
            now_ms=NOW + 1,
        )
    assert get_account_by_username(conn, "alice") is None
    assert get_invite(conn, code).used_count == 0


# ---------------------------------------------------------------------------
# Fixture 13: duplicate username does NOT consume the invite
# ---------------------------------------------------------------------------


def test_register_duplicate_username_does_not_consume_invite() -> None:
    conn = _db()
    admin = _admin(conn)
    # Pre-existing lowercase "alice".
    create_account(
        conn,
        username="alice",
        display_name="Alice",
        kind="human",
        role="user",
        password="alicepw123",
    )
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)

    with pytest.raises(RegisterError) as exc:
        handle_register(
            conn,
            username="Alice",  # case-insensitive duplicate
            password="alicepw999",
            display_name="Alice2",
            invite_code=code,
            now_ms=NOW + 1,
        )
    assert exc.value.message == "username already taken"
    # The load-bearing assertion: invite is still pristine.
    assert get_invite(conn, code).used_count == 0
    assert _account_count(conn) == 2  # admin + original alice only


# ---------------------------------------------------------------------------
# Fixture 15: display_name sanitisation
# ---------------------------------------------------------------------------


def test_register_sanitises_display_name() -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)
    res = handle_register(
        conn,
        username="alice",
        password="alicepw123",
        display_name="<script>alert(1)</script>Bob",
        invite_code=code,
        now_ms=NOW + 1,
    )
    stored = get_account_by_username(conn, "alice").display_name
    assert "<" not in stored and ">" not in stored
    assert "(" not in stored and ")" not in stored
    assert "Bob" in stored
    assert res.display_name == stored  # response echoes what was stored


def test_register_empty_display_falls_back_to_username() -> None:
    conn = _db()
    admin = _admin(conn)
    code = mint_invite(conn, created_by=admin, created_at_ms=NOW)
    res = handle_register(
        conn,
        username="alice",
        password="alicepw123",
        display_name="@#$%^&*",  # everything stripped
        invite_code=code,
        now_ms=NOW + 1,
    )
    assert res.display_name == "alice"
