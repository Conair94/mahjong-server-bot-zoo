# S2 + S3 preparation plan

This is the plan-of-specs that bridges the engine work (Layers 0–6, done 2026-05-22) and a server that friends can log into and play on (S2 + S3 in [server-plan.md](server-plan.md)).

**Status:** open questions in §10 resolved 2026-05-22; spec drafting in progress per §6.

This document does not itself pin any contract. It names the contracts that must be pinned, in what order, with which dependencies, and gates each spec on a small set of open questions that should be answered *here* and *now* — not while drafting.

## 1. What we ship at the end of this work

**S2 exit (server-plan.md §"S2 — TUI client + WebSocket transport"):** a scripted TUI session (canned keystrokes → human seat adapter → engine) produces a server-side record matching a checked-in fixture. WebSocket connect / disconnect / reconnect within the seat-hold window preserves the game; reconnect after timeout produces the documented auto-pass behavior.

**S3 exit (server-plan.md §"S3 — accounts, sessions, persistence"):** migration test green from fresh DB and from previous schema; persistence round-trip identical (write record + index → restart → read back); auth tests green (argon2 round-trip, session token lifecycle, no account-existence leak on failed login); multi-table fixture green (two concurrent tables don't share state).

**Combined deliverable:** a single process you can run with `python -m mahjong serve`, point three friends' TUIs at over Tailscale, and have them play a hand whose record gets indexed in SQLite and replays byte-identically.

## 2. What we already have

- **Layers 0–6** ([CHECKLIST.md](../CHECKLIST.md)): engine, records, adapters, table manager, bot runner, self-play harness. All deterministic and fixture-gated.
- **Tier-1 specs locked:** [state-schema.md](specs/state-schema.md), [record-format.md](specs/record-format.md), [seat-port.md](specs/seat-port.md), [bot-runner-protocol.md](specs/bot-runner-protocol.md), [determinism.md](specs/determinism.md).
- **Tier-2 specs locked:** [engine-api.md](specs/engine-api.md), [selfplay-harness.md](specs/selfplay-harness.md).

The seat-port already nominates a `human` `SeatIdentity` variant ([seat-port.md § Data shapes](specs/seat-port.md)). That's the seam Layer 7 plugs into; we do not modify the seat-port to add S2.

## 3. What we deliberately do not build in this pass

Tracking server-plan.md phase boundaries:

- **HTTPS / Caddy / Let's Encrypt.** Tailscale or localhost is enough through S3.
- **Email verification, password reset flow.** Admin (you) resets passwords directly.
- **Analysis overlays.** S4. The wire-protocol must leave room for overlay messages to be added additively, but no overlay code is written here.
- **Home-rule overlays beyond what's already in `RuleSetRef`.** S5.
- **Opponent-aware overlays.** S6, AI-plan-gated.
- **systemd unit, log rotation, backup runbook.** S7 (Linux-only; see [project_hosting_target.md](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_hosting_target.md)). A skeletal `server-lifecycle.md` covers what the *process* must do (graceful shutdown, env config); the systemd unit and the host-side ops live in S7.
- **Permanent always-on spectator table.** S8. (Note: *spectating* itself is in scope for v1 — see Layer 7. S8 is specifically the always-running bot-vs-bot table with a default spectator view, which depends on an AI-plan bot being ready.)
- **Chat.** server-plan.md open question. Defer until S3 ships per its own note; we leave a non-action event slot in the wire-protocol so adding it is additive.

## 4. New layers

### Layer 7 — networking + human adapter (S2)

The transport + the `SeatAdapter` implementation that bridges a connected human to the table manager.

**Specs to write:**

| # | Spec | Tier | Pins | Consumed by |
| - | - | - | - | - |
| 10 | `wire-protocol.md` | 1 | The WebSocket message contract: framing, message types (player + spectator), error model, version handshake, reconnect token format. Public-vs-private field split mirrors the seat-port's `SeatView` privacy rule. | TUI client, session-mux, `HumanAdapter`, any future non-TUI client. |
| 11 | `session-mux.md` | 1 | Connection ↔ seat binding lifecycle (attach, detach, reconnect, hold-window timeout, auto-pass substitution) and spectator-set bookkeeping (subscribe, immediate drop, max-per-table). Pins the state machine the table manager observes when a human seat's connection comes and goes. | Server process, `HumanAdapter`, table manager (consumes substitution events). |
| 12 | `tui-client.md` | 2 | Textual app architecture: screen layout, input bindings, rendering pipeline over `SeatView`s, spectator view, plain-mode-only constraint. | TUI client only (single consumer; tier-2). |

**Prerequisite amendment to an existing spec:** [state-schema.md § Per-seat projection](specs/state-schema.md) broadens `project(state, seat: int) -> SeatView` to `project(state, seat: int | None) -> SeatView`, where `seat=None` yields the public-only "spectator" projection (every `concealed` empty, own-draw `tile` field elided, concealed-meld tile elided). This is the only edit to a previously-locked spec; it lands as sub-step 7.0 before any new code, with a refreshed projection-privacy fixture (state-schema.md fixture 3 generalised to include `seat=None`) and the existing 6 callsites under [mahjong/engine/](../mahjong/engine/) updated. The corresponding [mahjong/engine/state.py:117](../mahjong/engine/state.py#L117) signature is widened in the same change.

**Implementation steps (full breakdown lands in [implementation-order.md](specs/implementation-order.md) after specs are signed off):**

- 7.0 — state-schema.md public-view amendment + `project(state, seat=None)` implementation + fixture refresh (prerequisite for everything below).
- 7.1 — wire-protocol codec (encode/decode every message including SPECTATE/SPECTATING; round-trip fixture per type).
- 7.2 — WebSocket server (accept, frame, route to session-mux).
- 7.3 — session-mux (attach/detach/reconnect state machine + spectator set; produces `HumanAdapter` per attached seat).
- 7.4 — `HumanAdapter` (the `SeatAdapter` impl bridging session-mux to seat-port).
- 7.5 — TUI client (Textual app consuming wire-protocol; player and spectator screens).
- 7.6 — end-to-end fixture: scripted keystrokes → server → engine → record byte-identical to canned fixture; spectator-subscription fixture verifying public-only projection.

**S2 exit gate:** the scripted-keystroke fixture from server-plan.md §S2 lands as a check-in. The connect/disconnect/reconnect test passes. `python -m mahjong serve` + four scripted TUIs play a hand whose record replays byte-identically.

### Layer 8 — accounts, sessions, persistence (S3)

Adds SQLite, auth, multi-table.

**Specs to write:**

| # | Spec | Tier | Pins | Consumed by |
| - | - | - | - | - |
| 13 | `sqlite-schema.md` | 2 | Tables (`accounts`, `sessions`, `game_index`), indexes, constraints, migration mechanics, `schema_version` semantics. | auth, persistence-api, server-lifecycle (DB open/close), tests. |
| 14 | `auth.md` | 2 | argon2 parameters, password hashing round-trip, session token issuance/validation/expiry/revocation, bot-account auth path, failed-login policy (no account-existence leak). | session-mux (login on attach), wire-protocol (auth handshake messages), persistence-api. |
| 15 | `persistence-api.md` | 2 | Query layer over SQLite + record files: write game-index row on hand-end, look up games by player, restore mechanics (rebuild index from records). | Table manager (hand-end hook), server-lifecycle (startup integrity check), future overlay/stats code. |
| 16 | `server-lifecycle.md` | 2 | Process startup, env-var config (`MAHJONG_DATA_DIR`, `MAHJONG_LISTEN_ADDR`, …), graceful `SIGTERM` shutdown (finish current turn, flush records, close DB), `/health` endpoint, multi-table orchestration. | Server entry point (`python -m mahjong serve`), tests. |

**Implementation steps (preview):**

- 8.1 — SQLite schema + migrations (apply on fresh and on previous-version DB; both tested).
- 8.2 — auth module (argon2 hashing, session tokens, login/logout endpoints in wire-protocol).
- 8.3 — persistence API (game-index writer wired to table-manager hand-end; query helpers).
- 8.4 — multi-table orchestrator (refactor: one process holds N independent table managers).
- 8.5 — lifecycle (env config, graceful shutdown, `/health`, signal handling).
- 8.6 — end-to-end fixture: create account → log in → join table → finish hand → query game by player.

**S3 exit gate:** the four bullets in server-plan.md §S3 exit criteria are all green and checked in.

## 5. Layer dependency order

Layer 7 and Layer 8 are independent in code (a bot-only multi-table server could ship from Layer 8 alone). The natural sequencing is **7 → 8** because:

- S2 (Layer 7) is the smaller, contained piece — local play, no auth.
- Layer 8's multi-table refactor (8.4) is much easier to validate when a real client (the TUI from 7.5) can drive a second table independently.

But the *specs* all land **before any implementation begins** — that's the working agreement ("plans are the source of truth"). A refactor surfacing in Layer 7 spec drafting could change the auth handshake shape in Layer 8, and we want that ripple to happen on paper, not in code.

## 6. Spec writing order

1. **`wire-protocol.md`** — gates everything in Layer 7. Also forces the auth-handshake shape decision (§10), which gates `auth.md`.
2. **`session-mux.md`** — pins the reconnect lifecycle that `auth.md` (token rebinding) and `persistence-api.md` (record file lifecycle on disconnect) must respect.
3. **`tui-client.md`** — depends on wire-protocol.
4. **`sqlite-schema.md`** — foundation for auth + persistence-api.
5. **`auth.md`** — depends on sqlite-schema + wire-protocol.
6. **`persistence-api.md`** — depends on sqlite-schema.
7. **`server-lifecycle.md`** — depends on persistence-api (shutdown must flush records and close DB cleanly) and session-mux (shutdown must drain attached sessions).

After all seven land, [implementation-order.md](specs/implementation-order.md) and [CHECKLIST.md](../CHECKLIST.md) get Layer 7 + Layer 8 sections appended in the existing format (step → spec citation → fixture list → gate).

## 7. Workflow per step (restated for emphasis)

This is the same global rule from [CLAUDE.md](../CLAUDE.md) and [implementation-order.md](specs/implementation-order.md), restated because the *combination* of TUI + WebSocket + SQLite is the part of the project most likely to invite "I'll write the test after" drift.

1. Re-read the spec section the step cites.
2. Write the listed fixtures **first** — the failing test is the design artifact.
3. Implement the smallest thing that turns them green.
4. Run the verification ladder (format → lint → type-check → unit → integration).
5. Don't tick the step until its Gate passes.

For S2/S3 specifically: TUI tests are headless (Textual ships a pilot driver — `app.run_test()`). Wire-protocol tests are codec round-trips, no socket. SQLite tests run against `:memory:` for unit, against a temp file for migrations. Real-socket and real-disk runs are integration tests, gated on the unit tier being green.

## 8. New conventions named inline (per global "teach as you go")

These show up in the upcoming specs; calling them out so the rationale is visible:

- **Ports and adapters.** Already in play (seat-port.md). The HumanAdapter is just a new adapter; the engine and table manager don't change shape.
- **Optimistic-concurrency / monotonic sequence numbers.** Used in wire-protocol message ordering and SQLite session-token rotation. Why it exists: lets the server reject stale or replayed messages without holding locks.
- **Schema migrations.** Mentioned in server-plan.md; pinned in sqlite-schema.md. Why it exists: lets the DB schema evolve without destructive `DROP TABLE` between releases. Even at hobby scale, having migrations means tomorrow's schema change isn't blocked by yesterday's prod data.
- **Argon2 (winner of the 2015 Password Hashing Competition).** Used in auth.md. Why it exists: memory-hard hashing resists GPU-cracking in ways bcrypt and PBKDF2 don't.
- **Graceful shutdown / drain pattern.** Used in server-lifecycle.md. Why it exists: SIGTERM stops *new* work, lets *in-flight* work finish, *then* exits — so a deploy doesn't corrupt an in-progress hand.
- **12-factor configuration.** Used in server-lifecycle.md. Why it exists: config via env vars (not files) means the same binary runs in dev / prod without code branches.
- **Tracer-bullet client.** The TUI is written to be the *simplest* thing that exercises the full stack end-to-end (login → seat → discard → terminal), not the prettiest. Aesthetics come in S4 (overlays).

## 9. Verification artifacts per layer

(Per [feedback_tdd_and_rl_verification.md](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_tdd_and_rl_verification.md) — "no learning claim without a verification artifact" generalises to "no working-server claim without a verification artifact.")

| Layer | Artifact |
| - | - |
| 7.1 codec | Round-trip fixture per message type; corrupted-frame rejection fixture. |
| 7.2 server | Connect → ping/pong → close fixture; framing-error fixture. |
| 7.3 session-mux | State-machine fixture covering attach, detach-within-hold, detach-past-hold, reconnect, replace. |
| 7.4 HumanAdapter | `seated/observe/decide/left` round-trip against a fake connection. |
| 7.5 TUI | `app.run_test()` scripted-keystroke fixture; output snapshot per screen. |
| 7.6 end-to-end | The S2 exit fixture from server-plan.md §S2: scripted four-seat hand, byte-identical record. |
| 8.1 schema | Migration applies on fresh + on prev-version; both verified. |
| 8.2 auth | Argon2 round-trip; token lifecycle; failed-login negative test (no leak). |
| 8.3 persistence | Hand-end → row written → query returns row; record file path resolvable. |
| 8.4 multi-table | Two-table fixture; mutation on table A leaves table B's state and record unchanged. |
| 8.5 lifecycle | SIGTERM-mid-hand fixture: hand finishes, record flushed, DB closed cleanly; subsequent process can resume the recorded hand for replay. |
| 8.6 end-to-end | The S3 exit fixture from server-plan.md §S3: account → login → join → play → query. |

## 10. Resolved decisions (2026-05-22)

These were the load-bearing open questions before spec drafting. All nine are now pinned; each spec drafts from these as fixed premises.

1. **Transport: WebSocket.** Server speaks WebSocket on a TCP port; TUI client connects via the `websockets` library. SSH-served Textual is not pursued (worse separation of client/server, harder for non-SSH clients).
2. **Migrations: hand-rolled `schema_version` table.** ~30 lines of code; a `schema_version` row + a list of versioned migration scripts under `mahjong/persistence/migrations/`. Zero dependencies. Easy to swap to Alembic later if scale demands it.
3. **Multi-table: N independent `TableManager` instances.** Server holds `{table_id: TableManager}`; each table is the same per-hand `TableManager` Layer 4 already builds. No multi-table-aware refactor of `TableManager` itself.
4. **Bot auth: same-flow-as-humans.** Bots get an `accounts` row with `kind = 'bot'`; bot-runner logs in with credentials before the subprocess starts. Uniform auth surface; matches server-plan.md.
5. **Reconnect seat-hold window: server-global env var.** `MAHJONG_SEAT_HOLD_SECONDS=60` (default). Per-table override is YAGNI for v1.
6. **Hand-record path: `records/{year}/{month}/{hand_id}.jsonl`** — already locked by [record-format.md § File layout](specs/record-format.md). SQLite mirrors this; the primary key in the index table is `hand_id`, not a separate integer. (Pre-draft, I'd picked integer; reading record-format.md showed it was already pinned as a UUIDv7 string. The locked spec wins.)
7. **`hand_id` type: UUIDv7 string** (per [record-format.md](specs/record-format.md)). Timestamp-prefixed, lexicographically sortable, globally unique without coordinating with the DB. SQLite stores it as `TEXT PRIMARY KEY`. The "auto-increment integer" option from my pre-draft is dropped.
8. **Wire-protocol versioning: integer `protocol_version`** on the HELLO message. Mirrors the bot-runner-protocol's shape.
9. **Server state on restart: in-memory connections only; SQLite persists session tokens.** A restart drops every WebSocket; clients reconnect by presenting their session token. Session-mux state rebuilds from those reconnects. No "preserve attached-connection state across restart" machinery.

Spec drafting begins at `wire-protocol.md` in the §6 order.

## 11. Where this lands

- This plan: `docs/s2-s3-plan.md` (sibling to `server-plan.md` and `ai-plan.md`).
- New specs: `docs/specs/{wire-protocol,session-mux,tui-client,sqlite-schema,auth,persistence-api,server-lifecycle}.md`.
- The existing [docs/specs/README.md](specs/README.md) table gets the new specs appended in the listed tiers.
- The existing [docs/specs/implementation-order.md](specs/implementation-order.md) gets Layer 7 + Layer 8 sections appended.
- The existing [CHECKLIST.md](../CHECKLIST.md) gets Layer 7 + Layer 8 trackers appended in the same format as Layers 0–6.

Nothing in the existing docs is rewritten — this is purely additive on top of what's already there.
