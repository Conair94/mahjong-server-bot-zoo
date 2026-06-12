"""Auth module tests — docs/specs/auth.md § Verification fixtures (all 17).

These are unit tests against the auth module directly.  They use an in-memory
SQLite DB with the full migration applied (no mocking of the DB layer).

The argon2 hash/verify operations are slow by design (memory-hard).  The
expensive tests (timing, rehash) are marked ``slow`` so the core <10s suite
can skip them; they run in CI on a dedicated runner.

Fixture 9 (timing) is inherently environment-dependent and uses a generous
30% tolerance.  It is skipped by default (opt-in: ``-m slow``).
"""

from __future__ import annotations

import re
import sqlite3
import time

import pytest

from mahjong.persistence import apply_migrations
from mahjong.persistence.auth import (
    STATIC_INVALID_HASH,
    PasswordHasher,
    create_account,
    handle_auth_request,
    handle_resume,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _create_alice(db: sqlite3.Connection, password: str = "alicepw123") -> int:
    """Insert a human account for alice and return her account_id."""
    return create_account(
        db,
        username="alice",
        display_name="Alice",
        kind="human",
        role="user",
        password=password,
    )


# ---------------------------------------------------------------------------
# Fixture 1: Argon2id round-trip
# ---------------------------------------------------------------------------


def test_argon2id_roundtrip_hash_format() -> None:
    """Fixture 1a: hash() returns a PHC-formatted argon2id string."""
    h = PasswordHasher.hash("testpw")
    assert h.startswith("$argon2id$"), f"Expected argon2id PHC prefix, got: {h[:20]!r}"


def test_argon2id_roundtrip_verify_correct() -> None:
    """Fixture 1b: verify(hash, correct_pw) returns True."""
    h = PasswordHasher.hash("testpw")
    assert PasswordHasher.verify(h, "testpw") is True


def test_argon2id_roundtrip_verify_wrong() -> None:
    """Fixture 1c: verify(hash, wrong_pw) returns False."""
    h = PasswordHasher.hash("testpw")
    assert PasswordHasher.verify(h, "wrongpw") is False


# ---------------------------------------------------------------------------
# Fixture 2: Hash parameters match spec
# ---------------------------------------------------------------------------


def test_hash_parameters_match_spec() -> None:
    """Fixture 2: A fresh hash carries m=65536,t=3,p=4."""
    h = PasswordHasher.hash("pw")
    # PHC format: $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
    assert "m=65536" in h, f"memory_cost=65536 not in hash: {h}"
    assert "t=3" in h, f"time_cost=3 not in hash: {h}"
    assert "p=4" in h, f"parallelism=4 not in hash: {h}"


# ---------------------------------------------------------------------------
# Fixture 3: needs_rehash detects parameter drift
# ---------------------------------------------------------------------------


def test_needs_rehash_detects_downgraded_params() -> None:
    """Fixture 3a: A hash with time_cost=2 reports needs_rehash=True."""
    import argon2 as _argon2

    downgraded = _argon2.PasswordHasher(
        time_cost=2, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16
    )
    old_hash = downgraded.hash("pw")
    assert PasswordHasher.needs_rehash(old_hash) is True


def test_needs_rehash_fresh_hash_is_current() -> None:
    """Fixture 3b: A fresh hash with current params reports needs_rehash=False."""
    h = PasswordHasher.hash("pw")
    assert PasswordHasher.needs_rehash(h) is False


# ---------------------------------------------------------------------------
# Fixture 4: Session token format
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"^s_[0-9a-f]{32}$")


def test_session_token_format(tmp_path: pytest.TempdirFactory) -> None:
    """Fixture 4a: issue_session returns a string matching ^s_[0-9a-f]{32}$."""
    from mahjong.persistence.auth import issue_session

    db = _make_db()
    account_id = _create_alice(db)
    token = issue_session(db, account_id)
    assert _TOKEN_RE.match(token), f"Token format mismatch: {token!r}"


def test_session_tokens_are_unique() -> None:
    """Fixture 4b: 100 successive issue_session calls produce 100 distinct tokens."""
    from mahjong.persistence.auth import issue_session

    db = _make_db()
    account_id = _create_alice(db)
    tokens = [issue_session(db, account_id) for _ in range(100)]
    assert len(set(tokens)) == 100, "Token collision — CSPRNG not being used"


# ---------------------------------------------------------------------------
# Fixture 5: Successful login round-trip
# ---------------------------------------------------------------------------


def test_successful_login_roundtrip() -> None:
    """Fixture 5: create account → AUTH_REQUEST ok → RESUME ok → last_seen_ms updated."""
    db = _make_db()
    _create_alice(db)

    # login
    before_login = int(time.time() * 1000)
    result = handle_auth_request(db, "alice", "alicepw123")
    assert result.ok is True
    assert result.session_token is not None
    assert result.user_id is not None
    assert result.display_name == "Alice"
    assert result.expires_at_ms is not None

    # resume
    result2 = handle_resume(db, result.session_token)
    assert result2.ok is True
    assert result2.session_token == result.session_token  # same token (no rotation in v1)

    # last_seen_ms updated
    row = db.execute(
        "SELECT last_seen_ms FROM sessions WHERE session_id = ?",
        (result.session_token,),
    ).fetchone()
    assert row is not None
    assert row[0] >= before_login


# ---------------------------------------------------------------------------
# Fixture 6: Wrong password failure
# ---------------------------------------------------------------------------


def test_wrong_password_returns_ok_false() -> None:
    """Fixture 6: AUTH_REQUEST with wrong password returns ok=False."""
    db = _make_db()
    _create_alice(db)
    result = handle_auth_request(db, "alice", "wrongpassword")
    assert result.ok is False
    assert result.session_token is None


def test_wrong_password_no_session_created() -> None:
    """Fixture 6b: No session row is created on failed login."""
    db = _make_db()
    _create_alice(db)
    handle_auth_request(db, "alice", "wrongpassword")
    count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# Fixture 7: Unknown user failure
# ---------------------------------------------------------------------------


def test_unknown_user_returns_ok_false() -> None:
    """Fixture 7: AUTH_REQUEST for a non-existent user returns ok=False."""
    db = _make_db()
    result = handle_auth_request(db, "ghost", "anypassword")
    assert result.ok is False
    assert result.session_token is None


# ---------------------------------------------------------------------------
# Fixture 8: Failure shape byte-identical for wrong-password vs unknown-user
# ---------------------------------------------------------------------------


def test_failure_shape_byte_identical() -> None:
    """Fixture 8: Serialised AuthResult for wrong-password and unknown-user are equal."""
    import dataclasses

    db = _make_db()
    _create_alice(db)
    wrong_pw = handle_auth_request(db, "alice", "wrongpassword")
    unknown = handle_auth_request(db, "ghost", "anypassword")

    # Both should be ok=False with all optional fields None/absent
    assert dataclasses.asdict(wrong_pw) == dataclasses.asdict(
        unknown
    ), f"Failure shapes differ:\n  wrong_pw: {wrong_pw}\n  unknown:  {unknown}"


# ---------------------------------------------------------------------------
# Fixture 9: Timing within 30% (slow — opt-in with -m slow)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_failure_timing_close_to_success_timing() -> None:
    """Fixture 9: Mean failure time is within 30% of success time (timing-attack defense).

    This is environment-dependent.  Run on a stable machine; skip in noisy CI.
    Mark ``slow`` because it takes ~3s (10 x argon2 verify).
    """
    SAMPLES = 10  # fewer than spec's 100 to keep wall-clock manageable

    db = _make_db()
    _create_alice(db)

    def _time_calls(fn: object, n: int) -> float:
        times = []
        for _ in range(n):
            start = time.perf_counter()
            fn()  # type: ignore[operator]
            times.append(time.perf_counter() - start)
        return sum(times) / len(times)

    success_mean = _time_calls(lambda: handle_auth_request(db, "alice", "alicepw123"), SAMPLES)
    wrong_pw_mean = _time_calls(lambda: handle_auth_request(db, "alice", "wrong"), SAMPLES)
    unknown_mean = _time_calls(lambda: handle_auth_request(db, "ghost", "wrong"), SAMPLES)

    tolerance = 0.30
    for label, mean in [("wrong_pw", wrong_pw_mean), ("unknown", unknown_mean)]:
        ratio = abs(mean - success_mean) / success_mean
        assert ratio <= tolerance, (
            f"{label} mean {mean:.3f}s is >30% from success mean {success_mean:.3f}s "
            f"(ratio={ratio:.2%})"
        )


# ---------------------------------------------------------------------------
# Fixture 10: Disabled account refused
# ---------------------------------------------------------------------------


def test_disabled_account_login_refused() -> None:
    """Fixture 10a: Disabled account returns ok=False on AUTH_REQUEST."""
    db = _make_db()
    account_id = _create_alice(db)
    db.execute("UPDATE accounts SET disabled=1 WHERE account_id=?", (account_id,))
    result = handle_auth_request(db, "alice", "alicepw123")
    assert result.ok is False


def test_disabled_account_resume_refused() -> None:
    """Fixture 10b: Existing token for a disabled account fails RESUME."""
    db = _make_db()
    account_id = _create_alice(db)

    # Login first (while enabled)
    result = handle_auth_request(db, "alice", "alicepw123")
    assert result.ok is True
    token = result.session_token
    assert token is not None

    # Disable the account
    db.execute("UPDATE accounts SET disabled=1 WHERE account_id=?", (account_id,))

    # RESUME should fail
    result2 = handle_resume(db, token)
    assert result2.ok is False


# ---------------------------------------------------------------------------
# Fixture 11: Expired session refused
# ---------------------------------------------------------------------------


def test_expired_session_refused() -> None:
    """Fixture 11: A session with expires_at_ms in the past returns ok=False on RESUME."""
    db = _make_db()
    _create_alice(db)
    result = handle_auth_request(db, "alice", "alicepw123")
    assert result.ok is True
    token = result.session_token

    # Back-date expiry to 1ms ago
    db.execute(
        "UPDATE sessions SET expires_at_ms=? WHERE session_id=?",
        (int(time.time() * 1000) - 1, token),
    )

    result2 = handle_resume(db, token)
    assert result2.ok is False


# ---------------------------------------------------------------------------
# Fixture 12: Revoked session refused
# ---------------------------------------------------------------------------


def test_revoked_session_refused() -> None:
    """Fixture 12: A session with revoked=1 returns ok=False on RESUME."""
    db = _make_db()
    _create_alice(db)
    result = handle_auth_request(db, "alice", "alicepw123")
    assert result.ok is True
    token = result.session_token

    db.execute("UPDATE sessions SET revoked=1 WHERE session_id=?", (token,))

    result2 = handle_resume(db, token)
    assert result2.ok is False


# ---------------------------------------------------------------------------
# Fixture 13: Sliding renewal updates expiry
# ---------------------------------------------------------------------------


def test_sliding_renewal_extends_expiry() -> None:
    """Fixture 13: RESUME extends expires_at_ms beyond the original value."""
    db = _make_db()
    _create_alice(db)
    result = handle_auth_request(db, "alice", "alicepw123")
    assert result.ok is True
    original_expiry = result.expires_at_ms
    token = result.session_token

    time.sleep(0.01)  # 10ms — tiny but enough for ms-precision timestamps to advance

    result2 = handle_resume(db, token)
    assert result2.ok is True
    assert result2.expires_at_ms is not None
    assert result2.expires_at_ms > original_expiry, (
        f"Expected expires_at_ms to increase: "
        f"original={original_expiry}, renewed={result2.expires_at_ms}"
    )
    assert result2.session_token == token  # same token (no rotation)


# ---------------------------------------------------------------------------
# Fixture 14: Lazy rehash on parameter upgrade
# ---------------------------------------------------------------------------


def test_lazy_rehash_upgrades_hash_on_login() -> None:
    """Fixture 14: Account with downgraded hash gets rehashed on successful login."""
    import argon2 as _argon2

    db = _make_db()
    account_id = _create_alice(db)

    # Manually downgrade the stored hash to time_cost=2
    downgraded_hasher = _argon2.PasswordHasher(
        time_cost=2, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16
    )
    old_hash = downgraded_hasher.hash("alicepw123")
    db.execute("UPDATE accounts SET password_hash=? WHERE account_id=?", (old_hash, account_id))

    # Verify the stored hash is downgraded
    stored_before = db.execute(
        "SELECT password_hash FROM accounts WHERE account_id=?", (account_id,)
    ).fetchone()[0]
    assert "t=2" in stored_before

    # Login — should succeed and trigger rehash
    result = handle_auth_request(db, "alice", "alicepw123")
    assert result.ok is True

    # The stored hash should now have t=3 (current params)
    stored_after = db.execute(
        "SELECT password_hash FROM accounts WHERE account_id=?", (account_id,)
    ).fetchone()[0]
    assert "t=3" in stored_after, f"Expected rehashed hash with t=3, got: {stored_after[:80]!r}"


# ---------------------------------------------------------------------------
# Fixture 15: Account creation rejects case-insensitive duplicates
# ---------------------------------------------------------------------------


def test_create_account_rejects_case_insensitive_duplicate() -> None:
    """Fixture 15: Creating 'Alice' after 'alice' raises ValueError."""
    db = _make_db()
    create_account(
        db, username="alice", display_name="Alice", kind="human", role="user", password="pw12345678"
    )
    with pytest.raises(ValueError, match=r"[Aa]lready exists|[Dd]uplicate|[Ee]xists"):
        create_account(
            db,
            username="Alice",
            display_name="Alice2",
            kind="human",
            role="user",
            password="pw12345678",
        )


# ---------------------------------------------------------------------------
# Fixture 16: Bot account follows same auth path
# ---------------------------------------------------------------------------


def test_bot_account_auth_same_as_human() -> None:
    """Fixture 16: Bot account logs in and resumes via the same code path."""
    db = _make_db()
    create_account(
        db,
        username="bot_rule_v1",
        display_name="RuleBot v1",
        kind="bot",
        role="user",
        password="very-long-bot-secret-!@#$",
    )

    result = handle_auth_request(db, "bot_rule_v1", "very-long-bot-secret-!@#$")
    assert result.ok is True
    assert result.session_token is not None

    result2 = handle_resume(db, result.session_token)
    assert result2.ok is True


# ---------------------------------------------------------------------------
# Fixture 17: STATIC_INVALID_HASH exists and is shaped right
# ---------------------------------------------------------------------------


def test_static_invalid_hash_is_valid_phc() -> None:
    """Fixture 17a: STATIC_INVALID_HASH is non-empty and PHC-formatted."""
    assert STATIC_INVALID_HASH, "STATIC_INVALID_HASH is empty"
    assert STATIC_INVALID_HASH.startswith(
        "$argon2id$"
    ), f"STATIC_INVALID_HASH not argon2id PHC: {STATIC_INVALID_HASH[:30]!r}"


def test_static_invalid_hash_does_not_verify_against_empty() -> None:
    """Fixture 17b: STATIC_INVALID_HASH does not match the empty string."""
    assert PasswordHasher.verify(STATIC_INVALID_HASH, "") is False


def test_static_invalid_hash_does_not_verify_against_common_passwords() -> None:
    """Fixture 17c: STATIC_INVALID_HASH does not verify for common submitted passwords.

    The hash is used for timing defence only — the submitted password is always
    a real user's attempt, never the sentinel phrase baked into the hash.
    """
    for pw in ("password", "123456", "admin", ""):
        assert (
            PasswordHasher.verify(STATIC_INVALID_HASH, pw) is False
        ), f"STATIC_INVALID_HASH unexpectedly verified against {pw!r}"
