# Session handoff — 2026-05-25 (end of Layer 8 Step 8.4)

Snapshot of implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 8 Steps 8.1, 8.2, 8.3, and 8.4 are complete.**

| # | Step | Commit | Notes |
| --- | --- | --- | --- |
| KL-fix | dealer_seat through mgr.run_hand | `9f6ad07` | + hand_index_in_match; 2 new tests |
| **8.1** | **SQLite schema + migration runner** | **`40d645c`** | **17 tests; all 10 spec fixtures** |
| **8.2** | **Auth module (argon2id + sessions)** | **`bee3a5a`** | **24 tests; all 17 spec fixtures** |
| **8.3** | **Persistence API (Persistence class)** | **`bef3972`** | **13 tests; all 13 spec fixtures** |
| **8.4** | **Multi-table orchestrator** | **`2a7c587`** | **5 tests; fixtures 17 + 18 + FLIST + FADMIN** |

**Verification at end of session:** ruff clean · mypy strict clean · **597 tests pass repo-wide** (592 prior + 5 new; 2 Linux-only + 1 slow deselected).

## Decisions reached this session

- **(59) `TableHandle` does not reuse `WebOrchestrator` internals.** The hand-loop logic is duplicated between `WebOrchestrator` (single-table, existing tests unchanged) and `TableHandle` (multi-table). The duplication is ~90 lines and acceptable at this scale; it avoids coupling that risks breaking the 8.0–8.3 test suite. Step 8.5 may unify them if the architecture stabilises.
- **(60) `table_id` is a string at the registry API boundary, int in wire frames.** `TableRegistry.create_table_direct` returns `str`; the orchestrator converts to `int` for `TABLE_CREATED`. `TableSessions` receives `int(table_id)` since it typed `table_id: int`.
- **(61) `admin_predicate` is the seam for auth in Step 8.5.** `MultiTableOrchestrator` accepts `admin_predicate: Callable[[Connection], bool]` (default: always True). `CLOSE_TABLE` calls it; Step 8.5 will replace the default with an auth-token check.
- **(62) Persistence wiring deferred to Step 8.5.** `TableHandle._run_hand_loop` does not call `reserve_hand`/`finalize_hand`. The hook point exists (before/after `mgr.run_hand`); wiring happens with the full lifecycle startup in 8.5.
- **(63) `create_table_direct` is the only allocation path for now.** Both the wire handler (`_handle_create_table`) and tests call `registry.create_table_direct(...)`. A future `create_table` coroutine (async, awaitable for the task to start) may replace it in 8.5 when the lifecycle is more formalised.

## What this session built

### Step 8.4 — Multi-table orchestrator

**[mahjong/server/\_\_init\_\_.py](../mahjong/server/__init__.py)** — new package marker

**[mahjong/server/registry.py](../mahjong/server/registry.py)** — new:

- `TableSummary` — frozen dataclass with `table_id`, `ruleset`, `hand_index`, `phase`; `to_wire()` for LIST_TABLES
- `ShuttingDown`, `TableNotFound` — typed exceptions
- `TableHandle` — single-table: `TableSessions` + CannedAdapters + hand-loop task; `attach`, `spectate`, `handle_inbound`, `on_socket_dropped`, `close` (shutdown sessions + cancel task); `summary()`, `record_path`, `hand_id`, `match_done` properties
- `TableRegistry` — `dict[str, TableHandle]`; `create_table_direct` (auto-increments ID, creates records dir, allocates `TableHandle`); `list_tables`, `get_table`, `close_table`, `drain_all`; `accepting_new` flag

**[mahjong/server/orchestrator.py](../mahjong/server/orchestrator.py)** — new:

- `MultiTableOrchestrator` — `WebSocketServer` + `TableRegistry`; two-phase handler (pre-attach admin loop → attached inbound loop)
- Wire handlers: `LIST_TABLES` → `TABLE_LIST`, `CREATE_TABLE` → `TABLE_CREATED` (checks `accepting_new`), `CLOSE_TABLE` (checks `admin_predicate`), `ATTACH`/`SPECTATE` (routes to `TableHandle`)
- `admin_predicate` kwarg — defaults to `lambda conn: True` (all-admin in S2); replaced by auth check in 8.5

**[tests/server/test\_multi\_table.py](../tests/server/test_multi_table.py)** — new:

- 5 fixtures: F_LIST, F_CREATE, F17, F18, F_CLOSE_ADMIN

## Known limitations carried forward

- **`next_hand_seq` in HAND_END is always `null`** (since Step 8.0). Low priority; client works.
- **No wire-protocol integration for AUTH_REQUEST / RESUME yet.** Auth and persistence are pure Python; not wired into any WS handler. Step 8.5.
- **No account CLI yet.** `python -m mahjong.cli.account create` not implemented.
- **`Persistence` not wired into `WebOrchestrator` or `TableHandle`.** `reserve_hand`/`finalize_hand` not called at HEADER/FOOTER write. Step 8.5.
- **`CLOSE_TABLE` via wire protocol not fully exercised.** `F_CLOSE_ADMIN` uses `registry.create_table_direct` directly because CLOSE_TABLE requires the table to exist first and a way to create it without a second admin connection. Production use works; the test takes the simpler path.
- All earlier known limitations still apply.

## What remains

**Remaining Layer 8 steps per CHECKLIST.md:**

- **Step 8.5 — Server lifecycle.** Graceful drain, startup sequence (DB open → migrations → auth check → serve), systemd unit, periodic session cleanup. This is where `Persistence` gets wired into `TableHandle` at HEADER/FOOTER hook points, `admin_predicate` gets replaced by real auth, and `WebOrchestrator` may be refactored to delegate to `TableHandle`.
- **Step 8.6 — End-to-end S3 gate.** Byte-identical + auth + persistence fixture.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] `git log --oneline -5` — confirm `2a7c587` (or later) at HEAD.
- [ ] `.venv/bin/python -m pytest -m "not slow" --tb=no -q` — confirm 597 passing, 3 deselected/skipped.
- [ ] Read [docs/specs/server-lifecycle.md](specs/server-lifecycle.md) §§ Configuration, Startup sequence, Graceful shutdown before starting 8.5.
- [ ] Re-read [docs/specs/persistence-api.md](specs/persistence-api.md) § Wiring into the table manager for the reserve_hand/finalize_hand hook points.
- [ ] Optionally `/extract-learnings` to consolidate memory before starting.
