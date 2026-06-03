# Spec 14 — Authentication

How a user (human or bot) proves their identity to the server, and how an authenticated session persists across reconnects. Pins password hashing, session token issuance, validation rules, and the small administrative surface for account management.

Tier-2 spec. Single consumer (the server's auth module, wired into [wire-protocol.md](wire-protocol.md)'s `AUTH_REQUEST` / `RESUME` flow). Builds on [sqlite-schema.md](sqlite-schema.md) (`accounts`, `sessions`) and [wire-protocol.md](wire-protocol.md).

**Status:** draft, pre-S3 implementation. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md).

## Goals

- **Argon2id, not bcrypt.** Argon2 is the current standard for password hashing (winner of the 2015 Password Hashing Competition). Memory-hard hashing resists GPU/ASIC cracking in ways bcrypt and PBKDF2 don't. Specifically Argon2id — the hybrid variant that defends against side-channel attacks better than Argon2i alone and is the OWASP-recommended default.
- **Friends-and-family threat model.** No public-internet exposure ([server-plan.md § Hosting](../server-plan.md) puts the server on a Tailscale tailnet). **(Superseded for public deploys by [Spec 24 — public-deployment.md](public-deployment.md), which revises this threat model and resolves the deferred TLS / anti-abuse / wire-registration items below.)** The adversary we plan against is "someone got into the LAN and is trying to log in as Alice"; we are *not* planning against "an attacker has stolen the DB and is offline-cracking 10M passwords". That said, we use defensive hashing parameters anyway — they're cheap and the cost of getting them wrong is high if the threat model shifts.
- **No information leak on login failure.** Wrong username and wrong password produce byte-identical `AUTH_RESPONSE { ok: false }`. Constant-time comparison of password hashes; consistent timing across both failure paths.
- **Session tokens are opaque, long-lived, and rotatable.** Clients receive a `session_token` on login. The token is bound to one `account_id`; it expires (sliding renewal on every `RESUME`); it can be revoked. Token rotation on `RESUME` is supported but not enforced in v1 (rationale below).
- **Bots authenticate the same way humans do.** Per [s2-s3-plan.md §10.4](../s2-s3-plan.md). The bot-runner reads bot credentials from a server-side config file and submits them via `AUTH_REQUEST` before spawning the bot subprocess. The bot subprocess itself never authenticates; the bot-runner does, on its behalf.
- **No emails, no password reset flow, no MFA.** v1 friends-and-family. Admin resets passwords by direct DB edit.

## Non-goals

- **Not OAuth / SSO.** No external identity provider. Username + password only.
- **Not TLS configuration.** Transport security lives at the WebSocket layer ([server-lifecycle.md](server-lifecycle.md)). This spec assumes credentials are in transit over a trusted link (Tailscale, localhost, or TLS-fronted in a future deploy).
- **Not authorization beyond `accounts.role`.** Who can `CREATE_TABLE` / `CLOSE_TABLE` is gated on `accounts.role = 'admin'`. There's no per-table ACL, no resource-level permissions, no granular policy in v1.
- **Not anti-abuse.** Rate limiting on login attempts is in [wire-protocol.md § Rate limiting](wire-protocol.md) (the connection-wide rate limit covers login attempts at the same cap). A real auth-targeted rate limiter (e.g. "10 failures per IP per hour") is deferred to S7.
- **Not session management UI.** A user reviewing and revoking their own active sessions is a future feature ("you're logged in from 3 places — log out the others?"). The schema supports it; the wire protocol doesn't yet.
- **Not single-sign-on across services.** This server is one service.

## Threat model

What we defend against and how:

| Threat | Defense |
| --- | --- |
| Casual password-list testing | Argon2id with cost parameters that make each hash ~100ms. Even at the LAN level, a wrong password is annoyingly slow to test. |
| Stolen DB file → offline cracking | Same Argon2id cost makes 10M-password rainbow-table impractical. |
| Timing side-channel on login (does this username exist?) | Both failure paths run the full Argon2id verify against a *static known-bad hash* if the user doesn't exist. Constant-time string compare on the resulting flag. |
| Replay of a stolen `session_token` | Tokens are bound to an `account_id` and expire. Revocation is one UPDATE. (We don't bind to source IP because Tailscale users hop between devices — see "Alternatives considered".) |
| Cross-site request forgery | The WebSocket subprotocol check in [wire-protocol.md](wire-protocol.md) and the origin check in the WebSocket handshake protect against malicious browser tabs. Sessions in cookies would need CSRF tokens; we don't use cookies. |

What we explicitly *don't* defend against in v1:

- **Phishing.** Friends-and-family server: someone tricking Alice into giving them her password is a social problem, not a protocol one.
- **Compromised host.** If someone has root on the server, they can read everything. The argon2 hashes are useless if you have the running server's memory.
- **Account takeover via admin compromise.** Admin reset path is "edit the DB". An attacker who can edit the DB has already won.

## Argon2id parameters

```python
# argon2-cffi PasswordHasher arguments
argon2.PasswordHasher(
    time_cost=3,          # iterations
    memory_cost=65536,    # 64 MiB
    parallelism=4,        # threads
    hash_len=32,          # output bytes
    salt_len=16,          # salt bytes
    type=argon2.Type.ID,  # Argon2id
)
```

Rationale (OWASP Argon2 cheat-sheet, December 2023):

- **`memory_cost = 64 MiB`** is OWASP's middle recommendation. 19 MiB is the floor; 1 GiB is overkill for our threat model. 64 MiB keeps each hash at ~100ms on a modern laptop and ~250ms on a Raspberry Pi 5 (which is our deployment target per [project_hosting_target.md](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_hosting_target.md)).
- **`time_cost = 3`** combined with the above memory cost meets OWASP's "must take at least 0.5s on attacker hardware" rule of thumb when the attacker is using consumer GPUs.
- **`parallelism = 4`** matches typical server core counts; the verifier uses up to 4 threads per hash but they're short-lived.
- **`hash_len = 32`** — 256-bit output is plenty.
- **`salt_len = 16`** — 128-bit salt; argon2-cffi generates this per-hash.

The argon2 hash string is the full PHC-formatted output (`$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>`). Stored as-is in `accounts.password_hash`. Verifying a password re-derives the hash and compares.

### Parameter migration

Argon2 hashes carry their cost parameters inline. If we raise the cost in a future migration, existing hashes don't break — they verify fine, just at the old cost. On successful login, the verifier checks if the hash needs rehashing (`PasswordHasher.check_needs_rehash`) and, if so, rehashes with current parameters and updates the row. This is the standard "lazy upgrade" pattern.

## Password hashing API

```python
class PasswordHasher:

    def hash(password: str) -> str:
        """Return a PHC-formatted argon2id hash. Used on account creation."""
        ...

    def verify(hash_str: str, password: str) -> bool:
        """Constant-time verify. Returns True iff password matches hash.
        Never raises on a wrong password — only on malformed hash_str."""
        ...

    def needs_rehash(hash_str: str) -> bool:
        """Compare the parameters in hash_str against current; True iff outdated."""
        ...
```

Implementation note: `argon2-cffi`'s `PasswordHasher.verify` raises `VerifyMismatchError` on a wrong password. We wrap it to return a bool — a wrong-password raise would leak via try/except timing if any caller forgot to handle it carefully. Wrapping centralises the constant-time guarantee.

### The static known-bad hash

For the "username doesn't exist" timing-attack defense, we keep a single precomputed argon2id hash in module-level state and verify against it whenever the username lookup fails:

```python
STATIC_INVALID_HASH = PasswordHasher.hash("never-a-real-password")  # computed at import time
```

When `AUTH_REQUEST` arrives for unknown user:

```python
verify(STATIC_INVALID_HASH, submitted_password)  # ignore result; ensure consistent timing
return AuthResponse(ok=False)
```

This costs one full argon2 verify (~100ms) per unknown-user attempt. Same cost as the real-user path. The wall-clock observed by the attacker is identical (modulo network jitter, which the LAN dominates anyway).

## Session token format

```text
session_token := "s_" + 32 hex characters
                       (128 bits of entropy from os.urandom)
```

- Prefix `s_` is purely for human-readability when debugging. Tools that store tokens shouldn't rely on it; the auth path doesn't strip or verify the prefix beyond a length check.
- 128 bits is enough that even at 1 trillion guesses per second, finding a single valid token takes longer than the heat death of the universe. (In practice the attacker is rate-limited by the wire-protocol; the bit count is just defense in depth.)
- Generated via `secrets.token_hex(16)` and prefixed at the wrapper layer. `secrets` uses the OS CSPRNG; no seeded RNG, no determinism for tokens.

Tokens are opaque. Clients store them in OS keyring (TUI) or wherever future clients prefer; they're never inspected.

## Session lifecycle

### Issue

`AUTH_REQUEST` with valid credentials → server:

1. Generates a token.
2. INSERTs `sessions` row: `(session_id, account_id, issued_at_ms, expires_at_ms = now + SESSION_LIFETIME_MS, last_seen_ms = now, revoked = 0, user_agent)`.
3. Returns `AUTH_RESPONSE { ok: true, session_token, expires_at_ms, user_id, display_name }`.

`SESSION_LIFETIME_MS` defaults to 14 days (`MAHJONG_SESSION_LIFETIME_HOURS=336`).

### Validate

`RESUME` with a `session_token` → server:

1. SELECT row by `session_id = ?`.
2. Reject if not found, `revoked = 1`, `expires_at_ms <= now`, or the referenced account is `disabled = 1`. All rejections return identical `AUTH_RESPONSE { ok: false }` (no leak of which reason).
3. Accept: UPDATE `last_seen_ms = now`, `expires_at_ms = now + SESSION_LIFETIME_MS` (sliding renewal). Return `AUTH_RESPONSE { ok: true, ... }` with the *same* token (not rotated; see below).

### Sliding renewal

Each successful `RESUME` extends `expires_at_ms` by `SESSION_LIFETIME_MS`. A user who plays every few days keeps their session indefinitely; a user who goes a month without playing has to log in again.

Renewal updates `last_seen_ms` on every `RESUME`. A future "your sessions" UI would render this.

### Token rotation

v1 does *not* rotate the token on `RESUME`. The same token works for the lifetime of the session.

Rationale: rotation defends against token theft (an attacker who grabbed an old token loses access once you next `RESUME`), but it also breaks "I have two TUI tabs open and I just reconnected from one" — the other tab's token is now stale. At our friends-and-family scale the theft scenario is far-fetched (the wire is on Tailscale) and the multi-client scenario is real.

Token rotation is an additive change: a future migration could add `rotation_required` to the schema and the wire protocol could carry a rotated token on `AUTH_RESPONSE`. Not done in v1.

### Revoke

Three paths:

1. **Explicit logout.** Client sends `AUTH_REQUEST { kind: "LOGOUT" }` (a new client-to-server message? — no, we don't have one; logout in v1 is implemented as "close the WebSocket and forget the token client-side"). The server-side row stays valid until expiry. This is fine: a "live" token whose client has forgotten it is harmless.

   *Defer:* a real `LOGOUT` wire message that UPDATEs `revoked = 1` server-side is additive and can land in S3.1 if a real need surfaces.

2. **Admin revoke.** Admin DELETEs or UPDATEs the row by direct DB edit (or via a future admin CLI).

3. **Account disable.** Setting `accounts.disabled = 1` doesn't touch the sessions table directly, but every subsequent `RESUME` fails (the join with accounts filters disabled accounts). This is the soft-revoke-all path.

### Expiry cleanup

A scheduled task DELETEs expired sessions nightly:

```sql
DELETE FROM sessions
 WHERE expires_at_ms < ?    -- now
    OR revoked = 1;
```

This keeps the `sessions` table from growing without bound. Not load-bearing for correctness (the validation path filters anyway); purely housekeeping.

Implemented in [server-lifecycle.md § Periodic tasks](server-lifecycle.md). v1 runs once at startup and on a 24-hour timer; no separate cron.

## Account creation

Two paths in v1:

### CLI: `python -m mahjong.cli.account create`

```text
$ python -m mahjong.cli.account create \
    --username alice \
    --display-name "Alice" \
    --kind human \
    --role user
Password: <prompted; not echoed>
Confirm:  <prompted; not echoed>
Account u_alice created.
```

The CLI:

1. Validates username (`3-32 chars`, regex `^[a-zA-Z0-9_-]+$`).
2. Checks for case-insensitive duplicate (`SELECT account_id FROM accounts WHERE lower(username) = lower(?)`).
3. Prompts for password; validates (`>= 8 chars`, no other constraints in v1).
4. Hashes via `PasswordHasher.hash`.
5. INSERTs the row with `kind`, `role`, `disabled = 0`.
6. Prints the resulting `account_id` (`u_<account_id>` is the canonical form embedded in records).

Future work (S3.1 or later): a wire-protocol `CREATE_ACCOUNT` message that admins can call. Not in v1; CLI is enough.

### Bot account creation

Same CLI but `--kind bot`. The bot-runner's config file references the bot account's username + password:

```toml
# /etc/mahjong/bot-credentials.toml
[bots.b_rule_v1]
username = "bot_rule_v1"
password = "very-long-server-secret-string"
```

The bot-runner reads this file at startup (mode 0600, owner=mahjong-server-user) and uses the credentials to issue `AUTH_REQUEST` over an internal WebSocket *before* spawning the bot subprocess. The bot subprocess itself doesn't know the credentials.

This is uniform-with-humans on the wire and clean-ops at the file system: rotating a bot's password is editing the TOML and restarting the bot-runner.

## Wire flow

The wire-protocol surface of auth (already pinned in [wire-protocol.md](wire-protocol.md)):

- `AUTH_REQUEST { username, password }` — initial login.
- `RESUME { session_token }` — re-establish.
- `AUTH_RESPONSE { ok: bool, [user_id, display_name, session_token, expires_at_ms] }` — both responses.

Server validation flow on `AUTH_REQUEST`:

```python
async def handle_auth_request(req: AuthRequest) -> AuthResponse:
    row = db.fetchone(
        "SELECT account_id, password_hash, disabled FROM accounts "
        "WHERE lower(username) = lower(?)",
        (req.username,),
    )
    if row is None:
        password_hasher.verify(STATIC_INVALID_HASH, req.password)  # timing defense
        return AuthResponse(ok=False)
    if row.disabled:
        password_hasher.verify(STATIC_INVALID_HASH, req.password)  # timing defense
        return AuthResponse(ok=False)
    if not password_hasher.verify(row.password_hash, req.password):
        return AuthResponse(ok=False)

    # success path
    if password_hasher.needs_rehash(row.password_hash):
        new_hash = password_hasher.hash(req.password)
        db.execute("UPDATE accounts SET password_hash = ? WHERE account_id = ?",
                   (new_hash, row.account_id))

    token = issue_session(row.account_id, user_agent=req.user_agent)
    db.execute("UPDATE accounts SET last_login_ms = ? WHERE account_id = ?",
               (now_ms(), row.account_id))
    return AuthResponse(ok=True, session_token=token, ...)
```

The "always run an argon2 verify on the failure path" pattern is the timing-attack defense. The `disabled` check runs the static verify *before* returning — same wall-clock cost as a real failure.

Server validation flow on `RESUME`:

```python
async def handle_resume(req: Resume) -> AuthResponse:
    row = db.fetchone(
        "SELECT s.account_id, s.expires_at_ms, s.revoked, a.disabled, a.display_name, a.username "
        "FROM sessions s JOIN accounts a ON a.account_id = s.account_id "
        "WHERE s.session_id = ?",
        (req.session_token,),
    )
    if row is None or row.revoked or row.expires_at_ms <= now_ms() or row.disabled:
        return AuthResponse(ok=False)

    new_expiry = now_ms() + SESSION_LIFETIME_MS
    db.execute("UPDATE sessions SET expires_at_ms = ?, last_seen_ms = ? "
               "WHERE session_id = ?",
               (new_expiry, now_ms(), req.session_token))
    return AuthResponse(ok=True, session_token=req.session_token, expires_at_ms=new_expiry, ...)
```

No timing-attack defense on `RESUME` — the existence of a session token isn't a secret worth protecting (the attacker who could enumerate tokens has bigger problems; tokens have 128 bits of entropy).

## Alternatives considered

- **bcrypt.** Worked fine for two decades; still ubiquitous. Rejected for Argon2id because Argon2 is the PHC winner and the OWASP-current default. The migration cost is zero (we're greenfielding); use the current standard.
- **PBKDF2.** Standardised, FIPS-compliant. Rejected: not memory-hard; vulnerable to GPU cracking far more than argon2 is. We don't need FIPS.
- **scrypt.** Memory-hard like argon2, predates it. Rejected for argon2 on the same "current standard" grounds.
- **Token rotation on every `RESUME`.** Better theft-defense, breaks multi-tab. Deferred to a future spec when a threat surfaces.
- **Bind sessions to source IP.** "Token only valid from the IP it was issued to." Rejected: Tailscale users move between Wi-Fi and cellular, devices change IPs, the user experience suffers. The token's 128-bit entropy is the binding.
- **JWT instead of opaque tokens in SQLite.** Standard, stateless. Rejected: stateless auth means revocation requires either a blacklist (defeating the point) or short token lifetimes (forcing constant re-login). Our scale doesn't need stateless; SQLite handles 10K rows trivially.
- **HTTP Basic Auth on the WebSocket upgrade.** Skips a roundtrip. Rejected: the wire-protocol's `AUTH_REQUEST` happens after `HELLO`, which lets us version-negotiate before authenticating. Basic Auth at upgrade time would also embed credentials in browser HTTP logs for a future web client — worse hygiene.
- **No `static_invalid_hash` timing defense; just return `ok: false` faster.** Rejected: this is a textbook timing-attack mitigation and costs one argon2 verify on the slow path. Worth it.
- **Argon2 cost parameters tuned per-host.** Some servers run on a Raspberry Pi 5; a 64MiB / 3-iteration verify on the Pi is ~250ms vs ~100ms on a laptop. Acceptable for v1 (login is rare). If a busy table sees noticeable login lag in deployment, tune. Not pre-tuned.
- **MFA via TOTP.** Rejected: friends-and-family scale; the user count is too small to justify the UX cost. A future spec can add it as additive.

## Verification fixtures

Acceptance criteria for impl step 8.2 (auth module).

1. **Argon2id round-trip.** `hash("pw")` produces a PHC-formatted string starting with `$argon2id$`; `verify(hash, "pw")` returns True; `verify(hash, "pw2")` returns False.

2. **Hash parameters match spec.** Parsing a fresh hash, the parameters are `m=65536,t=3,p=4`.

3. **`needs_rehash` detects parameter drift.** A hash produced with `time_cost=2` reports `needs_rehash=True` against current settings; a fresh hash reports False.

4. **Session token format.** `issue_session(...)` returns a string matching `^s_[0-9a-f]{32}$`; 100 successive calls produce 100 distinct tokens.

5. **Successful login round-trip.** Create account `alice`/`pw`; `handle_auth_request("alice", "pw")` returns `ok=True` with a token; `handle_resume(token)` returns `ok=True` with the same token; `last_seen_ms` updated.

6. **Wrong password failure.** `handle_auth_request("alice", "wrongpw")` returns `ok=False`. No session row created.

7. **Unknown user failure.** `handle_auth_request("ghost", "pw")` returns `ok=False`.

8. **Failure shape byte-identical for wrong-password vs unknown-user.** Serialised `AuthResponse` for the two failure cases is byte-equal.

9. **Failure timing within 30% of success timing.** Run 100 wrong-password attempts and 100 unknown-user attempts; mean wall-clock is within 30% of a successful-login mean (loose bound; tightened in CI on a perf-controlled runner). The exact threshold is a knob; the constraint is "no obvious timing leak".

10. **Disabled account refused.** Create account, `UPDATE accounts SET disabled=1`, attempt login: `ok=False`. Existing tokens for that account fail `RESUME`.

11. **Expired session refused.** Issue token with `expires_at_ms = now - 1`; `handle_resume(token)` returns `ok=False`.

12. **Revoked session refused.** Issue token, UPDATE `revoked=1`, `handle_resume` returns `ok=False`.

13. **Sliding renewal updates expiry.** Issue token, wait 100ms, RESUME; new `expires_at_ms > original`; same token returned.

14. **Lazy rehash on parameter upgrade.** Create account with downgraded params; login succeeds; the row's `password_hash` is updated to current params.

15. **Account creation rejects duplicates case-insensitively.** Create `alice`; attempt to create `Alice` → rejected.

16. **Bot account follows same path.** Create account with `kind='bot'`; login via `AUTH_REQUEST` succeeds; session row created; subsequent `RESUME` works.

17. **Static-invalid-hash exists and is shaped right.** `STATIC_INVALID_HASH` is non-empty, PHC-formatted, doesn't match `verify(... , "")` or any plausible password (we just check it parses + verifies-against-mismatch).

Fixture 8 + 9 are the load-bearing ones for the timing-attack defense; fixture 5 is the load-bearing happy-path gate.

## Open questions

None at v1. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md). Future considerations (token rotation, real `LOGOUT` wire message, account-management UI, MFA) become additive specs.
