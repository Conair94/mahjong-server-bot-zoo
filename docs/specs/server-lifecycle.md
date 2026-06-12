# Spec 16 — Server lifecycle

The process-level contract for `python -m mahjong serve`: how the server starts, what it holds in memory while running, how it accepts new work, how it stops, and the small operational surface (`/health`, env-var configuration, structured logging) that lets a host system run it as a normal long-lived service.

Tier-2 spec. Single consumer (the `mahjong.cli.serve` entry point and its test harness). Pulls together every other tier-2 surface — opens [sqlite-schema.md](sqlite-schema.md) via [persistence-api.md](persistence-api.md), binds the [wire-protocol.md](wire-protocol.md) WebSocket, drains [session-mux.md](session-mux.md), authenticates via [auth.md](auth.md), and orchestrates N instances of the Layer 4 table manager ([seat-port.md](seat-port.md)).

**Status:** draft, pre-S3 implementation. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md).

## Goals

- **One process, one job.** `python -m mahjong serve` is the only entry point. No separate worker pool, no separate DB process, no out-of-process scheduler. The whole server is a single asyncio loop hosting a WebSocket acceptor, a `Persistence` instance, and a registry of `TableManager`s. This matches [seat-port.md § Lifecycle and concurrency model](seat-port.md) ("one asyncio event loop owns the whole server") and keeps the deploy story trivial (one binary + one config + one systemd unit in S7).
- **Configuration via environment.** 12-factor: every runtime knob is a `MAHJONG_*` env var with a documented default. No config files in v1. The same binary runs in `dev` (talking to `127.0.0.1:8400` and `./var/`) and on the Raspberry Pi 5 (talking to the Tailscale address and `/var/lib/mahjong/`) without code branches.
- **Graceful shutdown finishes the current turn.** `SIGTERM` stops *new* work (no new `ATTACH`, no new hands started) and lets *in-flight* work finish (the current turn's `decide` resolves, the table manager writes its FOOTER, the index row is finalised, the WAL is checkpointed). A deploy never truncates a hand. The drain has a configurable timeout; past that, escalate to `SIGKILL`-equivalent and let the rebuild path clean up on next startup.
- **Crash-resumable.** A hard kill (host power loss, OOM, `SIGKILL`) cannot corrupt the persistent state. SQLite is WAL-safe; record files are append-only with FOOTER checksums; the startup integrity check ([persistence-api.md § Startup integrity check](persistence-api.md)) reconciles index ↔ records. In-flight hands at the time of a crash are lost (no resumption of *gameplay* across a restart — see [s2-s3-plan.md §10.9](../s2-s3-plan.md)) but their partial records and orphan rows are detected and marked `ABORTED`.
- **`/health` is a fast, dependency-aware probe.** A separate HTTP endpoint on the same WebSocket server (path `/health`, not `/socket`) returns 200 when the server can serve requests. Used by Tailscale Funnel health checks, systemd `Restart=on-failure` predicates, and the operator running `curl` from the LAN. Does not require auth; returns nothing sensitive.
- **Structured logging to stdout.** One JSON object per line, written to `stdout`. Captured by `journald` / `docker logs` / whatever the host runs the process under. No log file rotation in the server itself — the host handles persistence ([project_hosting_target.md](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_hosting_target.md): "S7 ops hardening is Linux-specific" — systemd + journald handle rotation).
- **Multi-table is just a dict.** Per [s2-s3-plan.md §10.3](../s2-s3-plan.md), multi-table is `{table_id: TableManager}`. No multi-table-aware refactor of `TableManager`. This spec pins the registry's lifecycle and the cross-table operations (`CREATE_TABLE`, `LIST_TABLES`, `CLOSE_TABLE`) that ride on top.

## Non-goals

- **Not the WebSocket frame loop.** Accept, frame, route — those are the [websockets](https://websockets.readthedocs.io/) library + the [wire-protocol.md](wire-protocol.md) codec. This spec invokes them; it doesn't redefine them.
- **Not the DB schema.** Tables, indexes, migrations are in [sqlite-schema.md](sqlite-schema.md). This spec opens the DB, runs migrations, calls integrity check, and closes the DB.
- **Not auth policy.** Whether a given credential is valid is [auth.md](auth.md). This spec wires the auth module into the connection-accept path.
- **Not systemd, Caddy, Tailscale config.** Those are S7 ops hardening. This spec pins what the *process* must do; the host wraps that into a service.
- **Not in-process hot reload.** Restart the process for config changes. No `SIGHUP` handler in v1.
- **Not log shipping.** Stdout JSON is the boundary. journald / vector / promtail are downstream of this spec.
- **Not metrics / Prometheus.** A `/health` endpoint with a coarse OK/not-OK is the v1 ops signal. Detailed metrics (request rate, table-active count, eval scores) are deferred to S4+ when there's something useful to graph.
- **Not auto-restart of in-flight hands.** If the server crashes mid-hand, that hand is lost — clients reconnect and find their seat un-attached, their previous record file orphaned-then-`ABORTED`. The "resume the actual gameplay across a restart" feature is explicitly out of scope ([s2-s3-plan.md §10.9](../s2-s3-plan.md)).

## Process entry point

```python
# mahjong/cli/serve.py — invoked as `python -m mahjong serve`

async def main(argv: list[str] | None = None) -> int:
    config = load_config_from_env()                       # § Configuration
    setup_logging(config)                                 # § Logging
    log.info("server.starting", config=config.redacted())

    # Startup — fail fast on anything that prevents serving
    persistence = await open_persistence(config)          # § Startup sequence
    ruleset_registry = load_rulesets(config)
    bot_registry = load_bot_registry(config)              # for S2 spectator-of-bot-table

    table_registry = TableRegistry(persistence, ruleset_registry, bot_registry, config)

    server = WebSocketServer(config, table_registry, persistence)
    health = HealthHandler(server, persistence, table_registry)

    # Periodic tasks (one task each; cancelled on shutdown)
    tasks = [
        asyncio.create_task(periodic_session_cleanup(persistence, config)),
        asyncio.create_task(periodic_wal_checkpoint(persistence, config)),
    ]

    # Signal handlers
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)

    await server.start()                                  # binds socket, starts accepting
    log.info("server.ready", listen=config.listen_addr, pid=os.getpid())

    # Run until signalled
    await shutdown_event.wait()
    log.info("server.shutting_down")

    # Drain — § Graceful shutdown
    await drain(server, table_registry, persistence, tasks, config)
    log.info("server.exited")
    return 0
```

The module is invocable as `python -m mahjong serve` via `mahjong/__main__.py` dispatching `argv[1] == "serve"` to this function. `argv[1] == "play-test"` and `argv[1] == "selfplay"` continue to dispatch to Layer 4 and Layer 6 CLIs respectively (unchanged).

## Configuration

Every knob is `MAHJONG_*` in the process environment. The loader parses, validates, and returns a frozen `ServerConfig` dataclass. Unknown `MAHJONG_*` vars log a warning at startup (catches typos like `MAHJONG_HARTBEAT_INTERVAL_SECONDS`).

```python
@dataclass(frozen=True)
class ServerConfig:
    # --- transport ---
    listen_addr: str            # MAHJONG_LISTEN_ADDR        default "127.0.0.1:8400"
    health_listen_addr: str     # MAHJONG_HEALTH_LISTEN_ADDR default "" (= same as listen_addr)
    # --- storage ---
    data_dir: Path              # MAHJONG_DATA_DIR           default "./var/mahjong"
    db_path: Path               # derived: data_dir / "mahjong.db"
    records_dir: Path           # derived: data_dir / "records"
    # --- session / auth ---
    seat_hold_seconds: int      # MAHJONG_SEAT_HOLD_SECONDS         default 60
    heartbeat_interval_s: int   # MAHJONG_HEARTBEAT_INTERVAL_SECONDS default 30
    resume_buffer_size: int     # MAHJONG_RESUME_BUFFER_SIZE         default 256
    session_lifetime_hours: int # MAHJONG_SESSION_LIFETIME_HOURS     default 336 (14 days)
    max_spectators_per_table: int  # MAHJONG_MAX_SPECTATORS_PER_TABLE default 32
    # --- rulesets / bots ---
    default_ruleset: str        # MAHJONG_DEFAULT_RULESET    default "mcr-2006"
    bot_manifest_dir: Path | None  # MAHJONG_BOT_MANIFEST_DIR default None (bots not auto-loaded)
    # --- lifecycle ---
    shutdown_timeout_s: int     # MAHJONG_SHUTDOWN_TIMEOUT_SECONDS default 30
    wal_checkpoint_interval_s: int  # MAHJONG_WAL_CHECKPOINT_INTERVAL_SECONDS default 300
    # --- logging ---
    log_level: str              # MAHJONG_LOG_LEVEL          default "INFO"
    log_format: str             # MAHJONG_LOG_FORMAT         default "json"  (one of "json", "console")
    # --- diagnostics ---
    server_version: str         # derived from package metadata + git SHA at build time
    server_id: str              # derived: f"mahjong-server-{version}"

    def redacted(self) -> dict:
        """For logging at startup. No secrets in v1, but reserve the seam."""
        return asdict(self)
```

### Variable reference

| Var | Default | Purpose | Consumer |
| --- | --- | --- | --- |
| `MAHJONG_LISTEN_ADDR` | `127.0.0.1:8400` | WebSocket bind address; `host:port`. | [wire-protocol.md](wire-protocol.md) |
| `MAHJONG_HEALTH_LISTEN_ADDR` | *(empty)* | If set, a *separate* host:port for `/health`. Empty means "same listener". | This spec § Health endpoint |
| `MAHJONG_DATA_DIR` | `./var/mahjong` | Root of `mahjong.db` + `records/`. Must be writable; created on first start if missing. | [sqlite-schema.md](sqlite-schema.md), [record-format.md](record-format.md) |
| `MAHJONG_SEAT_HOLD_SECONDS` | `60` | Grace window for a dropped player to reconnect before the seat is released. | [session-mux.md](session-mux.md) |
| `MAHJONG_HEARTBEAT_INTERVAL_SECONDS` | `30` | Server-to-client app-level heartbeat cadence. | [session-mux.md](session-mux.md), [wire-protocol.md](wire-protocol.md) |
| `MAHJONG_RESUME_BUFFER_SIZE` | `256` | Per-seat replay ring-buffer capacity. | [session-mux.md](session-mux.md) |
| `MAHJONG_SESSION_LIFETIME_HOURS` | `336` (14d) | Session token TTL. Sliding renewal extends it on every `RESUME`. | [auth.md](auth.md) |
| `MAHJONG_MAX_SPECTATORS_PER_TABLE` | `32` | Cap on simultaneous spectators per table. | [session-mux.md](session-mux.md) |
| `MAHJONG_DEFAULT_RULESET` | `mcr-2006` | Ruleset used when `CREATE_TABLE` omits `ruleset`. | This spec § Table registry |
| `MAHJONG_BOT_MANIFEST_DIR` | *(unset)* | If set, load bot manifests from this dir at startup. Unset = no auto-loaded bots (humans only). | [bot-runner-protocol.md](bot-runner-protocol.md) |
| `MAHJONG_SHUTDOWN_TIMEOUT_SECONDS` | `30` | Max time to spend draining on `SIGTERM` before exiting forcefully. | This spec § Graceful shutdown |
| `MAHJONG_WAL_CHECKPOINT_INTERVAL_SECONDS` | `300` | Periodic `wal_checkpoint(PASSIVE)` cadence. Bounds WAL growth without blocking writers. | This spec § Periodic tasks |
| `MAHJONG_LOG_LEVEL` | `INFO` | Standard Python logging level. | This spec § Logging |
| `MAHJONG_LOG_FORMAT` | `json` | `json` for production / journald; `console` for local dev. | This spec § Logging |

The default for `MAHJONG_DATA_DIR` is `./var/mahjong` (relative to CWD). On the deployment target (RPi 5), the systemd unit sets `WorkingDirectory=/var/lib/mahjong/` and `MAHJONG_DATA_DIR=/var/lib/mahjong/`, which collapses the path to the same absolute directory either way.

### Worked example — dev vs. prod

```bash
# Dev (macOS laptop)
$ python -m mahjong serve
# loads defaults: 127.0.0.1:8400, ./var/mahjong, INFO logging, json output to stdout

# Prod (RPi 5 via systemd; values pulled from EnvironmentFile=/etc/mahjong.env)
MAHJONG_LISTEN_ADDR=100.64.0.1:8400        # Tailscale tailnet IP
MAHJONG_DATA_DIR=/var/lib/mahjong
MAHJONG_SEAT_HOLD_SECONDS=120              # friends on phone hotspots — be lenient
MAHJONG_DEFAULT_RULESET=mcr-2006
MAHJONG_LOG_LEVEL=INFO
MAHJONG_LOG_FORMAT=json
```

## Startup sequence

The order matters: each step depends on its predecessors, and any failure here is fatal (exit code 1). No "best effort" startup — a server that can't open its DB or bind its socket has nothing useful to do.

```text
   1. load_config_from_env()           → ServerConfig (or fail with clear stderr msg)
   2. setup_logging(config)            → root logger configured
   3. ensure data_dir / records/       → mkdir -p; refuse if not writable
   4. open SQLite at db_path           → run pragmas (foreign_keys, journal_mode=WAL, busy_timeout)
   5. apply_migrations()               → bring schema to latest; create schema_version row if absent
   6. persistence.integrity_check()    → §"Startup integrity check" — fail on PRAGMA integrity_check != "ok"
   7. mark_in_progress_hands_aborted() → see §"In-flight at crash"
   8. load_rulesets(default_ruleset)   → MANIFEST.json + ruleset configs
   9. load_bot_registry(manifest_dir)  → optional; skipped if env unset
  10. construct TableRegistry          → empty map; not yet accepting
  11. bind WebSocket server            → listen on listen_addr; not yet accept()ing
  12. bind health endpoint             → /health on same listener (or separate per env)
  13. install SIGTERM/SIGINT handlers  → both signal the shutdown event
  14. start periodic tasks             → session cleanup, WAL checkpoint
  15. accept loop begins               → log "server.ready"
```

Each step logs a structured event on success and a structured error on failure. Failures at steps 1–6 exit with code 1 before any connections are served. Step 7 is non-fatal (logged warnings) — orphan in-progress rows are an expected outcome of an ungraceful shutdown, not a startup blocker.

### Startup integrity check

Wraps [persistence-api.md § Startup integrity check](persistence-api.md). Server behavior on each outcome:

- `PRAGMA integrity_check != "ok"` → **fatal**. SQLite file is corrupted. Log `db.corrupt`, exit code 1, operator restores from backup.
- `missing_files > 0` → **warning**. An indexed hand's record file is gone. Log per-file; the rows stay (queries still return the metadata; the FOOTER checksum check just doesn't run). Operator can rerun rebuild later.
- `orphaned_files > 0` → **warning**. JSONL files under `records/` not indexed. Auto-rebuild is *not* run at startup — operator runs `python -m mahjong rebuild-index` explicitly (a CLI added in S7). Default is to log and continue.
- `in_progress_hands > 0` → **warning**. Hands inserted via `reserve_hand` but never finalised. See §"In-flight at crash" below.

### In-flight at crash

When the server crashes (`SIGKILL`, OOM, power loss) mid-hand, the persistent state is:

- The `hand_index` row exists (inserted at HEADER write) but has `ended_at_ms = NULL`, `terminal_kind = NULL`, etc.
- The record file exists at `records/{year}/{month}/{hand_id}.jsonl` with HEADER + some events but no FOOTER.
- The four `hand_participants` rows exist with `final_score_delta = NULL`.

On next startup, step 7 reconciles these:

```python
def mark_in_progress_hands_aborted(persistence: Persistence) -> None:
    for hand in persistence.find_in_progress_hands():
        # Inspect the record file: does it have a TERMINAL event?
        record_path = persistence.data_dir / hand.record_path
        if not record_path.exists():
            log.warning("startup.in_progress_no_record_file",
                        hand_id=hand.hand_id, record_path=str(record_path))
            persistence.finalize_hand(
                hand.hand_id,
                ended_at_ms=now_ms(),
                terminal_kind="ABORTED",
                winner_seat=None,
                fan_total=None,
                record_checksum=None,
                participants_scores={s.seat: 0 for s in hand.participants},
            )
            continue

        # The record file exists. Use the rebuild logic to decide ABORTED vs. complete.
        recovery = persistence.recover_hand_from_record(record_path)
        log.warning("startup.in_progress_recovered",
                    hand_id=hand.hand_id, outcome=recovery.terminal_kind)
```

This *does not* attempt to resume gameplay. A client whose seat was in that hand sees the seat as unattached on reconnect; the partial record sits in the corpus marked `ABORTED`; the user's score for that hand is 0. Per [s2-s3-plan.md §10.9](../s2-s3-plan.md).

## Table registry

The in-memory map of live `TableManager` instances. Single-process, single-loop; no locking beyond the cooperative-async discipline of the event loop.

```python
class TableRegistry:

    def __init__(self, persistence: Persistence,
                 ruleset_registry: RulesetRegistry,
                 bot_registry: BotRegistry,
                 config: ServerConfig) -> None:
        self._tables: dict[str, TableHandle] = {}
        self._persistence = persistence
        self._rulesets = ruleset_registry
        self._bots = bot_registry
        self._config = config
        self._accepting_new = True   # flips False on shutdown drain

    async def create_table(self, *, owner_account_id: int,
                           ruleset: str, name: str | None = None) -> TableHandle:
        """Allocate a new TableManager. Owner is the account that called CREATE_TABLE.
        Raises ShuttingDown if !accepting_new."""
        ...

    def list_tables(self) -> list[TableSummary]:
        """Snapshot for LIST_TABLES wire message."""
        ...

    def get_table(self, table_id: str) -> TableHandle:
        """Raises TableNotFound."""
        ...

    async def close_table(self, table_id: str, *, reason: str) -> None:
        """End the current hand if one is running; detach all attached seats and spectators
        with reason; remove from _tables. Drains seat-mux state per session-mux.md."""
        ...

    async def drain_all(self, deadline_ms: int) -> None:
        """Shutdown path: refuse new CREATE_TABLE; wait for each TableManager's current hand
        to finish or hit the deadline."""
        self._accepting_new = False
        ...

    @property
    def accepting_new(self) -> bool:
        return self._accepting_new
```

A `TableHandle` bundles one `TableManager` with its `SessionMux` (per-table seat sessions + spectator set). The manager runs in a long-lived asyncio task; the session-mux is a passive object the wire layer calls into.

### Table lifecycle within the registry

```text
  CREATE_TABLE wire msg
        │
        ▼
  registry.create_table(...)
        │  (allocates table_id, instantiates TableManager + SessionMux,
        │   starts the manager's hand-orchestration task)
        ▼
   ┌─────────────────────────────────────────────┐
   │  Active table:                               │
   │  - players ATTACH via session-mux            │
   │  - manager runs hands sequentially:          │
   │      DEAL → DISCARD/CLAIM cycles → HU/DRAW   │
   │  - records written, hand_index rows finalised│
   │  - between hands: seats briefly UNBOUND;     │
   │    spectators persist                        │
   └──────────────────────┬──────────────────────┘
                          │
                          ▼
                    CLOSE_TABLE
                  (owner or admin, or
                   server drain reason)
                          │
                          ▼
                   registry.close_table()
                  (finishes current hand,
                   detaches everyone)
```

Tables do not persist across server restarts. Reading the `hand_index` rows lets clients see *historical* hands at this table-id, but the table-id itself is ephemeral; a restart drops all tables and clients reconnect against `LIST_TABLES` which is now empty.

That decision is the natural consequence of "in-memory connection state only" ([s2-s3-plan.md §10.9](../s2-s3-plan.md)) plus "the table manager runs the hand" (Layer 4). Persisting *which tables exist* across restart is a future feature; not needed for friends-and-family v1.

## Health endpoint

Path: `/health` on the WebSocket-serving host:port (or on `MAHJONG_HEALTH_LISTEN_ADDR` if set). HTTP/1.1 GET only; any other method returns 405.

```http
GET /health HTTP/1.1

HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ok","server_id":"mahjong-server-0.1.0","tables":3,"uptime_s":12504}
```

Status codes:

- `200` — the process is up, the DB is responsive (a `SELECT 1` succeeded under a 200ms deadline), the listener is accepting, and shutdown has not started. Standard healthy response.
- `503` — shutdown drain is in progress (`!table_registry.accepting_new`). Returns `{"status":"draining","drain_remaining_s": ...}`. Lets a load balancer stop sending new traffic during drain.
- `500` — the DB ping timed out or raised. Process is up but unhealthy. Operator should investigate; systemd `Restart=on-failure` would restart on this.

The endpoint does *not* require auth. It exposes only counts and version strings; no per-user data, no table-content data. The threat model ([auth.md § Threat model](auth.md)) considers the LAN trusted.

The endpoint is implemented as an `aiohttp`-style sub-route on the same listener the WebSocket uses — the `websockets` library supports passing an HTTP handler for non-upgrade requests. No second framework. (The "separate listener" path via `MAHJONG_HEALTH_LISTEN_ADDR` exists for the case where the WebSocket port isn't routable from the health-checker's network, but that's not the v1 default.)

## Graceful shutdown

Triggered by `SIGTERM` (systemd standard) or `SIGINT` (Ctrl-C in dev). Both set the same `shutdown_event`; the main coroutine awakens and runs the drain.

```text
  signal received
        │
        ▼
  shutdown_event.set()
        │
        ▼
  drain():
    1. server.stop_accepting()             ── no new WebSocket upgrades
    2. table_registry.accepting_new = False ── CREATE_TABLE → ERROR shutting_down
    3. for each table_id in registry:
         signal table_manager: finish_current_hand_and_stop()
       (table managers run their own drain — they finish the current turn,
        write FOOTER, finalize_hand; the hand they were *about* to start is skipped)
    4. session-mux drain:
         - every LIVE seat receives DETACH(reason=server_shutdown)
         - every HELD seat: outstanding prompt defaults or SeatError (per session-mux.md)
         - every spectator receives DETACH(reason=server_shutdown)
    5. wait up to shutdown_timeout_s for tasks to finish
    6. cancel periodic tasks (session cleanup, WAL checkpoint)
    7. persistence.wal_checkpoint(TRUNCATE)  ── compact WAL before close
    8. persistence.close()                   ── flushes, closes
    9. server.close()                        ── tears down listener
  return
```

Steps 1–4 are parallelisable per table (all tables drain at once via `asyncio.gather(...)`). Step 5 awaits all of them; step 6+ run after.

### Drain timeout escalation

If step 5 hits `shutdown_timeout_s` (default 30s) with tasks still pending:

1. Log `shutdown.timeout` with the per-table status.
2. Cancel each `TableManager`'s task with `task.cancel()`. The manager's `CancelledError` handler attempts a best-effort FOOTER write of the current hand (marks it `ABORTED` if it can't terminate cleanly).
3. Wait another 5 seconds for cleanup.
4. Whatever's still pending is abandoned. The records on disk are whatever state they're in; the index rows for unfinalised hands stay `NULL`-terminal; the next startup's `mark_in_progress_hands_aborted` picks them up and marks them `ABORTED`.

The drain-timeout escalation is the failure-handling path; the happy path completes long before. An MCR hand takes seconds to finish a turn; 30s is generous.

### Why finish the current hand, not the current match

A "match" is N hands (typically 16 for MCR); finishing the whole match on `SIGTERM` would block deploys for tens of minutes. Finishing the *current hand* is the right granularity:

- The hand is the atomic unit on disk (one JSONL per hand, finalized at FOOTER).
- The hand boundary is the only state where a client can cleanly re-attach (between hands, seats are briefly UNBOUND anyway per session-mux.md).
- A multi-hand match resumes naturally: clients reconnect after deploy, see the table still exists (if persisted across restart — not in v1) or rejoin a new table with the same `match_id` in the HEADER.

## Periodic tasks

Two long-lived asyncio tasks run for the lifetime of the server:

### Session cleanup

```python
async def periodic_session_cleanup(persistence: Persistence, config: ServerConfig) -> None:
    """Once per hour: delete expired session tokens. Bounded by config."""
    while True:
        await asyncio.sleep(3600)
        cutoff_ms = now_ms() - config.session_lifetime_hours * 3600 * 1000
        deleted = persistence.delete_expired_sessions(before_ms=cutoff_ms)
        log.info("sessions.cleanup", deleted=deleted)
```

Implements [persistence-api.md § Periodic tasks](persistence-api.md). Not load-bearing (a token-validation path also re-checks `expires_at_ms`); housekeeping to keep the table small.

### WAL checkpoint

```python
async def periodic_wal_checkpoint(persistence: Persistence, config: ServerConfig) -> None:
    """Every wal_checkpoint_interval_s: run PRAGMA wal_checkpoint(PASSIVE).
    Bounds WAL growth; non-blocking under WAL mode."""
    while True:
        await asyncio.sleep(config.wal_checkpoint_interval_s)
        pages, _ = persistence.wal_checkpoint(mode="PASSIVE")
        log.debug("db.wal_checkpoint", pages_checkpointed=pages)
```

SQLite's WAL grows monotonically until checkpointed. `PASSIVE` runs without blocking writers; the size cap is the natural OS-level limit. A `TRUNCATE` checkpoint at shutdown (drain step 7) collapses the WAL fully so a clean restart finds an empty WAL alongside the DB.

## Logging

Structured JSON to stdout, **and** (DEF-20, amended 2026-06-12) teed to a rotating file — `MAHJONG_LOG_FILE`, default `<data_dir>/logs/server.log`, 5 MB x 3 backups, always JSON-formatted; set the var to an empty string to disable. The original "the host's journal handles persistence" stance assumed a systemd deploy; in practice the server runs from a terminal whose scrollback dies with it, which made the 2026-06-12 hand-loop stall (FB-19) unattributable. Instrument-and-defer ledger rows are only grep-able if some file survives the process.

```python
# every log call is structured:
log.info("server.ready", listen=config.listen_addr, pid=os.getpid())
# → stdout:
# {"ts":"2026-06-01T12:00:00.123Z","level":"INFO","event":"server.ready",
#  "listen":"127.0.0.1:8400","pid":12345}
```

Fixed top-level keys: `ts` (ISO-8601 UTC with ms), `level`, `event`. All other keys are event-specific. Event names are `dotted.snake_case` and stable — they're effectively a public API (operators grep for them). New events get added; existing events don't rename their keys.

### Event taxonomy

A skeletal catalog. New events get added as the implementation grows.

| Event | Level | Fields |
| --- | --- | --- |
| `server.starting` | INFO | `config` (redacted) |
| `server.ready` | INFO | `listen`, `pid` |
| `server.shutting_down` | INFO | (none) |
| `server.exited` | INFO | `uptime_s`, `exit_code` |
| `db.migrate.applied` | INFO | `from_version`, `to_version` |
| `db.corrupt` | ERROR | `pragma_result` |
| `startup.in_progress_recovered` | WARN | `hand_id`, `outcome` |
| `connection.accepted` | INFO | `remote`, `connection_id` |
| `connection.auth` | INFO | `account_id`, `connection_id`, `outcome` (`ok`/`failed`) |
| `connection.closed` | INFO | `connection_id`, `reason`, `code` |
| `table.created` | INFO | `table_id`, `owner_account_id`, `ruleset` |
| `table.closed` | INFO | `table_id`, `reason` |
| `hand.started` | INFO | `table_id`, `hand_id`, `seed_prefix` |
| `hand.ended` | INFO | `table_id`, `hand_id`, `terminal`, `winner_seat`, `fan_total` |
| `sessions.cleanup` | INFO | `deleted` |
| `db.wal_checkpoint` | DEBUG | `pages_checkpointed` |
| `shutdown.timeout` | ERROR | `pending_tables`, `pending_connections` |

Secrets never appear in logs — no password hashes, no session tokens, no payload bodies of auth requests. Account *ids* (integers) are fine; usernames are fine; passwords are not.

Per [s2-s3-plan.md § "12-factor configuration"](../s2-s3-plan.md), `MAHJONG_LOG_FORMAT=console` switches to a colorized human-readable formatter for dev sessions. Production stays `json`.

## Concurrency model

One asyncio event loop. Everything is a coroutine on it. No threads beyond the asyncio default executor (used by SQLite if we ever did `run_in_executor` on a synchronous SQL call — we don't, in v1).

Per-component:

- **WebSocket acceptor:** one task per inbound connection. The connection task runs the auth handshake, then dispatches inbound messages and awaits outbound from the session-mux. Lives until the WebSocket closes.
- **`TableManager`:** one task per table, started at `create_table`. Runs the hand-orchestration loop ([seat-port.md § Lifecycle](seat-port.md)).
- **`SessionMux`:** no dedicated task. It's a passive state machine driven by the connection task (inbound) and the table manager (outbound `observe`/`decide`).
- **`Persistence`:** synchronous SQLite calls from any task. WAL means readers don't block writers. With one connection per process and one writer at a time, the synchronous calls are sub-millisecond at our scale.
- **`/health`:** the `websockets` library serves it on the same listener. No dedicated task.
- **Periodic tasks:** the two long-running tasks described above. Cancelled at shutdown.

This matches [seat-port.md § Lifecycle and concurrency model](seat-port.md)'s "one asyncio event loop owns the whole server" rule.

## Multi-table interaction with persistence

Each `TableManager` writes its own records and triggers its own `reserve_hand` / `finalize_hand` calls. The single shared `Persistence` instance serialises these via the SQLite connection — SQLite handles one writer at a time, and the WAL means any concurrent readers (replay code) don't block.

There is no cross-table state that needs locking:

- `accounts` and `sessions` rows are accessed by the auth path on connection-accept; the table managers don't touch them.
- `hand_index` and `hand_participants` rows are written by the table that owns the hand; reads are by hand_id, never racing a concurrent write of the same row.

The "two tables run simultaneously" fixture (8.4 in [s2-s3-plan.md](../s2-s3-plan.md)) verifies that a mutation on table A's state doesn't leak into table B's records or rows.

## Exit codes

- `0` — normal exit via `SIGTERM` / `SIGINT` after successful drain.
- `1` — startup failure (config invalid, DB open failed, migration failed, port bind failed, integrity check failed fatally).
- `2` — runtime fatal error (rare; logged). Distinct from `1` so the host can tell startup-failed-fast from running-then-died.
- `137` (= `128 + 9`) — `SIGKILL`'d by host (OOM, manual). Server didn't observe this; logged by the host.

## Alternatives considered

- **Config file instead of env vars.** A `mahjong.toml` next to the binary. Rejected: 12-factor is the standard for containers / systemd / journald-friendly services. Env vars are how every modern Python web service is configured. The discoverability hit ("what knobs exist?") is mitigated by the variable-reference table above and a future `python -m mahjong serve --print-config` flag (additive, not v1).
- **Separate process per table.** A `multiprocessing.Pool` or `asyncio` subprocess per table. Rejected for v1: complexity not paid for at our scale. The single-loop model handles dozens of tables comfortably; we can split when measurements show contention. Also, the cross-process IPC for "auth and persistence are shared" is the hard part — solving it before there's a load problem is YAGNI.
- **No `/health` endpoint; rely on TCP connect.** Simpler: systemd can `tcp:8400` probe. Rejected: a TCP connect doesn't catch "DB hung, can't serve auth requests." The `SELECT 1` in `/health` catches that case. Cheap insurance.
- **Two-port topology: WebSocket on 8400, HTTP `/health` on 8401.** Cleaner separation. Rejected as the v1 default (the env var `MAHJONG_HEALTH_LISTEN_ADDR` enables it for ops who want it); the WebSocket library cleanly accepts HTTP requests on the same listener via its router hook, and one port is one fewer firewall hole.
- **Block `SIGTERM` until a hand naturally completes; no timeout.** Friendlier to in-progress hands. Rejected: a deploy that's blocked indefinitely by a slow hand is operationally painful; the 30s timeout balances "finish naturally if possible" with "exit eventually." A manual `SIGKILL` is always available as the escape hatch.
- **Restart in-flight hands across server restart.** "The user was about to discard; resume the hand at that exact state." Rejected per [s2-s3-plan.md §10.9](../s2-s3-plan.md). Would require persisting the full `GameState` (not just the record) to SQLite at every transition; that's a different persistence model. The "lose the hand on crash" cost is small at our scale (rare crashes; one ABORTED hand isn't a big deal).
- **Auto-rebuild on startup when `orphaned_files > 0`.** Tempting. Rejected: an automatic rebuild on every startup could mask a real bug (records being written but never indexed). Manual `python -m mahjong rebuild-index` is the explicit operator action. The integrity check still surfaces the count.
- **In-process metrics endpoint (`/metrics` Prometheus-style).** Future-friendly. Rejected for v1; `/health` is enough. When there's something to alert on (request rate, table-active count), a `/metrics` endpoint is additive.
- **`SIGHUP` reload.** Edit env vars and `kill -HUP` to apply. Rejected: complexity for a hobby-scale server. `systemctl restart mahjong` does the same thing with a clean drain.
- **A single binary `python -m mahjong` dispatcher that selects mode by argv.** Already there (`play-test`, `selfplay`, soon `serve` and `rebuild-index`). Pinning: the `serve` subcommand has no positional arguments and no `argparse` flags — all configuration is env. Subcommands that take flags (selfplay) keep theirs.

## Verification fixtures

Acceptance criteria for impl step 8.5 (server lifecycle). The end-to-end S3 exit fixture (8.6) integrates these.

1. **Defaults load cleanly.** With no `MAHJONG_*` env vars set, `load_config_from_env()` returns a `ServerConfig` with every documented default. No unset / `None` fields beyond `bot_manifest_dir` and `health_listen_addr`.

2. **Required-var validation.** `MAHJONG_SEAT_HOLD_SECONDS=banana` raises `ConfigError` with the offending var name. Same for `MAHJONG_LISTEN_ADDR=not-a-host-port`. Server refuses to start.

3. **Unknown `MAHJONG_*` var logs a warning.** Setting `MAHJONG_HARTBEAT_INTERVAL_SECONDS=30` (typo) logs `config.unknown_var` at WARN level on startup and continues with defaults.

4. **Startup happy path.** Empty `MAHJONG_DATA_DIR`, no DB present: server creates `mahjong.db`, runs migrations to latest, starts accepting on the listen address. `/health` returns 200 within 1 second of startup. Log sequence matches the expected event ordering (`server.starting → db.migrate.applied → server.ready`).

5. **Startup with existing DB.** A pre-populated `mahjong.db` at schema version N (the current latest): server opens it, integrity check passes, no migrations applied, server.ready logged. Skipped if no future migrations exist yet (we're at v1).

6. **Corrupt DB exit.** A `mahjong.db` whose `PRAGMA integrity_check` returns anything but `"ok"`: server logs `db.corrupt` at ERROR, exits with code 1, prints a helpful stderr line ("DB file at $path is corrupted — restore from backup").

7. **Port bind failure.** Another process holding `MAHJONG_LISTEN_ADDR`: server logs `transport.bind_failed` at ERROR, exits with code 1, message includes the address that failed.

8. **In-flight hand at startup is marked ABORTED.** Setup: a `hand_index` row with `terminal_kind = NULL` from a previous "crash". On startup, the row is finalised with `terminal_kind = "ABORTED"` and a warning is logged. The participants' `final_score_delta` are set to 0.

9. **`/health` returns 200 normally.** With the server running, GET `/health` returns 200 with the documented JSON body. Body parses; `status == "ok"`; `server_id` matches `config.server_id`; `tables` is the count from `TableRegistry`; `uptime_s` is monotonically increasing across two consecutive probes.

10. **`/health` returns 503 during drain.** Setup: send `SIGTERM`, then within 1s GET `/health`. Asserts 503 with `status == "draining"`. After drain completes (or timeout), the listener is closed so further GETs error at the socket level.

11. **`/health` returns 500 on DB stall.** Setup: monkeypatch `persistence.ping` to raise / hang past its deadline. GET `/health` returns 500 with `status == "unhealthy"` and `reason` includes "db".

12. **`SIGTERM` drains a LIVE hand.** Setup: spin up server, scripted client attaches to a table, hand begins, send `SIGTERM` mid-turn. Assert: server completes the current `decide` (the client's `ACTION` is accepted), the table manager writes the FOOTER, `finalize_hand` is called, the WebSocket sends `DETACH(reason=server_shutdown)`, the process exits 0 within drain timeout. The record file is FOOTER-complete; the `hand_index` row is finalised; the eval-summary aggregator can read the hand cleanly.

13. **`SIGTERM` rejects new connections during drain.** Setup: send `SIGTERM`; while drain is in progress, a new WebSocket upgrade attempt either receives `ERROR { code: "shutting_down" }` (if upgraded already) or is rejected at the HTTP-upgrade layer (503).

14. **Drain timeout escalates to cancel.** Setup: `MAHJONG_SHUTDOWN_TIMEOUT_SECONDS=1`. Construct a scenario where the table manager is stuck waiting for a `decide` that will never resolve (e.g. a hung bot). Send `SIGTERM`. Assert: after 1 second, the table manager's task is cancelled; the process exits within ~6 seconds total (1 + 5 cleanup buffer); the in-flight hand is finalised as `ABORTED` *either now or on next startup* (both are acceptable; fixture pins which based on impl).

15. **Drain runs WAL checkpoint TRUNCATE.** Setup: server has been running, several hands written, WAL file is non-empty. Send `SIGTERM`; after drain, assert the WAL file is zero-length or absent. Confirms drain step 7 ran before close.

16. **Crash recovery (`SIGKILL`).** Setup: spin up server, start a hand mid-discard, `SIGKILL` the process. Verify on disk: record file has HEADER + some events but no FOOTER; `hand_index` row exists with NULL terminals. Restart server; assert the row is auto-finalised as `ABORTED` (fixture 8) and the record file is left untouched (still HEADER-no-FOOTER — operator can run `rebuild-index` later if they want the orphan reclaimed).

17. **Multi-table isolation.** Setup: create table A and table B in the same server. Have clients play hands on both concurrently. Assert: their records are written to disk in independent files; their `hand_index` rows have distinct `hand_id`s; closing table A does not affect table B; `LIST_TABLES` correctly omits A after close.

18. **`CREATE_TABLE` rejected after drain begins.** Setup: send `SIGTERM`; during drain, a still-connected client (admin) sends `CREATE_TABLE`. Assert: response is `ERROR { code: "shutting_down" }`. State unchanged.

19. **Periodic session cleanup runs.** Setup: insert two sessions, one expired (`expires_at_ms < now`) and one fresh. Patch `asyncio.sleep` to advance the clock to the cleanup tick. Assert: the expired session is deleted; the fresh one remains; `sessions.cleanup` log emitted with `deleted=1`.

20. **Periodic WAL checkpoint runs.** Setup: write enough hands to grow the WAL. Patch the sleep. Assert: `PRAGMA wal_checkpoint(PASSIVE)` is called; `db.wal_checkpoint` log emitted.

21. **Structured logging emits valid JSON.** Setup: capture stdout while running the server through startup → one hand → shutdown. Assert: every line is valid JSON; every line has `ts`, `level`, `event`; no line contains a password, hash, or session token (regex check against `password|token|hash:\s*[a-zA-Z0-9]{8,}`).

22. **End-to-end S3 exit fixture.** Spec 8.6, included here because it integrates 8.5: spin up server with a fresh data dir → admin creates an account via direct DB insert → client logs in → joins a new table → plays a hand against three CannedAdapters → hand records → server `SIGTERM` → restart → `find_hands_by_account(account_id)` returns the played hand. This is the **S3 exit gate**.

Fixture 12 (SIGTERM-mid-hand graceful) and fixture 16 (SIGKILL recovery) are the load-bearing ones — together they pin "deploys don't corrupt state" and "crashes don't corrupt state," which are the operational invariants S3 is in service of.

## Open questions

None at v1. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md). Possible v2 considerations (`SIGHUP` reload, `/metrics` endpoint, persist-tables-across-restart, per-table-config knobs) live in [research-ideas.md](../research-ideas.md) if surfaced; none gate S3 ship.
