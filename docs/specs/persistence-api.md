# Spec 15 — Persistence API

The Python-side query and write layer over [sqlite-schema.md](sqlite-schema.md) and the on-disk record-file corpus. Pins the function signatures the rest of the server calls; pins the write-time hooks that the table manager triggers on hand events; pins the integrity-check and rebuild paths used at startup.

Tier-2 spec. Single consumer (the server). Builds on [sqlite-schema.md](sqlite-schema.md), [record-format.md](record-format.md), [auth.md](auth.md) (writes `accounts` + `sessions`; not redefined here — see those specs).

**Status:** draft, pre-S3 implementation. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md).

## Goals

- **Records are the source of truth, SQLite is the index.** Every query is "look up in SQLite, optionally load record file by path". The DB never holds data that can't be reconstructed from the records (modulo `accounts` and `sessions`, which are auth-only).
- **One module, narrow API.** All persistence goes through `mahjong/persistence/` — no SQL outside this module. Type-checkable function signatures; no string-interpolation queries.
- **Transactional hand-end.** Inserting a `hand_index` row + four `hand_participants` rows + closing the record file happens atomically from the table manager's perspective. A crash leaves either everything written or nothing written; the rebuild path repairs the remainder.
- **Rebuildable from records alone.** Given just the `records/` tree, the rebuild path reconstructs `hand_index` + `hand_participants` losslessly. (`accounts` and `sessions` cannot be rebuilt — they're not in the records — but a fresh DB can be paired with the existing records.)

## Non-goals

- **Not the schema.** Tables, columns, constraints are in [sqlite-schema.md](sqlite-schema.md).
- **Not the auth flow.** Account/session lifecycle is in [auth.md](auth.md). This module exposes the *low-level functions* auth.md calls (`get_account_by_username`, `insert_session`, etc.) but does not implement the validation logic.
- **Not a generic ORM.** No SQLAlchemy, no Django-style models. Plain `sqlite3` + typed dicts/dataclasses returned from query helpers.
- **Not stats / analytics.** "Win rate over last 30 days" queries live in a future analytics module, not here. This module ships the primitives those queries would compose.

## Module layout

```text
mahjong/persistence/
    __init__.py            # re-exports the public API
    db.py                  # connection pooling, pragmas, lifecycle
    accounts.py            # account + session CRUD (consumed by auth.md)
    hands.py               # hand_index + hand_participants CRUD
    rebuild.py             # records/ → DB reconciliation
    migrations/            # numbered .py files; see sqlite-schema.md
        __init__.py
        0001_initial.py
```

The `__init__.py` exports a single `Persistence` class that holds the connection and methods. Tests construct it with `:memory:`; production constructs it with `$MAHJONG_DATA_DIR/mahjong.db`.

## Public API

```python
class Persistence:

    def __init__(self, db_path: str, data_dir: Path) -> None:
        """Open the DB; apply pragmas; verify schema_version."""

    # --- lifecycle ---
    def close(self) -> None: ...
    def integrity_check(self) -> IntegrityReport: ...

    # --- accounts (consumed by auth.md) ---
    def get_account_by_username(self, username: str) -> Account | None: ...
    def get_account_by_id(self, account_id: int) -> Account | None: ...
    def insert_account(self, *, username, display_name, kind, role,
                       password_hash) -> int: ...
    def update_account_login(self, account_id: int, *, password_hash=None,
                             last_login_ms=None) -> None: ...
    def set_account_disabled(self, account_id: int, disabled: bool) -> None: ...

    # --- sessions (consumed by auth.md) ---
    def insert_session(self, *, session_id, account_id, issued_at_ms,
                       expires_at_ms, user_agent) -> None: ...
    def get_session(self, session_id: str) -> SessionRow | None: ...
    def renew_session(self, session_id: str, *, expires_at_ms,
                      last_seen_ms) -> None: ...
    def revoke_session(self, session_id: str) -> None: ...
    def delete_expired_sessions(self, before_ms: int) -> int:
        """Returns rows deleted."""

    # --- hands (consumed by table manager + queries) ---
    def reserve_hand(self, *, hand_id, match_id, hand_index_in_match,
                     ruleset_id, ruleset_config_hash, started_at_ms,
                     master_seed, record_path, server_version, source,
                     participants: list[Participant]) -> None:
        """Atomic INSERT: hand_index row + 4 hand_participants rows.
        Called at HEADER write, before any actions."""

    def finalize_hand(self, hand_id: str, *, ended_at_ms, terminal_kind,
                      winner_seat, fan_total, record_checksum,
                      participants_scores: dict[int, int]) -> None:
        """Atomic UPDATE: hand_index row + 4 hand_participants.final_score_delta.
        Called at FOOTER write, after hand terminates."""

    def get_hand(self, hand_id: str) -> HandRow | None: ...
    def find_hands_by_account(self, account_id: int, *, limit: int = 50,
                              before_hand_id: str | None = None) -> list[HandRow]: ...
    def find_hands_by_match(self, match_id: str) -> list[HandRow]: ...
    def find_recent_hands(self, limit: int = 50) -> list[HandRow]: ...
    def find_in_progress_hands(self) -> list[HandRow]:
        """Hands inserted via reserve_hand but not yet finalized.
        Used by server-lifecycle startup janitor."""

    # --- rebuild ---
    def rebuild_index_from_records(self, *, dry_run: bool = False) -> RebuildReport: ...
```

### Return types

Plain dataclasses, no ORM rows. Each query helper materialises the row into a typed object so callers don't depend on column order.

```python
@dataclass(frozen=True)
class Account:
    account_id: int
    username: str
    display_name: str
    kind: Literal["human", "bot"]
    role: Literal["user", "admin"]
    password_hash: str
    disabled: bool
    created_at_ms: int
    last_login_ms: int | None

@dataclass(frozen=True)
class SessionRow:
    session_id: str
    account_id: int
    issued_at_ms: int
    expires_at_ms: int
    last_seen_ms: int
    revoked: bool
    user_agent: str | None

@dataclass(frozen=True)
class HandRow:
    hand_id: str
    match_id: str | None
    hand_index_in_match: int
    ruleset_id: str
    ruleset_config_hash: str
    started_at_ms: int
    ended_at_ms: int | None
    terminal_kind: Literal["HU", "EXHAUSTIVE_DRAW", "ABORTED"] | None
    winner_seat: int | None
    fan_total: int | None
    master_seed: str
    record_path: str
    record_checksum: str | None
    server_version: str
    source: Literal["live", "selfplay", "replay-import"]
    participants: list[Participant]   # populated by get_hand; not by list queries

@dataclass(frozen=True)
class Participant:
    seat: int
    account_id: int | None
    seat_kind: Literal["human", "bot", "canned"]
    wind: Literal["F1", "F2", "F3", "F4"]
    final_score_delta: int | None
```

## Wiring into the table manager

Two hook points in the existing table manager ([seat-port.md](seat-port.md)):

### On HEADER write (hand start)

After the record writer writes the HEADER event:

```python
persistence.reserve_hand(
    hand_id=record.header.hand_id,
    match_id=record.header.match_id,
    hand_index_in_match=record.header.hand_index_in_match,
    ruleset_id=record.header.ruleset.id,
    ruleset_config_hash=record.header.ruleset.config_hash,
    started_at_ms=parse_iso8601_to_ms(record.header.ts),
    master_seed=record.header.meta.master_seed or record.header.seed,
    record_path=str(record_path.relative_to(data_dir)),
    server_version=record.header.server.version,
    source=record.header.meta.source or "live",
    participants=[
        Participant(
            seat=s.seat,
            account_id=resolve_account_id(s.identity),
            seat_kind=s.identity.kind,
            wind=s.wind,
            final_score_delta=None,
        )
        for s in record.header.seats
    ],
)
```

`resolve_account_id` is a small helper: for `kind: "human"` identities, `user_id` is `f"u_{account_id}"` — strip the prefix. For `kind: "bot"`, look up by bot account username. For `kind: "canned"`, return None.

### On FOOTER write (hand end)

After the record writer writes the FOOTER and computes the final checksum:

```python
persistence.finalize_hand(
    hand_id=record.header.hand_id,
    ended_at_ms=parse_iso8601_to_ms(record.footer.ts),
    terminal_kind=record.footer.terminal.kind,
    winner_seat=record.footer.terminal.winner if record.footer.terminal.kind == "HU" else None,
    fan_total=record.footer.terminal.fan_total if record.footer.terminal.kind == "HU" else None,
    record_checksum=record.footer.checksum,
    participants_scores=record.footer.terminal.score_deltas,  # {seat: delta}
)
```

Both calls run in the same asyncio loop as the table manager. SQLite writes from one connection are serial; we do not need explicit locking.

## Transaction semantics

- `reserve_hand` is `BEGIN; INSERT hand_index; INSERT hand_participants × 4; COMMIT`. Atomic; either all five rows land or none do.
- `finalize_hand` is `BEGIN; UPDATE hand_index; UPDATE hand_participants × 4; COMMIT`. Atomic.
- The record-file writes are *not* in the transaction (the file is on a different storage layer). The ordering matters:
  - HEADER write → fsync → `reserve_hand`. If the server crashes between HEADER and reserve, the rebuild path picks up the orphaned record file and inserts the row.
  - actions write → ... → FOOTER write → fsync → `finalize_hand`. If the server crashes between FOOTER and finalize, the rebuild path detects an unfinalized `hand_index` row whose record file is footer-complete and UPDATEs it.

A crash *during* the record file write (mid-event, no FOOTER) leaves an orphan record file. The rebuild path detects this (no FOOTER) and marks the hand row as `terminal_kind = 'ABORTED'`.

## Startup integrity check

[server-lifecycle.md](server-lifecycle.md) calls `persistence.integrity_check()` on startup, which:

1. Runs `PRAGMA integrity_check` on the SQLite file. Must return `"ok"`.
2. SELECTs all `hand_index` rows with `record_path`. For each:
   - File exists at `data_dir / record_path` — assert.
   - File's FOOTER checksum matches `hand_index.record_checksum` — assert (only for finalized rows; in-progress rows skip this).
3. Walks `records/` recursively. For each `.jsonl` file:
   - Cross-reference against `hand_index.record_path`. Files without a row are "orphaned records" — logged for the operator.

Returns an `IntegrityReport` with counts: `(checked_db, ok_files, missing_files, orphaned_files, in_progress_hands)`. Server proceeds if the report is "ok or warnings"; refuses to start if `PRAGMA integrity_check` returned anything but `"ok"` (the DB is corrupted; restore from backup).

## Rebuild path

`rebuild_index_from_records(dry_run=False)`:

1. Walks `records/` recursively.
2. For each `.jsonl` file:
   a. Parses HEADER (always line 1) and FOOTER (always last line; absent for crash-truncated files).
   b. Computes the `Participant` list from HEADER's `seats[]`.
   c. INSERTs into `hand_index` + `hand_participants` via `INSERT OR REPLACE` (idempotent).
   d. If FOOTER present, also UPDATEs `terminal_kind`, `winner_seat`, `fan_total`, `record_checksum`, `ended_at_ms`.
   e. If FOOTER absent and the file's last event isn't a TERMINAL, mark `terminal_kind = 'ABORTED'`.
3. Returns `RebuildReport(processed_files, inserted, updated, errors)`.

`dry_run=True` runs the walk and reports counts without writing.

The rebuild is idempotent. Re-running it on a complete DB is a no-op (every row is `INSERT OR REPLACE`'d with identical content). Tested as fixture 12 in [sqlite-schema.md](sqlite-schema.md).

## Periodic tasks

Run by [server-lifecycle.md](server-lifecycle.md)'s scheduler:

- **Daily** — `delete_expired_sessions(before_ms = now - SESSION_LIFETIME_MS)`. Housekeeping; not load-bearing.
- **At startup** — `integrity_check()`. Block startup on a corrupt DB.

No other periodic tasks in v1.

## Alternatives considered

- **SQLAlchemy ORM.** Standard, well-typed. Rejected: significant dependency for a single-file SQLite schema we'll keep small. Plain `sqlite3` + dataclasses is ~100 lines per module and easier to reason about.
- **DAO per table (one class per table).** Rejected for cohesion: callers want "find hands by account" which crosses tables; a single `Persistence` class with helpers is simpler than a coordinator over per-table DAOs.
- **Cache layer on top of SQLite for hot queries.** Rejected for v1: SQLite at our scale doesn't need a cache; benchmarking would show queries are already <1ms.
- **Background-thread DB writes.** Rejected: writes are rare (one INSERT per hand at HEADER, one UPDATE at FOOTER, periodic session ops). Synchronous from the asyncio loop is fine; SQLite's WAL means readers don't block.
- **Rebuild as a separate CLI rather than an API method.** Could be useful. Rejected for now: keeping it as a method lets the startup integrity-check trigger it automatically when a missing row is detected. A future `python -m mahjong.persistence rebuild` CLI is additive.
- **Two-phase commit between record file and DB.** Rejected for complexity. The "HEADER → reserve_hand → ... → finalize_hand" ordering plus the rebuild path handles every crash window.

## Verification fixtures

Acceptance criteria for impl step 8.3 (persistence API).

1. **`reserve_hand` round-trip.** Reserve a hand; `get_hand(hand_id)` returns the row with NULL terminals; `find_in_progress_hands()` includes it.

2. **`finalize_hand` round-trip.** After finalize: terminals populated; `find_in_progress_hands()` excludes the hand; `participants_scores` materialised on `hand_participants.final_score_delta`.

3. **`reserve_hand` is atomic.** Force the second INSERT (a participant row) to fail via a CHECK violation: the hand_index row is not present (transaction rolled back).

4. **`find_hands_by_account` orders by time descending.** Insert 10 hands at known timestamps for one account; query returns them in `started_at_ms DESC`.

5. **`find_hands_by_account` paginates with `before_hand_id`.** First call returns 50 most recent; second call with `before_hand_id=first[-1].hand_id` returns the next 50 without overlap.

6. **`find_hands_by_match` returns ordered by `hand_index_in_match`.** Three hands of a match in random insertion order → returned in 0, 1, 2 order.

7. **`integrity_check` detects missing record file.** Insert a hand_index row pointing at a path that doesn't exist; integrity_check returns `missing_files = 1`.

8. **`integrity_check` detects orphaned record file.** Place a valid JSONL under records/ that no hand_index row references; integrity_check returns `orphaned_files = 1`.

9. **`integrity_check` validates record_checksum.** Tamper with a record file's content (preserving FOOTER); integrity_check reports a checksum mismatch.

10. **Rebuild from records produces equivalent DB.** Take a populated DB; export records; clear the DB; rebuild from records; resulting rows match the original (modulo `created_at_ms` and similar audit fields the rebuild can't reproduce).

11. **Rebuild is idempotent.** Run rebuild twice on the same records; no spurious updates on the second run.

12. **Session CRUD round-trip.** Insert session; get returns; renew updates expiry; revoke flips revoked; delete_expired_sessions removes after expiry passes.

13. **Account CRUD round-trip.** Insert account; get by username (case-insensitive); update_account_login changes hash + timestamp; set_account_disabled flips the flag.

Fixture 10 is the load-bearing one for the "records are the source of truth" property.

## Open questions

None at v1. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md). Future analytics queries become additions to this module without API breaks.
