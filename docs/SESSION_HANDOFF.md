# Session handoff — 2026-05-22

Snapshot of S2 + S3 preparation status. Read this to pick up where the prior session left off.

## Where we are

Layers 0–6 (engine through self-play harness) are complete and committed. The next milestones from [server-plan.md](server-plan.md) are **S2** (TUI + WebSocket) and **S3** (accounts, sessions, SQLite persistence).

Per the working agreement, we are loading the design work up front so implementation is mechanical:

1. **Plan-of-specs first** ([s2-s3-plan.md](s2-s3-plan.md)).
2. **Tier-1 and tier-2 specs** ([specs/](specs/)) — pin every interface before any code.
3. **Implementation-order + checklist** ([specs/implementation-order.md](specs/implementation-order.md), [../CHECKLIST.md](../CHECKLIST.md)) — append Layer 7 + Layer 8 sections in the existing format.
4. **Then implement** starting at sub-step 7.0 (state-schema amendment for `seat=None` public projection).

## What landed in this session

Seven artifacts written under [docs/](.):

| File | Tier | Purpose |
| --- | --- | --- |
| [s2-s3-plan.md](s2-s3-plan.md) | plan | Plan-of-specs. Pinned nine decisions in §10 (transport, migrations, multi-table, bot auth, seat-hold, hand_id, versioning, restart state, spectator). |
| [specs/wire-protocol.md](specs/wire-protocol.md) | 1 | WebSocket message contract. HELLO/AUTH/RESUME/LIST_TABLES/ATTACH/EVENT/PROMPT/ACTION/HAND_END/SPECTATE/STOP_SPECTATING/CREATE_TABLE/CLOSE_TABLE. 16 verification fixtures. |
| [specs/session-mux.md](specs/session-mux.md) | 1 | Connection ↔ seat state machine (LIVE/HELD/UNBOUND) + spectator-set bookkeeping. Per-seat ring buffer for reconnect replay. 21 verification fixtures. |
| [specs/tui-client.md](specs/tui-client.md) | 2 | Textual app architecture: LoginScreen, LobbyScreen, TableScreen (PlayerView + SpectatorView), HandEndModal. Headless `Pilot` tests. Bilingual EN/ZH. 17 fixtures. |
| [specs/sqlite-schema.md](specs/sqlite-schema.md) | 2 | Tables: `schema_version`, `accounts`, `sessions`, `hand_index`, `hand_participants`. Hand-rolled migrations under `mahjong/persistence/migrations/`. 12 fixtures. |
| [specs/auth.md](specs/auth.md) | 2 | Argon2id (m=64MiB, t=3, p=4). Session tokens `s_<32 hex>`. Static-invalid-hash timing defense. Bots auth same as humans. 17 fixtures. |
| [specs/persistence-api.md](specs/persistence-api.md) | 2 | `Persistence` class wrapping SQLite. `reserve_hand` / `finalize_hand` hooks. Rebuild-from-records path for crash recovery. 13 fixtures. |

## What remains for next session

In priority order (no dependencies between them after the spec, so you can do them sequentially or batch):

1. **Draft [specs/server-lifecycle.md](specs/server-lifecycle.md)** (tier-2). The last new spec. Should cover:
   - `python -m mahjong serve` entry point.
   - Env-var configuration (`MAHJONG_DATA_DIR`, `MAHJONG_LISTEN_ADDR`, `MAHJONG_SESSION_LIFETIME_HOURS`, `MAHJONG_SEAT_HOLD_SECONDS`, `MAHJONG_HEARTBEAT_INTERVAL_SECONDS`, `MAHJONG_MAX_SPECTATORS_PER_TABLE`, `MAHJONG_RESUME_BUFFER_SIZE`).
   - Startup flow: open DB → migrations → `persistence.integrity_check()` → janitor for in-progress hands → start WebSocket server → ready.
   - Graceful shutdown on SIGTERM: stop accepting attaches, drain session-mux (LIVE → DETACH, HELD → resolve), let table managers finish current turn, flush records, close DB, exit. Drain timeout default 30s.
   - `/health` endpoint (a wire-protocol `HEALTH` message? or a separate HTTP probe on a side port? — propose both and pick one).
   - Multi-table orchestration (the `{table_id: TableManager}` dict from [s2-s3-plan.md §10.3](s2-s3-plan.md)).
   - Periodic tasks: nightly `delete_expired_sessions`.
   - Verification fixtures (target ~10–12).
2. **Update [specs/README.md](specs/README.md)** — add the seven new specs to the tier-1 and tier-2 tables.
3. **Append Layer 7 + Layer 8 to [specs/implementation-order.md](specs/implementation-order.md)** — per the format in §5 of [s2-s3-plan.md](s2-s3-plan.md). Step IDs 7.0 through 7.6 for S2, 8.1 through 8.6 for S3.
4. **Append Layer 7 + Layer 8 to [../CHECKLIST.md](../CHECKLIST.md)** — mirror the Layers 0–6 format with tests-first checkboxes, deliverables, and Gate per step.

After (4) lands, implementation begins at **sub-step 7.0** (state-schema amendment + `project(state, seat=None)` implementation + fixture refresh) — see [s2-s3-plan.md §4 Layer 7](s2-s3-plan.md).

## Pinned decisions — do not re-litigate

From [s2-s3-plan.md §10](s2-s3-plan.md):

1. Transport: WebSocket (subprotocol `mahjong-v1`).
2. Migrations: hand-rolled `schema_version` table.
3. Multi-table: N independent `TableManager` instances.
4. Bot auth: same flow as humans.
5. Seat-hold: `MAHJONG_SEAT_HOLD_SECONDS=60` env var (server-global).
6. `hand_id`: UUIDv7 string (matches record-format.md).
7. Protocol versioning: integer `protocol_version`.
8. Restart state: in-memory only; SQLite persists session tokens.
9. Spectator: first-class from v1. Separate `SPECTATE`/`SPECTATING` flow, max 32/table, no seat-hold timer.

## The one cross-layer change

[state-schema.md § Per-seat projection](specs/state-schema.md) needs a tiny additive amendment: broaden `project(state, seat: int) -> SeatView` to `project(state, seat: int | None) -> SeatView`, where `seat=None` yields the public-only spectator projection. This is the only edit to a previously-locked tier-1 spec; it lands as sub-step 7.0 before any new code. The amendment is *additive in spirit* — state-schema.md's intro already names the spectator view as one of the targets of projection ([state-schema.md:12](specs/state-schema.md#L12)) — the signature just hadn't caught up.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [s2-s3-plan.md](s2-s3-plan.md) (full).
- [ ] Skim the six new specs to refresh the shape.
- [ ] Draft server-lifecycle.md.
- [ ] Update README.md.
- [ ] Append impl-order.md sections.
- [ ] Append CHECKLIST.md sections.
- [ ] Run verification ladder on the new docs (ruff/mypy don't apply to markdown, but make sure links resolve — `mkdocs serve` if installed, or eyeball).
- [ ] Commit. Suggested message: "Layer 7+8 spec preparation: pin S2 and S3 contracts".
- [ ] **Then** start implementation at sub-step 7.0.
