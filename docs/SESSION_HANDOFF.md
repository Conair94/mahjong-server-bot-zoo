# Session handoff — 2026-05-25 (end of Layer 8 Step 8.3)

Snapshot of implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 8 Steps 8.1, 8.2, and 8.3 are complete.**

| # | Step | Commit | Notes |
| --- | --- | --- | --- |
| KL-fix | dealer_seat through mgr.run_hand | `9f6ad07` | + hand_index_in_match; 2 new tests |
| **8.1** | **SQLite schema + migration runner** | **`40d645c`** | **17 tests; all 10 spec fixtures** |
| **8.2** | **Auth module (argon2id + sessions)** | **`bee3a5a`** | **24 tests; all 17 spec fixtures** |
| **8.3** | **Persistence API (Persistence class)** | **`bef3972`** | **13 tests; all 13 spec fixtures** |

**Verification at end of session:** ruff clean · mypy strict clean · **592 tests pass repo-wide** (579 prior + 13 new; 2 Linux-only + 1 slow deselected).

## Decisions reached this session

- **(52) `record_checksum` is nullable in the schema.** The `persistence-api.md` spec is correct: this field is NULL at `reserve_hand` time (HEADER write) and filled in by `finalize_hand` (FOOTER write). The migration and expected_schema.sql snapshot were both updated.
- **(53) Module-level SQL primitives, thin `Persistence` façade.** `accounts.py`, `hands.py`, `rebuild.py` hold the SQL; `Persistence.__init__.py` owns the connection and delegates. This makes each primitive testable without the façade, and the façade testable without mocking SQL.
- **(54) `auth.py` left untouched.** The 24 auth tests pass against the existing raw-SQL implementation. `accounts.py` provides the same operations through typed helpers for future callers (the `Persistence` class uses `accounts.py`; `auth.py` uses its own SQL). No duplication risk at this scale.
- **(55) Rebuild scans all lines for HAND_END.** The `rebuild_index_from_records` path parses HEADER (line 1), all middle events (for HAND_END terminal info), and FOOTER (last line). This is the only way to reproduce `terminal_kind`, `winner_seat`, `fan_total`, and `score_deltas` from the record file alone.
- **(56) Crash-truncated records (no FOOTER) rebuilt as `terminal_kind = 'ABORTED'`.** Consistent with the spec's "file's last event isn't a TERMINAL → ABORTED" rule.
- **(57) Keyset pagination in `find_hands_by_account` uses `started_at_ms <`.** Works correctly when timestamps are unique (which they always are in practice for per-hand records). Edge case of exact-millisecond ties is acceptable for v1.
- **(58) `open_db` now accepts `str | os.PathLike[str]`.** This enables `Persistence(":memory:", data_dir)` in tests. The `:memory:` string must be passed as a bare string (not a `Path` object), which callers naturally do.

## What this session built

### Step 8.3 — Persistence API

**[mahjong/persistence/models.py](../mahjong/persistence/models.py)** — new:
- `Account`, `SessionRow` — typed account + session rows
- `Participant`, `HandRow` — typed hand rows; `HandRow.participants` populated only by `get_hand`
- `IntegrityReport` — counts from `integrity_check` (pragma_ok, checked_db, ok_files, missing_files, checksum_mismatches, orphaned_files, in_progress_hands)
- `RebuildReport` — counts from rebuild (processed_files, inserted, updated, errors)

**[mahjong/persistence/accounts.py](../mahjong/persistence/accounts.py)** — new:
- `get_account_by_username`, `get_account_by_id`, `insert_account`, `update_account_login`, `set_account_disabled`
- `insert_session`, `get_session`, `renew_session`, `revoke_session`, `delete_expired_sessions`
- All take `sqlite3.Connection`; do NOT auto-commit (caller manages transaction boundaries)

**[mahjong/persistence/hands.py](../mahjong/persistence/hands.py)** — new:
- `reserve_hand` — atomic `BEGIN; INSERT hand_index; INSERT hand_participants × N; COMMIT`
- `finalize_hand` — atomic `BEGIN; UPDATE hand_index; UPDATE hand_participants × N; COMMIT`
- `get_hand` — fetches participants in a second query; returns populated `HandRow`
- `find_hands_by_account` — keyset pagination via `before_hand_id`
- `find_hands_by_match`, `find_recent_hands`, `find_in_progress_hands`

**[mahjong/persistence/rebuild.py](../mahjong/persistence/rebuild.py)** — new:
- `integrity_check` — PRAGMA + file existence + sha256 recompute + orphan walk
- `rebuild_index_from_records` — idempotent walk of `records/**/*.jsonl`, `INSERT OR REPLACE`

**[mahjong/persistence/__init__.py](../mahjong/persistence/__init__.py)** — updated:
- `Persistence` class: `__init__(db_path, data_dir)` calls `open_db` + `apply_migrations`
- All account/session/hand/rebuild methods delegate to module primitives; writes use `with self._conn:` for auto-commit

**[mahjong/persistence/db.py](../mahjong/persistence/db.py)** — updated:
- `open_db` now accepts `str | os.PathLike[str]` (previously `Path` only)

**[tests/persistence/test_persistence_api.py](../tests/persistence/test_persistence_api.py)** — new:
- 13 spec fixtures covering all of `persistence-api.md` plus `sqlite-schema.md` fixtures 11-12

## Known limitations carried forward

- **`next_hand_seq` in HAND_END is always `null`** (since Step 8.0). Low priority; client works.
- **No wire-protocol integration for AUTH_REQUEST / RESUME yet.** The auth module and the `Persistence` class are pure Python; not wired into the WebSocket server. That happens in Step 8.5.
- **No account CLI yet.** `python -m mahjong.cli.account create` is spec'd in auth.md but not implemented.
- **`Persistence` class not yet wired into `WebOrchestrator`.** The table manager still has no DB calls at HEADER/FOOTER write. Step 8.4 or 8.5 will add those hook points.
- All earlier known limitations still apply.

## What remains

**Remaining Layer 8 steps per CHECKLIST.md:**

- **Step 8.4 — Multi-table orchestrator.** One `WebSocketServer` hosts N tables; `LIST_TABLES` / `CREATE_TABLE` wire handlers.
- **Step 8.5 — Server lifecycle.** Graceful drain, startup sequence (DB open → migrations → auth check → serve), systemd unit, periodic session cleanup. This is also where `Persistence` gets wired into `WebOrchestrator` at HEADER/FOOTER hook points.
- **Step 8.6 — End-to-end S3 gate.** Byte-identical + auth + persistence fixture.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] `git log --oneline -5` — confirm `bef3972` (or later) at HEAD.
- [ ] `.venv/bin/python -m pytest -m "not slow" --tb=no -q` — confirm 592 passing, 3 skipped/deselected.
- [ ] Read [docs/specs/persistence-api.md](specs/persistence-api.md) § Wiring into the table manager before starting 8.4/8.5.
- [ ] Read [docs/specs/server-lifecycle.md](specs/server-lifecycle.md) for 8.5 context.
- [ ] Optionally `/extract-learnings` to consolidate memory before starting.
