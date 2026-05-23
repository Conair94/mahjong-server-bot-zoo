# Spec 13 — SQLite schema

The persistent state of the server. Three tables of actual state (`accounts`, `sessions`, `hand_index`), one normalised join (`hand_participants`), one bookkeeping row (`schema_version`). Everything else is on disk as JSONL record files; SQLite indexes them.

Tier-2 spec. Single consumer (the server process). Edits here are low blast radius compared to a wire-protocol change, but the migration story makes the *schema's evolution* the load-bearing concern, not the schema itself.

Builds on [record-format.md](record-format.md) (the `hand_id` UUIDv7 + record file path is locked; this schema indexes them). Consumed by [auth.md](auth.md) (reads `accounts` + writes `sessions`), [persistence-api.md](persistence-api.md) (writes `hand_index` + `hand_participants` on hand end; queries them on demand), and [server-lifecycle.md](server-lifecycle.md) (opens DB at startup, closes cleanly at shutdown).

**Status:** draft, pre-S3 implementation. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md).

## Goals

- **SQLite is fine.** A single file, ACID, fast enough for tens of users and ~handful of concurrent tables. Per [server-plan.md § Tech stack](../server-plan.md): "use SQLite until you have a real reason not to."
- **Schema is small, indexes are explicit, queries are obvious.** A new developer reading this file should be able to predict what each query does and what plan SQLite picks. No surprise N+1, no surprise full-table-scan on a billed-by-the-query backend (we're not, but the discipline still pays).
- **Migrations are hand-rolled and tested both ways.** Per [s2-s3-plan.md §10.2](../s2-s3-plan.md): no Alembic. A `schema_version` table + a numbered list of migration scripts. Applying them on a fresh DB and on the previous version's DB are both CI-gated.
- **The record file is the source of truth for the *contents* of a hand. SQLite is the source of truth for *finding* a hand and *who played it*.** If the two ever disagree, the record file wins. The index is rebuildable from the record corpus (a recovery operation; see [persistence-api.md](persistence-api.md)).
- **Foreign keys enforced.** `PRAGMA foreign_keys = ON` on every connection. SQLite defaults this off; we explicitly turn it on. Cascade behavior is documented per relation.
- **All timestamps are Unix epoch milliseconds.** Integer column, name suffix `_ms`. No DATE/DATETIME types, no TEXT timestamps, no timezones embedded. The record's `ts` field is ISO-8601 (per [record-format.md](record-format.md)) — that's the record's contract; the SQLite index uses ms for cheap sorting.

## Non-goals

- **Not a query layer.** This spec defines the *schema*. The Python-side query helpers — `find_hands_by_account(account_id)`, `mark_hand_complete(hand_id, terminal)`, etc. — live in [persistence-api.md](persistence-api.md).
- **Not a config store.** Server configuration is environment variables ([server-lifecycle.md](server-lifecycle.md)). No `settings` table.
- **Not a session-state store for in-progress hands.** A live hand's mutable state lives in the table manager (in-memory) and the record file (on disk). SQLite gets a row only when the hand ends.
- **Not a chat / message log.** Chat is deferred ([server-plan.md open questions](../server-plan.md)); when it lands, it gets its own tables in a future migration.
- **Not an audit log.** Login attempts, failed actions, etc. go to journald via stdout structured logging, not SQLite. Searchable via `journalctl`.

## Database file and connection

- **File path:** `$MAHJONG_DATA_DIR/mahjong.db`. The same `$MAHJONG_DATA_DIR` tree also contains `records/`. Backing up the whole tree backs up everything ([server-plan.md § Persistence](../server-plan.md)).
- **WAL mode.** Every connection runs `PRAGMA journal_mode = WAL` once at startup. WAL gives concurrent readers + a single writer without blocking, which matches our access pattern (the server is the only writer; spectator/replay code may read while a hand is being written).
- **Synchronous = NORMAL.** Default for WAL. Tradeoff: a power-loss-during-fsync can lose the last few commits, but the on-disk record file is the durable source of truth — index rows can be rebuilt.
- **Foreign keys on.** `PRAGMA foreign_keys = ON` per connection (must be enabled per-connection in SQLite — it's not a database-wide setting).
- **One connection per server process.** SQLite supports many connections but we don't need them. Single connection + WAL is enough; threading concerns don't arise because the server is one asyncio loop.
- **Busy timeout 5s.** `PRAGMA busy_timeout = 5000`. If WAL mode ever blocks (extremely rare for our access pattern), we retry briefly before raising.

## Tables

### `schema_version`

Tracks which migration version the DB is at. Exactly one row at any time.

```sql
CREATE TABLE schema_version (
    version           INTEGER NOT NULL PRIMARY KEY CHECK (version >= 0),
    applied_at_ms     INTEGER NOT NULL,
    applied_by        TEXT NOT NULL
);
```

- `version`: integer; matches a numbered migration script under `mahjong/persistence/migrations/`.
- `applied_at_ms`: when this version was applied to *this* DB.
- `applied_by`: server build identifier (e.g. `"mahjong-server-0.1.0/abc123"`). Diagnostic; helps when reading an old DB.

The PRIMARY KEY constraint *alone* doesn't guarantee single-row; the migration runner enforces "delete-then-insert" semantics on every step (see "Migrations" below).

### `accounts`

One row per user (human or bot).

```sql
CREATE TABLE accounts (
    account_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT NOT NULL UNIQUE
                          CHECK (length(username) BETWEEN 3 AND 32),
    display_name      TEXT NOT NULL
                          CHECK (length(display_name) BETWEEN 1 AND 64),
    kind              TEXT NOT NULL CHECK (kind IN ('human', 'bot')),
    role              TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    password_hash     TEXT NOT NULL,                  -- argon2 hash; see auth.md
    disabled          INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    created_at_ms     INTEGER NOT NULL,
    last_login_ms     INTEGER
);

CREATE INDEX accounts_username_lower
    ON accounts(lower(username));
```

- `username` is stored as-entered but looked up case-insensitively (the index supports `WHERE lower(username) = lower(?)`). Display name and login name are separate so a user can change display without breaking auth.
- `kind = 'bot'` accounts are real accounts with real argon2 hashes; per [s2-s3-plan.md §10.4](../s2-s3-plan.md) bots authenticate the same way humans do.
- `role` is `'user'` by default; `'admin'` is hand-set in the DB (no admin-promotion UI in v1) and gates `CREATE_TABLE` / `CLOSE_TABLE` in [wire-protocol.md](wire-protocol.md).
- `disabled` flags an account that exists but can no longer log in (account closure, banned bot). Auth refuses without distinguishing "disabled" from "wrong password" externally.
- `password_hash` is NOT NULL. Empty password is not a valid state; bots must have a real hash (often a long random secret that lives in a server-side config file the bot-runner reads).

There is no `email` column in v1. Per [server-plan.md § Auth](../server-plan.md), there's no email verification or password reset flow; admin (the human running the server) resets passwords by direct DB edit.

### `sessions`

One row per issued session token. A user may have multiple concurrent sessions (laptop + phone).

```sql
CREATE TABLE sessions (
    session_id        TEXT PRIMARY KEY,             -- opaque 32-byte token, hex-encoded
    account_id        INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    issued_at_ms      INTEGER NOT NULL,
    expires_at_ms     INTEGER NOT NULL,
    last_seen_ms      INTEGER NOT NULL,
    revoked           INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1)),
    user_agent        TEXT
);

CREATE INDEX sessions_account_active
    ON sessions(account_id, revoked, expires_at_ms);
CREATE INDEX sessions_expires
    ON sessions(expires_at_ms);
```

- `session_id` is the opaque token clients present on `RESUME`. Generation, lifetime, and rotation are in [auth.md](auth.md); this schema just stores it.
- `ON DELETE CASCADE` on `account_id`: deleting an account hard-deletes its sessions. Account *deletion* is admin-only and rare; *disabling* (via `accounts.disabled = 1`) is the common case and leaves sessions intact (auth refuses them on validation).
- `last_seen_ms` is updated on every successful `RESUME` (sliding renewal — see [auth.md](auth.md)). Reads are cheap; writes are batched.
- `sessions_account_active` index supports the common "find this user's live sessions" query.
- `sessions_expires` index supports the periodic cleanup of expired rows (a maintenance job that DELETEs `WHERE expires_at_ms < now() AND revoked = 0`, run nightly).

`user_agent` carries the `client_id` from the wire-protocol `HELLO` message. Diagnostic; helps users review their own sessions ("you're logged in from 'mahjong-tui-0.1.0' and 'mahjong-tui-0.1.0' — log out the other one?").

### `hand_index`

One row per completed hand. The pointer into the record-file corpus.

```sql
CREATE TABLE hand_index (
    hand_id                 TEXT PRIMARY KEY,             -- UUIDv7 from record-format.md
    match_id                TEXT,                          -- UUIDv7 or NULL for standalone
    hand_index_in_match     INTEGER NOT NULL DEFAULT 0,
    ruleset_id              TEXT NOT NULL,                 -- e.g. "mcr-2006"
    ruleset_config_hash     TEXT NOT NULL,                 -- per determinism.md
    started_at_ms           INTEGER NOT NULL,
    ended_at_ms             INTEGER,                       -- NULL while in progress
    terminal_kind           TEXT CHECK (terminal_kind IN ('HU', 'EXHAUSTIVE_DRAW', 'ABORTED', NULL)),
    winner_seat             INTEGER CHECK (winner_seat BETWEEN 0 AND 3),
    fan_total               INTEGER,
    master_seed             TEXT NOT NULL,                 -- the engine seed (hex string)
    record_path             TEXT NOT NULL UNIQUE,          -- relative to $MAHJONG_DATA_DIR
    record_checksum         TEXT NOT NULL,                 -- matches the FOOTER checksum
    server_version          TEXT NOT NULL,                 -- HEADER.server.version
    source                  TEXT NOT NULL DEFAULT 'live'
                                CHECK (source IN ('live', 'selfplay', 'replay-import'))
);

CREATE INDEX hand_index_started
    ON hand_index(started_at_ms DESC);
CREATE INDEX hand_index_match
    ON hand_index(match_id, hand_index_in_match);
CREATE INDEX hand_index_winner
    ON hand_index(winner_seat) WHERE winner_seat IS NOT NULL;
```

- `hand_id` mirrors [record-format.md § HEADER](record-format.md) exactly. **Same identifier in two places**, source of truth is the record file; the SQLite row is a denormalised view for queries.
- `match_id` groups related hands (a 16-hand MCR session). NULL for one-off hands. Indexed for the "list all hands in this match" query.
- `started_at_ms` / `ended_at_ms` are denormalisations of the record's HEADER `ts` and FOOTER `ts`. Stored as ms-since-epoch for index efficiency.
- `terminal_kind`, `winner_seat`, `fan_total` are denormalisations of the FOOTER's terminal block. Allowed to be NULL for an in-progress hand whose row was inserted at deal but not yet finalised. (We *can* insert at start to reserve the `hand_id` immediately; alternative is "insert only at end". Trade pinned in "In-progress rows" below.)
- `record_path` is `records/{year}/{month}/{hand_id}.jsonl` — relative path under `$MAHJONG_DATA_DIR`. UNIQUE constraint prevents two index rows pointing at the same file.
- `record_checksum` is the FOOTER's recomputed checksum, captured at indexing time. Equality with the live file's footer is the integrity check; mismatch means the record was edited (or the DB row is stale).
- `master_seed` is the value the engine was seeded with — present in the record's `meta.master_seed` for selfplay sources; the live-table source uses a per-hand seed derived from the server's RNG (still recorded).
- `source` distinguishes live-table hands from self-play harness output and from records imported via a future "replay this old Botzone log" tool. Filters out training data when we want to query "what did real users play".

#### In-progress rows

Decision: insert the row at hand *deal* time (after HEADER write, before any actions), with `ended_at_ms`, `terminal_kind`, `winner_seat`, `fan_total` all NULL. On hand end, UPDATE the row with terminals + checksum.

Trade-off:

- **Pro:** lets the server crash and recover (an orphan row points us at the partial record file for cleanup decisions).
- **Pro:** allows the wire-protocol's `LIST_TABLES` to surface in-progress hands without reading the disk.
- **Con:** rows can stay NULL forever if the server hard-crashes; a periodic janitor (run at startup; see [server-lifecycle.md](server-lifecycle.md)) reconciles them.

The alternative — "insert only on hand end" — is simpler but loses the "find in-progress hands by user" query (Tailscale-served friends list "what's Alice playing right now?"). The insert-at-deal cost is one INSERT per hand; the table will hold thousands of rows for a long-lived server, easily within SQLite's comfort zone.

### `hand_participants`

One row per seat per hand. Normalises out the per-seat data from `hand_index` so "find this user's games" is a clean indexed query.

```sql
CREATE TABLE hand_participants (
    hand_id              TEXT NOT NULL REFERENCES hand_index(hand_id) ON DELETE CASCADE,
    seat                 INTEGER NOT NULL CHECK (seat BETWEEN 0 AND 3),
    account_id           INTEGER REFERENCES accounts(account_id) ON DELETE SET NULL,
    seat_kind            TEXT NOT NULL CHECK (seat_kind IN ('human', 'bot', 'canned')),
    wind                 TEXT NOT NULL CHECK (wind IN ('F1', 'F2', 'F3', 'F4')),
    final_score_delta    INTEGER,                        -- NULL until hand end
    PRIMARY KEY (hand_id, seat)
);

CREATE INDEX hand_participants_account
    ON hand_participants(account_id, hand_id);
```

- One row per `(hand_id, seat)`. Composite primary key.
- `account_id` is nullable: canned (test-only) seats and synthetic-driver seats have no account. `ON DELETE SET NULL` so an account deletion doesn't break the historical record (it just becomes "anonymous seat 2 in this old hand"). Account *disabling* leaves rows untouched.
- `seat_kind` lets queries like "show me only games where every seat was a real human" run as `WHERE NOT EXISTS (SELECT 1 FROM hand_participants WHERE hand_id = ? AND seat_kind != 'human')`.
- `wind` is the seat's prevailing wind for this hand (deal rotation across a match changes wind per seat). Pulled from the record's HEADER.
- `final_score_delta` is filled at hand end. For a HU hand: +N for winner, -M for losers (M depends on the fan total and rule set). For an exhaustive draw: 0 across the board. For ABORTED: 0.
- `hand_participants_account` index is *the* index for "find this user's hands": `SELECT hand_id FROM hand_participants WHERE account_id = ? ORDER BY hand_id DESC LIMIT 50` is index-only (UUIDv7 sorts as a creation-time proxy).

The "one row per seat" normalisation costs four INSERTs per hand instead of one wide row on `hand_index`. The query cost difference is dramatic: looking up "Alice's games" against a wide row would scan all four `seat_N_account_id` columns; with the join table it's a single index probe.

## Relations diagram

```text
   accounts
     ▲ ▲
     │ │  (FK: ON DELETE CASCADE)
     │ │
     │ └────────────────────  sessions
     │
     │  (FK: ON DELETE SET NULL)
     │
   hand_participants  ─────────►  hand_index
                       (FK: ON DELETE CASCADE)
```

## Migrations

Hand-rolled per [s2-s3-plan.md §10.2](../s2-s3-plan.md). Mechanics:

- A directory `mahjong/persistence/migrations/` contains numbered Python files: `0001_initial.py`, `0002_chat_tables.py`, ... Each exports `def up(conn: sqlite3.Connection) -> None` and `def down(conn: sqlite3.Connection) -> None`.
- A `migrations/__init__.py` lists them in order and provides the runner: `apply_migrations(conn, target: int | None) -> None`. `target=None` means "the latest"; an explicit version target supports rollback to a known state (rare; we'd usually just restore from backup).
- The runner reads `schema_version.version`, computes the diff (`current+1 .. target`), and applies each in a transaction. The transaction wraps `up()` *and* the `schema_version` update; either both happen or neither. SQLite's DDL is mostly transactional, so this works for our migrations.
- `up()` is required; `down()` is best-effort. A v1 down is "DROP TABLE the new ones, ALTER TABLE the modified ones if SQLite supports it" — SQLite's `ALTER TABLE` is limited (no DROP COLUMN until 3.35+; we target 3.35+). For column-removing migrations the standard pattern is "create new table, copy, drop old, rename". We document that pattern in the migrations directory README and write each `down()` accordingly.

### Initial migration (`0001_initial.py`)

Creates everything in this spec:

1. `schema_version`.
2. `accounts` + `accounts_username_lower`.
3. `sessions` + `sessions_account_active` + `sessions_expires`.
4. `hand_index` + its three indexes.
5. `hand_participants` + `hand_participants_account`.
6. Inserts `(version=1, applied_at_ms=now, applied_by="mahjong-server-...")`.

All wrapped in `BEGIN; ... COMMIT`. If any step raises, the transaction rolls back and `schema_version` is empty (or whatever it was before).

### Future migrations

Patterns we'll need:

- **Adding a column:** `ALTER TABLE accounts ADD COLUMN avatar_url TEXT`. Idempotent on SQLite 3.35+.
- **Adding an index:** plain `CREATE INDEX`.
- **Dropping a column:** the new-table-copy-drop-rename dance. Documented inline in the migration; never use `ALTER TABLE ... DROP COLUMN` directly even on 3.35+ (some hosts pin older versions; the dance is portable).
- **Renaming a column:** new-table-copy-drop-rename, or `ALTER TABLE ... RENAME COLUMN` on 3.25+ (safe for our targets).
- **Adding a table:** plain `CREATE TABLE`. Probably the most common future migration as we add chat, achievements, etc.

### Migration testing

Two CI tests gate every migration:

1. **Fresh-apply.** Empty DB → run every migration in order → assert final schema matches the snapshot in `tests/persistence/expected_schema.sql`. This is the "if a new dev clones the repo, does the migration runner produce the right DB" gate.
2. **Forward-from-previous.** A DB at `schema_version = N-1` (built from `0001..N-1` applied) → apply migration `N` → assert final schema matches the snapshot. This is the "did the developer who wrote migration N forget to handle the previous state" gate.

Both gates run on Linux and macOS in CI (per [CLAUDE.md § Verification ladder](../../CLAUDE.md)). The snapshot file is dumped via `sqlite3 mahjong.db .schema`; diffing against it catches accidental table-shape drift.

## Backup and restore

Per [server-plan.md § Persistence](../server-plan.md): the entire `$MAHJONG_DATA_DIR` tree is rsync-friendly.

- **Live backup:** `sqlite3 mahjong.db ".backup mahjong-backup.db"` produces a consistent snapshot without stopping the server (WAL-safe). Pair with `rsync -a records/` for the record files.
- **Restore:** stop server → replace `mahjong.db` and `records/` from the backup → start server.
- **Integrity check on restore:** [server-lifecycle.md](server-lifecycle.md)'s startup runs `PRAGMA integrity_check` (returns "ok" if the file is sound) and a record-vs-index reconciliation (`SELECT record_path FROM hand_index` cross-checked against `find records/ -name "*.jsonl"`). Mismatches are logged but not fatal — a missing record file means the index row is dead; an unindexed record means a recovery candidate.

The "rebuild index from records" path is in [persistence-api.md](persistence-api.md): walk every JSONL in `records/`, parse HEADER + FOOTER, INSERT into `hand_index` + `hand_participants`. Idempotent via `INSERT OR REPLACE` keyed on `hand_id`.

## Alternatives considered

- **Postgres instead of SQLite.** Standard concurrent-server choice. Rejected per [server-plan.md § Tech stack](../server-plan.md): friends-and-family scale doesn't justify a separate DB process, backup story, or auth surface. Revisit when we have >100 concurrent users.
- **Alembic migrations.** Rejected per [s2-s3-plan.md §10.2](../s2-s3-plan.md). Hand-rolled is ~30 lines of code, zero deps, predictable. We swap to Alembic if a real need surfaces.
- **No `schema_version` table; use SQLite's `user_version` PRAGMA.** SQLite has a built-in 32-bit version slot accessed via `PRAGMA user_version`. Rejected because it doesn't carry `applied_at_ms` / `applied_by`; debugging an old DB benefits from the extra metadata. The PRAGMA is two integers (effectively one); our table is three columns with proper types and CHECKs.
- **Wide `hand_index` row with `seat_N_account_id` columns.** Simpler joins; rejected because "find user X's games" becomes a multi-column UNION instead of an index probe. The `hand_participants` normalisation pays for itself within 100 hands of query traffic.
- **`hand_id` as INTEGER PRIMARY KEY.** Was the pre-draft pick in [s2-s3-plan.md §10.7](../s2-s3-plan.md); reverted because [record-format.md](record-format.md) had already pinned UUIDv7 for the on-disk identifier. Having two different identifiers (string in the record, integer in the DB) would create a translation layer for no benefit.
- **Materialised stats columns on `accounts` (win count, total fan, etc.).** Tempting for fast leaderboard queries. Rejected for v1: those are computed from `hand_participants` and `hand_index` on demand; at our scale even a full table scan is sub-millisecond. We can add materialised columns + triggers in a future migration if a real query gets slow.
- **`hand_participants.account_id NOT NULL`.** Cleaner constraint. Rejected: canned-action seats (used in some test fixtures and the deferred S0 walking-skeleton record) have no account. Nullable column + the seat_kind discriminator handle both cases.
- **`sessions` as an in-memory dict, not a DB table.** Faster; rejected because we explicitly pinned in [s2-s3-plan.md §10.9](../s2-s3-plan.md) that session *tokens* persist across restarts (only the live WebSocket state is in-memory). A restart without persistent sessions would force every user to re-log-in.

## Verification fixtures

Acceptance criteria for impl step 8.1 (schema + migrations).

1. **Initial migration applies to a fresh DB.** Empty file → run `apply_migrations()` → `schema_version.version == 1`; every table + index exists per the snapshot.

2. **Forward migration from previous schema.** (Will gain a fixture when migration 0002 lands. Initial migration's "previous" is empty DB, so this collapses to fixture 1 for v1.)

3. **Foreign-key enforcement on.** `PRAGMA foreign_keys` returns 1 on every connection the runtime opens. INSERT into `sessions` with a non-existent `account_id` raises `IntegrityError`.

4. **UNIQUE on `username` is case-sensitive at the column, case-insensitive at lookup.** Insert `"Alice"` succeeds; insert `"alice"` also succeeds at the column level (different bytes), but the case-insensitive query (using `accounts_username_lower` index) returns both — which is the auth path's responsibility to reject. Auth-level uniqueness is enforced in [auth.md](auth.md) on top of this index.

   (Alternative reading: enforce UNIQUE on `lower(username)` at the schema level. We don't, because SQLite's expression-index UNIQUE syntax is awkward; the auth layer's "no duplicate-by-case username on creation" check is the practical enforcement point.)

5. **`CHECK (kind IN ('human', 'bot'))` on accounts.** INSERT with `kind='ghost'` raises `IntegrityError`.

6. **`PRIMARY KEY (hand_id, seat)` on hand_participants.** INSERT two rows with the same `(hand_id, seat)` raises `IntegrityError`. Four rows with distinct seats succeed.

7. **Cascade delete on hand_index.** DELETE FROM hand_index WHERE hand_id = ? cascades to hand_participants for the same hand.

8. **SET NULL on account deletion.** DELETE FROM accounts WHERE account_id = ? sets account_id NULL in hand_participants rows; the rows are NOT deleted.

9. **WAL mode and busy_timeout applied.** After running `apply_migrations()` and acquiring a fresh connection: `PRAGMA journal_mode` returns `wal`; `PRAGMA busy_timeout` returns `5000`.

10. **Schema snapshot stability.** After a clean fresh-apply, the output of `sqlite3 mahjong.db .schema` matches the checked-in snapshot byte-for-byte (modulo whitespace per the test helper). This is the regression gate against accidental schema drift.

11. **Round-trip a complete hand record.** End-to-end: server records a hand (HEADER → events → FOOTER), `persistence-api.write_hand(record)` inserts into hand_index + hand_participants, the SELECT reproduces the metadata, and `find_hands_by_account(...)` returns the row.

12. **Rebuild from records.** Given a `records/` directory and an empty DB, the rebuild path produces a populated DB equivalent (modulo `applied_at_ms`) to one populated incrementally.

Fixture 10 is the load-bearing one — schema drift is the failure mode that breaks downstream queries silently.

## Open questions

None at v1. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md) and the reconciliation with [record-format.md](record-format.md) noted in "Alternatives considered". Possible v2 considerations (avatars, achievements, leaderboard materialisation, chat tables) become future migrations.
