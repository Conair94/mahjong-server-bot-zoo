# Session handoff — 2026-05-25 (end of Layer 8 — pragmatic-cut 8.5 + 8.6)

Snapshot of implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 8 is functionally complete via the pragmatic cut.**

| # | Step | Notes |
| --- | --- | --- |
| 8.0 | Multi-hand orchestration | landed prior session |
| 8.1 | SQLite schema + migrations | landed prior session |
| 8.2 | Auth module (argon2id + sessions) | landed prior session |
| 8.3 | Persistence API | landed prior session |
| 8.4 | Multi-table orchestrator | landed prior session |
| **8.5** | **Server lifecycle (pragmatic cut)** | **this session — config, AUTH wire, persistence wiring, serve CLI, account CLI, signal-based drain** |
| **8.6** | **End-to-end S3 fixture** | **this session — `tests/server/test_s3_gate.py` + live LAN verification** |

**Verification at end of session:** ruff clean · mypy strict clean · **609 tests pass repo-wide** (607 fast + 2 slow including the S3 gate). Live wire round-trip against `192.168.1.157:8401` from a Python websockets client succeeded (HELLO → AUTH → CREATE_TABLE → ATTACH → 25 prompts → EXHAUSTIVE_DRAW HAND_END).

## What the pragmatic cut covers vs the full 8.5 spec

**Landed:**

- `mahjong/server/config.py` — `ServerConfig` + `load_config_from_env` (env-var loader with unknown-var warnings).
- AUTH_REQUEST / RESUME wire handlers in `MultiTableOrchestrator` (gated on `persistence is not None` by default; overridable via `require_auth`).
- Persistence wired into `TableHandle._run_hand_loop` — `reserve_hand` before `mgr.run_hand`, `finalize_hand` (or ABORTED) in a `finally` block.
- `mahjong/cli/account.py` — `python -m mahjong account {create,list}` CLI with stdin-or-getpass password input.
- `mahjong/cli/serve.py` — `python -m mahjong serve` entry point with config load → DB open → integrity check → in-progress→ABORTED reconciliation → SIGTERM/SIGINT-based graceful drain.
- `sqlite3.connect(check_same_thread=False)` so async handlers can call sync persistence/auth via `run_in_executor`.

**Deferred (additive — drop in when first needed):**

- `/health` endpoint (HTTP route exists in `WebSocketServer`; not wired in serve CLI).
- Drain-timeout escalation with `task.cancel()` (current cut uses `asyncio.wait_for(orch.close(), timeout=shutdown_timeout_s)`).
- Periodic WAL checkpoint task + periodic session cleanup task.
- Structured JSON logging (currently stdlib `logging.basicConfig` plain-text).
- Standalone fixtures for: SIGKILL recovery (16), drain-timeout escalation (14), WAL checkpoint TRUNCATE on drain (15), `/health` 200/503/500 (9-11). Logic mostly exists; tests not yet written.

## Decisions reached this session

- **(64) Pragmatic-cut serve CLI is single-file.** `mahjong/cli/serve.py` holds the startup sequence, signal handlers, and drain inline. The spec calls for `mahjong/server/lifecycle.py`; extract when a second consumer needs the same drain.
- **(65) `check_same_thread=False` on the SQLite connection.** Required for `run_in_executor` to call auth/persistence from a worker thread. Safe at our scale (single process, WAL, Python GIL serialises stmt execution). Documented inline in `mahjong/persistence/db.py`.
- **(66) auth_required defaults to `persistence is not None`.** Existing tests that pass no persistence keep their no-auth path; the serve CLI always passes persistence and so always requires auth. Override via `require_auth: bool | None`.
- **(67) Account_id is derived from `user_id` via the `u_{int}` convention.** No separate carry-along account_id field in `HumanIdentity`. Auth handler builds `user_id = f"u_{account_id}"`; `TableHandle._reserve_hand_row` parses it back.
- **(68) Per-table `match_id` is `match_t{table_id}`.** Stable across the table's lifetime; lets `find_hands_by_match` group all hands at one table. A future "match abstraction" (16-hand MCR matches) would change this.
- **(69) ABORTED finalisation runs in a `finally` block.** `mgr.run_hand` exceptions (cancellation, errors) trigger `_finalize_hand_row(final_state=None)` which writes `terminal_kind="ABORTED"` and zero score-deltas. The live LAN smoke test exercised this exact path (server SIGTERM'd mid-second-hand, producing one EXHAUSTIVE_DRAW + one ABORTED row).
- **(70) Live verification is a Linux-target check.** macOS dev works fine; the smoke test bound `0.0.0.0:8401` and accepted connections from `192.168.1.157` (LAN IP). For real deploy: change `MAHJONG_LISTEN_ADDR` to the Tailscale tailnet IP and rely on the host's systemd unit (S7).

## What this session built

### Code

- `mahjong/server/config.py` — env loader + `ServerConfig` dataclass.
- `mahjong/server/registry.py` — added `Persistence | None` to `TableRegistry` + `TableHandle`; threaded through to `_reserve_hand_row` / `_finalize_hand_row` hooks around `mgr.run_hand`.
- `mahjong/server/orchestrator.py` — added `_AuthState` per-connection identity store, `_run_auth_phase` (AUTH_REQUEST + RESUME via `run_in_executor`), `_identity_for(conn)`, `_is_admin(conn)`.
- `mahjong/persistence/db.py` — `check_same_thread=False`.
- `mahjong/cli/account.py` — new CLI.
- `mahjong/cli/serve.py` — new CLI.
- `mahjong/cli/__init__.py` — dispatch `account` + `serve`.

### Tests

- `tests/server/test_config.py` — 6 tests for env loader (server-lifecycle fixtures 1–3).
- `tests/server/test_auth_wire.py` — 3 tests (auth success, failure does not leak reason, RESUME round-trip).
- `tests/server/test_persistence_wiring.py` — 1 test (account → play hand → row finalised with right account_id and scores).
- `tests/server/test_s3_gate.py` — 1 slow test (subprocess server → account CLI → wire client plays hand → SIGTERM → restart → query persistence).

## Known limitations carried forward

- `next_hand_seq` in HAND_END still always `null` (since Step 8.0). Low priority.
- No `/health` endpoint in the serve CLI yet (route exists in `WebSocketServer`, no handler wired).
- No periodic WAL checkpoint or session cleanup tasks.
- No structured JSON logging — plain stdlib `basicConfig` for now.
- Drain timeout is a single `asyncio.wait_for(orch.close(), timeout=...)`; no two-phase escalation with `task.cancel()`.
- `mahjong/cli/serve.py` uses `seed=int(time.time())` for live play (nondeterministic per server start). Self-play uses explicit seeds.
- All Step 8.4 known limitations still apply (`next_hand_seq`, no account CLI was a limitation — now resolved).

## How to run the server

```bash
# 1. Create an admin account (interactive; or use --password-stdin).
MAHJONG_DATA_DIR=./var/mahjong python -m mahjong account create \
    --username alice --display "Alice" --admin

# 2. Run the server on the loopback (default) or LAN.
MAHJONG_DATA_DIR=./var/mahjong python -m mahjong serve
# or LAN-accessible:
MAHJONG_DATA_DIR=./var/mahjong MAHJONG_LISTEN_ADDR=0.0.0.0:8400 \
    python -m mahjong serve

# 3. Connect: open http://<addr>:<port>/ in a browser for the web client,
#    or point a `mahjong-v1` subprotocol websockets client at
#    ws://<addr>:<port> and follow HELLO → AUTH_REQUEST → CREATE_TABLE
#    → ATTACH.
```

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] `git log --oneline -5` — confirm Layer 8 pragmatic-cut commit at HEAD.
- [ ] `.venv/bin/python -m pytest --tb=no -q` — confirm 609 passing.
- [ ] Decide next focus:
  - **Layer 8 polish:** wire `/health`, add periodic tasks, structured JSON logging, drain-timeout escalation. Each is additive; pick when first needed.
  - **S7 ops hardening (Linux deploy):** RPi 5 / mini PC, Tailscale, systemd unit, journald-friendly logging. Cross-machine deploy.
  - **Real client surface:** the web client at `/static/` is currently the 7.5c walking skeleton (per `project_client_vision_web_ascii` memory); polish for friends-and-family hosting.
  - **Layer 9+ (RL training):** the AI plan now unblocks — persistent corpus + recorded hands give the eval/training harness real data.
- [ ] Optionally `/extract-learnings` to consolidate memory before starting.
