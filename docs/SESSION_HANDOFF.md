# Session handoff — 2026-05-25 (end of Layer 8 Steps 8.1 + 8.2)

Snapshot of implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 8 Steps 8.1 and 8.2 are complete.** This session also closed the two
`dealer_seat` known-limitations from Step 8.0.

| # | Step | Commit | Notes |
| --- | --- | --- | --- |
| KL-fix | dealer_seat through mgr.run_hand | `9f6ad07` | + hand_index_in_match; 2 new tests |
| **8.1** | **SQLite schema + migration runner** | **`40d645c`** | **17 tests; all 10 spec fixtures** |
| **8.2** | **Auth module (argon2id + sessions)** | **`bee3a5a`** | **24 tests; all 17 spec fixtures** |

**Verification at end of session:** ruff clean · mypy strict clean · **579 tests pass repo-wide** (555 prior + 24 new; 2 Linux-only + 1 slow deselected).

## Decisions reached this session

- **(46) `dealer_seat` and `hand_index_in_match` thread through `mgr.run_hand`.** The game engine now actually uses the rotated dealer; HEADER winds rotate correctly. Backwards-compatible defaults (`dealer_seat=0, hand_index_in_match=0`).
- **(47) Migration runner owns `schema_version` tracking.** `up()` creates tables only; the runner does the `DELETE-then-INSERT` on `schema_version` inside a single transaction. This makes every migration atomic and `up()` idempotent.
- **(48) In-memory SQLite for all persistence unit tests.** Fast (<0.04s for 17 schema tests, <1.5s for 24 auth tests including real argon2 hashes). File-backed DB only for WAL tests (fixture 9).
- **(49) `STATIC_INVALID_HASH` computed at module import time.** One argon2id hash (~100ms) paid once per process. The timing-attack defence always uses this sentinel to equalise the failure path with the success path.
- **(50) `slow` pytest mark registered.** The timing test (fixture 9 of auth) is `@pytest.mark.slow`; core suite runs `pytest -m "not slow"`. Opt-in with `-m slow` on a stable CI runner.
- **(51) `next_hand_seq` in HAND_END is still `null`.** Deferred again — client transitions correctly on ATTACHED; fixing requires threading info from the orchestrator into the per-session send path. Low priority.

## What this session built

### Known-limitation fix — `dealer_seat` through `run_hand`

**[mahjong/table/manager.py](../mahjong/table/manager.py):**

- Added `dealer_seat: int = 0` and `hand_index_in_match: int = 0` kwargs.
- Passes `dealer_seat` to `initial_state()` so the engine actually uses the rotated dealer.
- HEADER `seats[i].wind` now uses `F{(i - dealer_seat) % 4 + 1}` (was always `F{i+1}`).
- HEADER `hand_index_in_match` now uses the parameter (was always `0`).

**[mahjong/web/server.py](../mahjong/web/server.py):**

- `_run_hand_loop` now passes `dealer_seat=self._dealer_seat, hand_index_in_match=self._hand_index`.

### Step 8.1 — SQLite schema + migration runner

**[mahjong/persistence/](../mahjong/persistence/)** — new package:

- `db.py`: `open_db(path)` — WAL, FK enforcement (`PRAGMA foreign_keys = ON`), 5000ms busy timeout, `sqlite3.Row` factory.
- `migrations/_0001_initial.py`: `up()` / `down()` for the full v1 schema: `schema_version`, `accounts`, `sessions`, `hand_index`, `hand_participants` + all 7 indexes.
- `migrations/__init__.py`: `apply_migrations(conn, target=None)` — reads `schema_version`, applies missing migrations atomically; idempotent on current DB. `rollback_migrations()` for testing.
- `__init__.py`: re-exports `open_db`, `apply_migrations`.

**[tests/persistence/expected_schema.sql](../tests/persistence/expected_schema.sql)** — snapshot file for fixture 10.

### Step 8.2 — Auth module

**[mahjong/persistence/auth.py](../mahjong/persistence/auth.py):**

- `PasswordHasher` static class: `hash()`, `verify()`, `needs_rehash()` wrapping argon2-cffi with spec params `(t=3, m=65536, p=4, hash_len=32, salt_len=16, type=ID)`.
- `STATIC_INVALID_HASH`: module-level sentinel for timing-attack defence.
- `AuthResult` frozen dataclass.
- `create_account(db, *, username, display_name, kind, role, password) -> int` — validates, case-insensitive duplicate check, hashes, INSERTs.
- `issue_session(db, account_id, user_agent=None) -> str` — `s_<32hex>` token from `secrets.token_hex(16)`.
- `handle_auth_request(db, username, password, user_agent=None) -> AuthResult` — full AUTH_REQUEST flow with timing defence on all failure paths and lazy rehash.
- `handle_resume(db, session_token) -> AuthResult` — validates token, sliding renewal, same token returned (no rotation in v1).

## Known limitations carried forward

- **`next_hand_seq` in HAND_END is always `null`** (since Step 8.0). Low priority; client works.
- **No wire-protocol integration for AUTH_REQUEST / RESUME yet.** The auth module is a pure Python layer; it's not wired into the WebSocket server. That happens in Step 8.5 (server lifecycle) or earlier if a forcing function appears.
- **No account CLI yet.** `python -m mahjong.cli.account create` is spec'd in auth.md but not implemented. The auth module's `create_account()` function is the core; CLI is a thin wrapper for later.
- All earlier known limitations still apply.

## What remains

**Remaining Layer 8 steps per CHECKLIST.md:**

- **Step 8.3 — Persistence API.** `reserve_hand`, `finalize_hand`, `find_hands_by_*`, integrity check, rebuild from records. These are the query helpers over the 8.1 schema. Spec: [persistence-api.md](specs/persistence-api.md). Fixtures 11-12 from sqlite-schema.md belong here.
- **Step 8.4 — Multi-table orchestrator.** One `WebSocketServer` hosts N tables; `LIST_TABLES` / `CREATE_TABLE` wire handlers.
- **Step 8.5 — Server lifecycle.** Graceful drain, startup sequence (DB open → migrations → auth check → serve), systemd unit, periodic session cleanup.
- **Step 8.6 — End-to-end S3 gate.** Byte-identical + auth + persistence fixture.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] `git log --oneline -5` — confirm `bee3a5a` (or later) at HEAD.
- [ ] `.venv/bin/python -m pytest -m "not slow" --tb=no -q` — confirm 579 passing, 3 skipped/deselected.
- [ ] Read [docs/specs/persistence-api.md](specs/persistence-api.md) before starting 8.3.
- [ ] Optionally `/extract-learnings` to consolidate memory.
