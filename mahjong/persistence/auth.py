"""Authentication module — docs/specs/auth.md.

Public API:
  ``PasswordHasher``          — thin wrapper around argon2-cffi with spec parameters.
  ``STATIC_INVALID_HASH``     — module-level precomputed hash for timing-attack defense.
  ``AuthResult``              — return type for handle_auth_request / handle_resume.
  ``create_account(...)``     — insert a new account (hashes password; rejects duplicate).
  ``issue_session(...)``      — generate a session token and INSERT it.
  ``handle_auth_request(...)``— AUTH_REQUEST login flow.
  ``handle_resume(...)``      — RESUME token-validation flow.

All DB operations take a ``sqlite3.Connection`` and are synchronous; the async
WebSocket handler wires them into asyncio via run_in_executor.  Foreign-key
enforcement must be ON on the connection (``open_db()`` guarantees this).
"""

from __future__ import annotations

import dataclasses
import re
import secrets
import sqlite3
import time

import argon2
import argon2.exceptions

from mahjong.persistence.accounts import insert_account
from mahjong.persistence.invites import redeem_invite

# ---------------------------------------------------------------------------
# PasswordHasher — argon2id with spec parameters
# ---------------------------------------------------------------------------

# Argon2id parameters per docs/specs/auth.md § Argon2id parameters.
_HASHER = argon2.PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=argon2.Type.ID,
)


class PasswordHasher:
    """Thin static wrapper around argon2-cffi with the spec's parameters.

    Never raises on a wrong password — returns False instead.  This
    centralises the constant-time guarantee so callers don't have to
    catch VerifyMismatchError themselves.
    """

    @staticmethod
    def hash(password: str) -> str:
        """Return a PHC-formatted argon2id hash of *password*."""
        return _HASHER.hash(password)

    @staticmethod
    def verify(hash_str: str, password: str) -> bool:
        """Constant-time verify.  Returns True iff *password* matches *hash_str*.

        Returns False on mismatch *and* on malformed hash_str — never raises
        in normal operation.
        """
        try:
            return _HASHER.verify(hash_str, password)
        except (
            argon2.exceptions.VerifyMismatchError,
            argon2.exceptions.VerificationError,
            argon2.exceptions.InvalidHashError,
        ):
            return False

    @staticmethod
    def needs_rehash(hash_str: str) -> bool:
        """True iff the hash was produced with parameters below the current spec."""
        return _HASHER.check_needs_rehash(hash_str)


# ---------------------------------------------------------------------------
# Timing-attack defense — precomputed "known-bad" hash
# ---------------------------------------------------------------------------

# This hash is computed at import time (once per process).  On an unknown-user
# or disabled-user login path we always run a full argon2 verify against this
# hash to equalise wall-clock time with the real-user path.
# The sentinel phrase is never a valid user password.
STATIC_INVALID_HASH: str = PasswordHasher.hash("_mahjong_server_static_invalid_hash_sentinel_")

# ---------------------------------------------------------------------------
# Session token generation
# ---------------------------------------------------------------------------

# 14 days in milliseconds.  Overridable at import via the calling layer when
# the server reads MAHJONG_SESSION_LIFETIME_HOURS.
SESSION_LIFETIME_MS: int = 14 * 24 * 60 * 60 * 1000


def _new_token() -> str:
    """Generate a fresh session token: ``s_`` + 32 lowercase hex chars (128 bits)."""
    return "s_" + secrets.token_hex(16)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# AuthResult dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AuthResult:
    """Return value for handle_auth_request and handle_resume.

    On failure: ok=False, all optional fields are None.
    On success: ok=True, session_token / expires_at_ms / user_id / display_name set.
    """

    ok: bool
    session_token: str | None = None
    expires_at_ms: int | None = None
    user_id: str | None = None
    display_name: str | None = None


_FAILURE = AuthResult(ok=False)

# ---------------------------------------------------------------------------
# Account creation
# ---------------------------------------------------------------------------


def create_account(
    db: sqlite3.Connection,
    *,
    username: str,
    display_name: str,
    kind: str,
    role: str,
    password: str,
) -> int:
    """Insert a new account into *db*.  Returns the new ``account_id``.

    Raises:
        ValueError: if a case-insensitive duplicate username already exists,
                    or if the password is shorter than 8 characters.
        sqlite3.IntegrityError: on constraint violations (bad kind, role, etc.).
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    # Case-insensitive duplicate check (auth.md § Account creation).
    existing = db.execute(
        "SELECT account_id FROM accounts WHERE lower(username) = lower(?)",
        (username,),
    ).fetchone()
    if existing is not None:
        raise ValueError(f"Username already exists (case-insensitive): {username!r}")

    pw_hash = PasswordHasher.hash(password)
    cursor = db.execute(
        """
        INSERT INTO accounts
            (username, display_name, kind, role, password_hash, disabled, created_at_ms)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (username, display_name, kind, role, pw_hash, _now_ms()),
    )
    db.commit()
    return int(cursor.lastrowid)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


def issue_session(
    db: sqlite3.Connection,
    account_id: int,
    user_agent: str | None = None,
) -> str:
    """INSERT a new session row and return the generated token.

    The token is NOT committed here so it can be part of a larger transaction
    in the caller.  In standalone use call ``db.commit()`` after.
    """
    token = _new_token()
    now = _now_ms()
    expires = now + SESSION_LIFETIME_MS
    db.execute(
        """
        INSERT INTO sessions
            (session_id, account_id, issued_at_ms, expires_at_ms, last_seen_ms, revoked, user_agent)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (token, account_id, now, expires, now, user_agent),
    )
    return token


# ---------------------------------------------------------------------------
# Auth request handler
# ---------------------------------------------------------------------------


def handle_auth_request(
    db: sqlite3.Connection,
    username: str,
    password: str,
    user_agent: str | None = None,
) -> AuthResult:
    """AUTH_REQUEST flow per docs/specs/auth.md § Wire flow.

    Always runs a full argon2 verify on every failure path to equalise
    wall-clock time with the success path (timing-attack defense).
    """
    row = db.execute(
        "SELECT account_id, password_hash, disabled, display_name FROM accounts "
        "WHERE lower(username) = lower(?)",
        (username,),
    ).fetchone()

    if row is None:
        # Timing defense: verify against a precomputed hash so the wall-clock
        # cost is identical to a real verify.  Result is always ignored.
        PasswordHasher.verify(STATIC_INVALID_HASH, password)
        return _FAILURE

    account_id, pw_hash, disabled, display_name = (row[0], row[1], row[2], row[3])

    if disabled:
        # Still run a full verify — disabled-account path must look like
        # wrong-password path to the attacker.
        PasswordHasher.verify(STATIC_INVALID_HASH, password)
        return _FAILURE

    if not PasswordHasher.verify(pw_hash, password):
        return _FAILURE

    # Success path.
    # Lazy rehash: if the stored hash was produced with older parameters,
    # rehash now so future logins use current cost.
    if PasswordHasher.needs_rehash(pw_hash):
        new_hash = PasswordHasher.hash(password)
        db.execute(
            "UPDATE accounts SET password_hash=? WHERE account_id=?",
            (new_hash, account_id),
        )

    token = issue_session(db, account_id, user_agent=user_agent)
    now = _now_ms()
    db.execute(
        "UPDATE accounts SET last_login_ms=? WHERE account_id=?",
        (now, account_id),
    )
    db.commit()

    expires_at_ms = now + SESSION_LIFETIME_MS
    return AuthResult(
        ok=True,
        session_token=token,
        expires_at_ms=expires_at_ms,
        user_id=f"u_{account_id}",
        display_name=display_name,
    )


# ---------------------------------------------------------------------------
# Resume handler
# ---------------------------------------------------------------------------


def handle_resume(
    db: sqlite3.Connection,
    session_token: str,
) -> AuthResult:
    """RESUME flow per docs/specs/auth.md § Wire flow.

    Validates the token, slides the expiry window, returns the same token.
    No timing-attack defense needed here — token entropy provides that.
    """
    row = db.execute(
        """
        SELECT s.account_id, s.expires_at_ms, s.revoked,
               a.disabled, a.display_name, a.username
        FROM sessions s
        JOIN accounts a ON a.account_id = s.account_id
        WHERE s.session_id = ?
        """,
        (session_token,),
    ).fetchone()

    now = _now_ms()
    if row is None:
        return _FAILURE

    account_id, expires_at_ms, revoked, disabled, display_name, _username = (
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
    )

    if revoked or expires_at_ms <= now or disabled:
        return _FAILURE

    # Sliding renewal: extend expiry.
    new_expiry = now + SESSION_LIFETIME_MS
    db.execute(
        "UPDATE sessions SET expires_at_ms=?, last_seen_ms=? WHERE session_id=?",
        (new_expiry, now, session_token),
    )
    db.commit()

    return AuthResult(
        ok=True,
        session_token=session_token,  # same token — no rotation in v1
        expires_at_ms=new_expiry,
        user_id=f"u_{account_id}",
        display_name=display_name,
    )


# ---------------------------------------------------------------------------
# Invite-gated registration  (docs/specs/public-deployment.md § 24.2)
# ---------------------------------------------------------------------------

# Username: 3-32 chars, letters/digits/_/- only (same rule as the account CLI).
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Display-name allow-list: visible ASCII names, no control chars or HTML metas.
_DISPLAY_DISALLOWED = re.compile(r"[^A-Za-z0-9 .,!?'\-]")
_DISPLAY_MAX = 32

# Invite failures share one generic message so the endpoint can't be used as an
# oracle to distinguish "no such code" / "spent" / "expired" / "disabled".
GENERIC_INVITE_MESSAGE = "invalid or used invite code"


class RegisterError(Exception):
    """Registration was rejected.  ``message`` is safe to show the user.

    Raised (not returned) so the success path keeps the same ``AuthResult``
    shape as ``handle_auth_request``; the orchestrator turns this into an
    ``ERROR { code: "register_rejected" }`` frame.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _sanitise_display_name(raw: str, *, fallback: str) -> str:
    """Strip control chars / HTML metas, collapse whitespace, cap length.

    Falls back to *fallback* (the username) if nothing survives — the accounts
    table requires a non-empty display_name.
    """
    cleaned = _DISPLAY_DISALLOWED.sub(" ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:_DISPLAY_MAX]
    return cleaned if cleaned else fallback


def handle_register(
    db: sqlite3.Connection,
    *,
    username: str,
    password: str,
    display_name: str,
    invite_code: str,
    user_agent: str | None = None,
    now_ms: int | None = None,
) -> AuthResult:
    """Invite-gated signup per docs/specs/public-deployment.md § 24.2.

    On success creates a ``human``/``user`` account, consumes one invite use,
    issues a session, and returns an ``AuthResult`` (auto-login).  On any
    rejection raises ``RegisterError`` with a user-safe message.

    The duplicate-username check, the invite redemption, and the account INSERT
    run in a single transaction, so a rejected signup (duplicate username, or a
    later failure) never consumes the invite.  The password is hashed *before*
    the transaction so the ~250ms argon2 cost doesn't hold the write lock.
    """
    now = now_ms if now_ms is not None else _now_ms()

    if not (3 <= len(username) <= 32) or _USERNAME_RE.match(username) is None:
        raise RegisterError("username must be 3-32 characters: letters, digits, _ or -")
    if len(password) < 8:
        raise RegisterError("password must be at least 8 characters")

    display = _sanitise_display_name(display_name, fallback=username)
    pw_hash = PasswordHasher.hash(password)

    with db:  # one transaction — dup-check + redeem + insert, all-or-nothing
        existing = db.execute(
            "SELECT 1 FROM accounts WHERE lower(username) = lower(?)",
            (username,),
        ).fetchone()
        if existing is not None:
            # Raised before redeem: the invite stays pristine (fixture 13).
            raise RegisterError("username already taken")

        if not redeem_invite(db, invite_code, now_ms=now):
            raise RegisterError(GENERIC_INVITE_MESSAGE)

        account_id = insert_account(
            db,
            username=username,
            display_name=display,
            kind="human",
            role="user",
            password_hash=pw_hash,
            created_at_ms=now,
        )
        token = issue_session(db, account_id, user_agent=user_agent)

    return AuthResult(
        ok=True,
        session_token=token,
        expires_at_ms=now + SESSION_LIFETIME_MS,
        user_id=f"u_{account_id}",
        display_name=display,
    )
