# Implementation order

The bottom-up dependency-ordered build sequence that gets us from empty repo to the S0 walking skeleton, then to S1 (Botzone bot integration), then to the self-play harness. Every step references the spec it implements and the fixtures that gate its completion.

This document is *tactical*: how to slice the work within the high-level S0/S1/… phases from [server-plan.md](../server-plan.md). For each step, the discipline is the same and matches CLAUDE.md:

1. **Read the relevant spec section.** Don't skim; the verification fixture list at the bottom is the acceptance criterion.
2. **Write the failing test(s) from that fixture list.** TDD-first applies — these are core modules per CLAUDE.md's "test-first is mandatory" list.
3. **Implement the smallest thing that makes them pass.**
4. **Run the verification ladder** (formatter → linter → type-check → unit tests → relevant integration tests).
5. **Don't move on until green.** A red step blocks every subsequent step.

The order below is the *dependency* order. Within a step, sub-tasks can usually be parallelized; across steps, you can't start step N+1 with N broken.

## Layer 0 — primitives (no engine deps)

These are the building blocks every later layer touches. Get them right once; the cost of refactoring at this layer is the highest in the project because everything else depends on them.

### Step 0.1 — Repo scaffold

**Goal:** an empty Python project with the verification ladder wired.

**Deliverables:**

- `pyproject.toml` declaring Python 3.12+, dependencies (PyMahjongGB, `websockets`, `textual` deferred), dev deps (`pytest`, `mypy`, `ruff`).
- `mahjong/` package skeleton matching the layout in [engine-api.md](engine-api.md) (empty modules with `__init__.py`).
- Pre-commit config running formatter (ruff format), linter (ruff check), type-check (mypy --strict on `mahjong.engine.*`, lenient elsewhere), and pytest.
- GitHub Actions matrix: Linux + macOS, Python 3.12 + latest.
- CLAUDE.md is checked in already; nothing to do.

**Gate:** an empty `pytest` run succeeds; `mypy --strict mahjong/engine/` passes on the empty engine; CI matrix is green on the empty repo.

### Step 0.2 — Tile encoding

**Spec:** [state-schema.md § Tile encoding](state-schema.md).

**Deliverables:**

- `mahjong/engine/tiles.py`: tile-token validation, canonical sort order, the `canonical_tile_set()` function returning all 144 tokens in the locked order ([determinism.md](determinism.md)).
- Type alias `Tile = str` with a `validate_tile(s: str) -> bool` helper.

**Tests (write first):**

- Every valid token (`W1`–`W9`, `B1`–`B9`, `T1`–`T9`, `F1`–`F4`, `J1`–`J3`, `H1`–`H8`) validates true; a sample of invalid strings (`X1`, `W0`, `W10`, empty, lowercase) validates false.
- `canonical_tile_set()` returns exactly 144 tokens in the order pinned by [determinism.md fixture 2](determinism.md).
- Sort order: `sorted([random sample], key=tile_sort_key)` produces the canonical order section by section.

**Gate:** all tests green; tiles fixture matches the determinism spec golden.

### Step 0.3 — Determinism primitives

**Spec:** [determinism.md § The RNG, § The canonical hash](determinism.md).

**Deliverables:**

- `mahjong/engine/rng.py`: `rng_bytes(seed, cursor, n)`, `uniform_int(seed, cursor, upper_inclusive)`, `shuffled_wall(seed)`.
- `mahjong/engine/hashing.py`: `canonical_hash(obj)`.
- Lint rules: AST check that `random`, `numpy.random`, `time`, `datetime`, `logging` are not imported under `mahjong.engine.*` (engine-api.md fixture 1, determinism.md fixture 9). Wire into pre-commit.

**Tests (write first):**

- `rng_bytes` golden vector (determinism.md fixture 1) — three or four hardcoded `(seed, cursor, n) → bytes` triples, cross-platform.
- `uniform_int` golden table (determinism.md fixture 3) — covers `n=1, 2, 34, 144`, power-of-2, edge of rejection range.
- `shuffled_wall(seed=12345)` golden — the single load-bearing fixture (determinism.md fixture 4).
- `canonical_hash` golden table (determinism.md fixture 5) — empty dict, primitive values, nested, lists.
- The no-float and no-random-import lints fire on a synthetic offending file and pass on the real engine source.

**Gate:** all goldens match on both Linux and macOS in CI.

## Layer 1 — engine types and PyMahjongGB wrapper (depends on Layer 0)

### Step 1.1 — State types

**Spec:** [state-schema.md § Top-level state object, § Action grammar](state-schema.md).

**Deliverables:**

- `mahjong/engine/types.py`: `GameState`, `SeatView`, `Action`, `RuleSetRef` as `TypedDict`s (or dataclasses, decide once); field validators.
- `mahjong/engine/errors.py`: `EngineError`, `IllegalAction`, `InvalidState`, `RulesetError` per [engine-api.md](engine-api.md).

**Tests (write first):**

- A constructed canonical state serializes via `canonical_hash` to a stable value; two states with permuted-but-equivalent fields (e.g., dict key order) hash to the same value.
- A `concealed` list constructed out of order is rejected by the validator (engine sorts before construction; the validator checks the invariant).
- Each exception class carries the payload fields engine-api.md fixture 5 enumerates.

**Gate:** type-check passes; round-trip and invariant tests green.

### Step 1.2 — PyMahjongGB wrapper

**Spec:** [engine-api.md § PyMahjongGB integration boundary](engine-api.md).

**Deliverables:**

- `mahjong/engine/pymj.py`: the six wrapper functions (`calculate_fan`, `shanten`, `shanten_specialized`, `winning_tiles`, etc.).
- AST lint: no module under `mahjong.engine.*` except `pymj.py` imports the PyMahjongGB package directly.

**Tests (write first):**

- For each wrapper function, a checked-in MCR-canonical example: input hand → expected output (fan list, shanten count). At least one example per wrapper (engine-api.md fixture 4).
- Lint fires on a synthetic offending file.

**Gate:** wrapper tests green against the real PyMahjongGB; lint catches violations.

### Step 1.3 — Ruleset loader

**Spec:** [determinism.md § Ruleset `config_hash`](determinism.md), [engine-api.md § Internal submodule layout](engine-api.md).

**Deliverables:**

- `mahjong/engine/rulesets/mcr-2006.json`: the canonical 81-fan MCR config.
- `mahjong/engine/rulesets/MANIFEST.json`: maps `mcr-2006` → its `config_hash`.
- `mahjong/engine/rulesets/__init__.py`: `load_ruleset(ref) -> dict`, computes `config_hash` from the resolved dict.

**Tests (write first):**

- `load_ruleset({"id": "mcr-2006"})` returns a dict whose `canonical_hash` matches the MANIFEST.
- Unknown ruleset id raises `RulesetError`.
- Mismatched `config_hash` (caller provided one; loader computed a different one) raises `RulesetError`.

**Gate:** ruleset loads; hash stamps onto states.

## Layer 2 — engine core (depends on Layers 0 and 1)

### Step 2.1 — `initial_state` and projection

**Spec:** [state-schema.md § Engine API surface, § Per-seat projection](state-schema.md).

**Deliverables:**

- `mahjong/engine/state.py`: `initial_state(ruleset, seed)`, `project(state, seat)`, `is_terminal(state)`, `state_hash(state)`.
- The deal logic uses `shuffled_wall` from Layer 0, deals 13 (or 14 for dealer) tiles per seat, handles flower replacement at deal time, sets `phase = "DISCARD"`.

**Tests (write first):**

- `initial_state(mcr-2006, seed=12345)` produces a state whose `state_hash` matches a checked-in golden (cross-platform).
- The state's `wall.remaining_count + sum(seat.concealed sizes) + sum(seat.flowers sizes) == 144`.
- `project(state, seat)` privacy assertion (state-schema.md fixture 3): zero foreign concealed tokens.
- `project(state, seat).seats[seat] == state.seats[seat]` (fixture 4).
- Phase transitions table fixture (state-schema.md fixture 5) — the transition logic doesn't exist yet, so this test exercises only the initial transition (`DEAL → DISCARD`).

**Gate:** deal is byte-stable; projection enforces privacy.

### Step 2.2 — `legal_actions` (decomposed by phase)

**Spec:** [state-schema.md § Action grammar, § `legal_actions`](state-schema.md), [engine-api.md § Internal submodule layout](engine-api.md).

**Deliverables:**

- `mahjong/engine/legality/discard.py`: legal actions during `DISCARD` phase (PLAY a tile from concealed; declare concealed/added GANG; declare HU on self-draw).
- `mahjong/engine/legality/claim.py`: legal actions during `CLAIM_WINDOW` (PASS, PENG, CHI from next seat only, exposed GANG, HU on discard).
- `mahjong/engine/__init__.py`: re-export `legal_actions`.

**Tests (write first):**

- For every state in the (still-tiny) fixture suite and every seat: `legal_actions` returns the expected set (hand-traced fixtures, one per action type — state-schema.md fixture 6's "every action returned is legal" half; the "every illegal action is not returned" half waits for step 2.3).
- HU detection uses `pymj.winning_tiles`; a fixture concealed hand at tenpai + the winning discard produces an HU action.
- CHI legality respects "next seat only" — fixtures where CHI is correctly absent for non-adjacent seats.

**Gate:** legality fixtures green per action type.

### Step 2.3 — `apply_action` (decomposed by action type)

**Spec:** [state-schema.md § Engine API surface](state-schema.md), [engine-api.md](engine-api.md).

**Deliverables:**

- `mahjong/engine/transition/play.py`: PLAY (own-turn discard).
- `mahjong/engine/transition/claim.py`: PENG, CHI, exposed GANG.
- `mahjong/engine/transition/gang.py`: concealed and added GANG.
- `mahjong/engine/transition/hu.py`: HU (calls `pymj.calculate_fan`; populates `terminal`).
- `mahjong/engine/transition/pass_.py`: PASS in a claim window.
- `mahjong/engine/transition/__init__.py`: `internal_draw` + claim-window helpers (engine-internal, no caller surface).
- `mahjong/engine/__init__.py`: re-export `apply_action`.

**Tests (write first):**

- Per action type: hand-traced fixture (`state_before`, `action`) → `state_after` with checked-in `state_hash`. One fixture per action type minimum.
- `IllegalAction` is raised with full payload (engine-api.md fixture 5) when an action not in `legal_actions` is submitted.
- The "fully consistent" half of state-schema.md fixture 6: every action *not* in `legal_actions` raises.
- Determinism: same `(state, seat, action)` always produces the same `state_after` (hashes match across runs).

**Gate:** every action type has a passing transition fixture; the legality/transition consistency check is green.

### Step 2.4 — Engine end-to-end smoke

**Spec:** state-schema.md, engine-api.md.

**Deliverables:** no new code — composition of the above.

**Tests (write first):**

- A scripted four-PASS-everywhere "game" (no one can win; ends in exhaustive draw) runs from `initial_state` through `apply_action` calls until `terminal` is populated. Final `state_hash` matches a checked-in golden.
- A scripted "dealer self-draws HU on turn 1" toy game (synthetic deal via mocked `shuffled_wall`) runs to `terminal` with the expected fan list. Verifies `pymj.calculate_fan` is wired into HU.

**Gate:** the engine can play through a complete hand. **This is the engine's S0 internal milestone** — no I/O, no adapters, no records, but the rules engine is functionally complete and proven.

## Layer 3 — record store (depends on Layer 2)

### Step 3.1 — Record writer

**Spec:** [record-format.md](record-format.md).

**Deliverables:**

- `mahjong/records/writer.py`: `RecordWriter(path)` context-manager-like object. Methods: `header(...)`, `deal(...)`, `draw(...)`, `discard(...)`, `claim_window(...)`, `claim_decision(...)`, `claim_resolution(...)`, `hand_end(...)`, `footer()`. Each appends one JSONL line with the correct `seq` and computes the running checksum.
- `mahjong/records/diff.py`: `diff_to_events(state_before, action, state_after) -> list[RecordEvent]` — the diff function that produces record events from state transitions. Pure function; lives outside the engine per state-schema.md.

**Tests (write first):**

- `RecordWriter` round-trip: write a small sequence of events; read the file back as JSONL; every line parses; `seq` is 0..N strictly monotonic.
- `diff_to_events` for each action type: fixture `(state_before, action, state_after) → expected events`.
- Footer checksum matches recomputed value (record-format.md fixture 5).
- A canonical event has byte-identical serialization across two writes (record-format.md fixture 1, write half).

**Gate:** events written by the writer parse identically to themselves.

### Step 3.2 — Record reader / replay

**Spec:** [record-format.md § Replay reproduces canonical state](record-format.md).

**Deliverables:**

- `mahjong/records/reader.py`: `read_record(path) -> Record` (parses, validates `seq` integrity and checksum).
- `mahjong/records/replay.py`: `replay(record) -> Iterator[GameState]`, `replay(record, seat=S) -> Iterator[SeatView]`.

**Tests (write first):**

- Round-trip identity (record-format.md fixture 1): `record == write(read(record))`.
- Replay reproduces canonical state (fixture 2): every `state_hash` in the record matches the replayed state's hash.
- Per-seat projection consistency (fixture 3): `replay(record, seat=S)` produces `SeatView`s matching `project(state_t, S)`.
- Privacy on replay (fixture 4): zero foreign concealed tokens at any timestep.
- Sequence integrity (fixture 5): corrupted record (bad checksum, gap in `seq`, missing footer) is rejected with a clear error.

**Gate:** any record the writer produces is replayable; replays are deterministic.

### Step 3.3 — Botzone export

**Spec:** [record-format.md § Botzone export](record-format.md).

**Deliverables:**

- `mahjong/records/botzone_export.py`: `export(record) -> str` (the Botzone judge-log format).

**Tests (write first):**

- A hand-traced fixture record exports to a hand-traced expected Botzone log (string equality).
- Round-trip for events that *do* survive (HEADER, DEAL, DRAW, DISCARD, HAND_END are sufficient information): re-import to our format produces a record with the right action sequence (timing and claim-window data are necessarily missing).

**Gate:** export produces well-formed Botzone logs. The judge-acceptance test waits for S1 (real Botzone judge in the loop).

## Layer 4 — adapter scaffolding and table manager (depends on Layers 2 and 3)

### Step 4.1 — `SeatAdapter` protocol and trivial adapters

**Spec:** [seat-port.md § The interface, § Adapter catalog](seat-port.md).

**Deliverables:**

- `mahjong/adapters/base.py`: `SeatAdapter` Protocol, `SeatIdentity` type, `SeatContext` / `Prompt` / `LeaveReason` types.
- `mahjong/adapters/canned.py`: `CannedAdapter`.
- `mahjong/adapters/autopass.py`: `AutoPassAdapter`.

**Tests (write first):**

- `CannedAdapter` from a scripted action list returns the right action per `decide` call.
- `AutoPassAdapter` always returns `prompt.default_action`; never blocks; `seated`/`observe`/`left` are no-ops.
- Both adapters satisfy the `SeatAdapter` Protocol (mypy check).

**Gate:** adapters exist; protocol checks.

### Step 4.2 — Table manager (asyncio loop)

**Spec:** [seat-port.md § Lifecycle and concurrency model, § Error model](seat-port.md).

**Deliverables:**

- `mahjong/table/manager.py`: the asyncio loop. Orchestrates `seated` → `observe`-fanout / `decide` cycles → `left`. Implements timeout cancellation, illegal-action handling, crash handling, and `AutoPassAdapter` substitution per the strike budget.
- `mahjong/cli/play_test.py`: the `python -m mahjong play-test` entry point that wires four `CannedAdapter`s to a table and runs one hand.

**Tests (write first):**

- Four-`CannedAdapter` hand runs end-to-end and produces a record matching a checked-in fixture (seat-port.md fixture 1, **S0 walking-skeleton exit artifact**).
- Timeout, illegal action, and crash each produce the expected event markers and trigger the strike counter (seat-port.md fixtures 2, 3, 4).
- `observe` fanout independence (fixture 6).
- Claim-window concurrency invariance (fixture 7).
- `AutoPassAdapter` substitution preserves replay (fixture 8).

**Gate:** **S0 exit artifact** is checked in. `python -m mahjong play-test` plays a fixture hand; the record replays byte-identically. Determinism check passes cross-platform.

## Layer 5 — bot runner (depends on Layer 4)

### Step 5.1 — Manifest loader and sandbox setup

**Spec:** [bot-runner-protocol.md § Bot manifest, § Sandboxing](bot-runner-protocol.md).

**Deliverables:**

- `mahjong/bots/manifest.py`: manifest parsing and validation.
- `mahjong/bots/sandbox.py`: `setrlimit` application, env whitelist, network-namespace setup (Linux), unprivileged-uid drop. macOS deg-mode with warnings.
- `mahjong/bots/registry.py`: register/lookup of bots by `bot_id`.

**Tests (write first):**

- Manifest validation: missing `bot_id` is rejected at registration; over-cap `memory_mb` is rejected (bot-runner-protocol.md fixture 10).
- Sandbox layers: a bot that allocates beyond `RLIMIT_AS` gets killed (Linux test, macOS xfail — fixture 6).
- Network deny: a bot that opens a socket fails on Linux (fixture 7).

**Gate:** manifests validate; sandbox layers enforce on Linux.

### Step 5.2 — `BotRunnerAdapter`

**Spec:** [bot-runner-protocol.md § Process lifecycle, § Wire framing, § Time-budget enforcement](bot-runner-protocol.md).

**Deliverables:**

- `mahjong/adapters/bot_runner.py`: the adapter. Spawns subprocess, performs HELLO handshake, serializes Botzone history per `decide`, parses responses, enforces per-turn timeout.
- `mahjong/bots/sdk/`: a small Python SDK helper (one file, ~50 lines) bundled with the project. Bots that use it write `from mahjong.bots.sdk import run_bot; run_bot(decide_fn)` and get the framing for free.

**Tests (write first):**

- HELLO handshake success and skip paths (bot-runner-protocol.md fixtures 2, 3).
- Spawn failure (fixture 4): bad manifest path produces clean `process_exit` and `AutoPassAdapter` substitution.
- Per-turn timeout (fixture 5): a `sleep(10)` bot against 1s budget triggers SIGTERM and event marker.
- Framing violations (fixture 8): missing sentinel triggers framing_error; CRLF responses are tolerated.
- Illegal-action surfacing (fixture 9): a bot proposing a non-legal CHI gets `illegal: true` plus original `attempted_action`.

**Gate:** a trivial Python bot bundled with the project plays a hand against three `CannedAdapter`s.

### Step 5.3 — Botzone reference bot integration (S1 exit)

**Spec:** [bot-runner-protocol.md fixture 1](bot-runner-protocol.md), [server-plan.md S1](../server-plan.md).

**Deliverables:**

- `bots/sample-botzone/`: a vendored copy (or git submodule) of the reference bot from `sample-bot-Botzone`.
- An integration test that plays four sample-bots against each other for one hand; exports the record to Botzone log format; feeds the log to the official Botzone judge; asserts judge acceptance.

**Tests (write first):**

- Four-sample-bot hand completes (one of each: PASS, PLAY, PENG, CHI, GANG, BUGANG, HU represented at least once across the fixture suite — server-plan.md S1 fixture 2).
- Botzone judge acceptance: the exported log is accepted (server-plan.md S1 exit).

**Gate:** **S1 exit artifact** is checked in. Records are judge-accepted.

## Layer 6 — self-play harness (depends on Layer 5)

### Step 6.1 — `selfplay` subcommand

**Spec:** [selfplay-harness.md](selfplay-harness.md).

**Deliverables:**

- `mahjong/cli/selfplay.py`: the CLI entry point with the flags from selfplay-harness.md.
- `mahjong/selfplay/seeds.py`: `hand_seed(master_seed, hand_index)`.
- `mahjong/selfplay/runner.py`: the serial loop; record writing; eval-summary aggregation.
- `mahjong/selfplay/parallel.py`: the `--parallel-hands N` worker pool.

**Tests (write first):**

- 10-hand serial run determinism (selfplay-harness.md fixture 1) — checked-in record hashes match across re-runs.
- Crash recovery (fixture 2): killed mid-hand-10, resumed, produces same final set.
- Parallel-equivalence (fixture 3): `--parallel-hands 4` produces the same record set as serial.
- God-view privacy gate (fixture 4): without `--driver-bot`, no adapter sees canonical state.
- Bot rotation determinism (fixture 5).
- Eval-summary correctness (fixture 6) against a checked-in fixture record set.

**Gate:** the harness produces deterministic, resumable runs.

## Layer 7 — networking + human adapter (depends on Layer 6, gates S2)

Specs drafted 2026-05-22 per [s2-s3-plan.md §4 Layer 7](../s2-s3-plan.md). Build order respects dependencies inside the layer: state-schema amendment first (everything below assumes the public projection works), then codec, then transport, then the session-mux that ties them together, then the adapter that bridges to the table manager, then the TUI, then the end-to-end gate.

### Step 7.0 — `project(state, seat=None)` amendment

**Spec:** [state-schema.md § Per-seat projection](state-schema.md) (additive edit; lands as the *first* sub-step before any new code touches the engine).

**Deliverables:**

- Widen the `project(state: GameState, seat: int) -> SeatView` signature in [state-schema.md](state-schema.md) to `seat: int | None`. Document the public-view rule field-by-field: every `concealed` empty, no own-draw `tile`, concealed-meld tile elided, exposed melds fully revealed.
- Implement the broadened `project` in `mahjong/engine/state.py`. The `seat=None` path returns a `PublicView` whose shape is `SeatView` with `seats[i].concealed` always empty and the `you` field absent.
- Add `project_event(event, seat=None)` symmetric to the existing per-seat path; it's the function the wire layer calls for spectator EVENT projection.
- The six existing callsites under `mahjong/engine/` accept `int | None` without further change.

**Tests (write first):**

- The existing state-schema.md fixture 3 (privacy assertion) generalised to `seat=None`: zero `concealed` tokens visible anywhere; own-draw events strip `tile`; concealed gangs strip their tile-identity.
- Round-trip: `project(state, seat=None)` is byte-stable across two calls (canonical hash).
- For every seat S, the per-seat projection's *public* fields agree with the `seat=None` projection's same fields (concealed differs; public state matches).
- Lint: no engine module other than `state.py` constructs a `PublicView` (boundary discipline).

**Gate:** the amendment is locked in the spec; the six callsites pass mypy; new fixtures green on Linux + macOS.

### Step 7.1 — Wire-protocol codec

**Spec:** [wire-protocol.md § Message catalog, § Message framing](wire-protocol.md).

**Deliverables:**

- `mahjong/wire/codec.py`: encode/decode every documented message kind (player + spectator). Typed `WireMessage` discriminated union; `encode(msg) -> bytes`, `decode(bytes) -> WireMessage`.
- `mahjong/wire/errors.py`: `WireDecodeError`, `WireFramingError`, `WireVersionError`.
- The `shutting_down` error code is added to the wire-protocol catalog as part of this step (see [session-mux.md § Server lifecycle interaction](session-mux.md)).

**Tests (write first):**

- One round-trip fixture per message kind: encode → decode → equality on the typed object. Covers HELLO, AUTH_REQUEST/RESPONSE, RESUME, ATTACH/ATTACHED, DETACH, EVENT, PROMPT, ACTION, ERROR, HEARTBEAT, LIST_TABLES/TABLE_LIST, CREATE_TABLE, CLOSE_TABLE, SPECTATE/SPECTATING/STOP_SPECTATING.
- Corrupted-frame rejection: invalid JSON, missing `kind`, unknown `kind`, missing `seq` on server→client → typed errors raised.
- Forward-compat: unknown optional fields tolerated; unknown `kind` is a hard error.
- Privacy: encoding a server-bound `EVENT` for a player vs. spectator produces different `tile` field presence per `project(state, seat=None)`.

**Gate:** every wire-protocol fixture from the spec's verification list is green; lint enforces the no-floats / no-non-canonical-JSON discipline.

### Step 7.2 — WebSocket transport

**Spec:** [wire-protocol.md § Transport](wire-protocol.md).

**Deliverables:**

- `mahjong/wire/server.py`: `WebSocketServer` wrapping the `websockets` library. Accepts upgrade on `/socket` with subprotocol `mahjong-v1`; handles ping/pong; surfaces inbound frames as `WireMessage`s and outbound `WireMessage`s as frames.
- Connection-id allocation; per-connection inbound/outbound channels; the `start` / `stop_accepting` / `close` lifecycle.
- HTTP handler hook so `/health` can ride on the same listener ([server-lifecycle.md § Health endpoint](server-lifecycle.md)).

**Tests (write first):**

- Connect → HELLO → close round-trip against a real local socket (loopback, dynamic port).
- Subprotocol mismatch (request `mahjong-v2`) → server refuses upgrade.
- Binary frame → server closes with code 1003.
- Ping/pong handled transparently; an idle connection stays open as long as pings answer.
- Framing error (oversized frame, malformed UTF-8) → server closes with the documented code.

**Gate:** real-socket fixtures green; `mypy mahjong/wire/` clean.

### Step 7.3 — Session multiplexer

**Spec:** [session-mux.md § Seat state machine, § Spectator handling, § Conflict resolution, § Server lifecycle interaction](session-mux.md).

**Deliverables:**

- `mahjong/sessions/mux.py`: `SessionMux` per table, holding `dict[seat, SeatSession]` + `dict[connection_id, Spectator]`. State machine for `UNBOUND ↔ LIVE ↔ HELD`; ring buffer; hold timer; pending-prompt future.
- `mahjong/sessions/timers.py`: `asyncio.call_later` wrappers for seat-hold, prompt-deadline, and heartbeat timers, all idempotent on fire-after-resolution.
- Conflict resolution (same-user takeover, different-user rejection, HELD→LIVE via RESUME shortcut).
- Spectator subscribe/unsubscribe path with public-projection wiring through Step 7.0's `project_event(event, seat=None)`.

**Tests (write first):**

- Every state-machine transition fires exactly once per trigger (session-mux.md fixture 1).
- Buffered events replay in order on reconnect (fixture 2).
- Ring-buffer overflow forces a fresh snapshot (fixture 3).
- Pending prompt survives reconnect (fixture 4).
- Pending prompt defaults at deadline while HELD (fixture 5).
- Seat-hold expiry: no pending prompt (fixture 6) and with pending prompt (fixture 7).
- Same-user takeover (fixture 8); different-user rejection (fixture 9).
- Hand-end while HELD (fixture 10).
- Spectator subscribe + public projection (fixture 16); immediate drop (fixture 17); multiple-spectator identical streams (fixture 18); max-per-table limit (fixture 19); across-hand subscription (fixture 20); own-draw projection rule (fixture 21).

**Gate:** all 21 session-mux fixtures green; mypy clean; cross-platform CI green.

### Step 7.4 — `HumanAdapter`

**Spec:** [session-mux.md § The HumanAdapter](session-mux.md), [seat-port.md § The interface](seat-port.md).

**Deliverables:**

- `mahjong/adapters/human.py`: `HumanAdapter` implementing `SeatAdapter` against a `SessionMux` seat slot. `seated` sends ATTACHED + snapshot; `observe` projects + buffers/sends; `decide` parks a future and arms the deadline; `left` tears down.
- The four implementation invariants from [session-mux.md § The HumanAdapter](session-mux.md): one outstanding prompt, ring-buffer order, no-loss-on-LIVE→HELD, decide-always-resolves.

**Tests (write first):**

- `seated/observe/decide/left` round-trip against a fake `SessionMux` (no real socket).
- Concurrency: `observe` arriving the same tick as the drop ends up in the buffer, not lost.
- Strike counter integration: illegal action increments strike at the table manager but leaves the seat LIVE (session-mux.md fixture 14).
- No-prompt action (fixture 12); stale prompt_id (fixture 13); illegal action (fixture 14); idempotent reconnect cycles (fixture 15).
- Graceful shutdown delivery (fixture 11).

**Gate:** the `HumanAdapter` slots into the existing table manager without changes; the four-`CannedAdapter` walking-skeleton fixture from Step 4.2 still passes (regression).

### Step 7.5 — TUI client

**Spec:** [tui-client.md](tui-client.md).

**Deliverables:**

- `mahjong/tui/app.py`: `MahjongApp(textual.App)`. Owns the `ConnectionManager` (one WebSocket).
- `mahjong/tui/screens/{login,lobby,player_table,spectator_table,hand_end}.py`: the five screens. Each is headless-testable via Textual's `Pilot`.
- `mahjong/tui/render/`: tile rendering, meld layout, discard-pile widget, public-vs-private rendering branches.
- `mahjong/cli/tui.py`: `python -m mahjong tui` entry point.

**Tests (write first):**

- One `app.run_test()` scripted-keystroke fixture per screen: scripted input produces the expected outbound wire-message sequence.
- A "watch a table" fixture: lobby → spectator screen → render verifies that no `concealed` tile leaks even if the wire payload accidentally contained one (defense-in-depth assertion on the rendering layer).
- Bilingual labels render both EN and ZH for every tile and action label fixture.
- Crash-resistance: a deliberately broken render path is caught at the screen boundary; the WebSocket stays open; an error placeholder appears.

**Gate:** every screen has a passing pilot fixture; spectator privacy assertion green; locale rendering matches the spec's bilingual rule.

### Step 7.6 — End-to-end S2 fixture

**Spec:** [s2-s3-plan.md §"S2 exit"](../s2-s3-plan.md), [server-plan.md S2](../server-plan.md).

**Deliverables:** no new modules — composition of 7.0–7.5 against an in-process loopback server.

**Tests (write first):**

- Scripted-keystroke end-to-end: spin up the server in the test process; connect four TUI pilots (one per seat); script one hand of canned-input; assert the server-side record file is byte-identical to a checked-in fixture.
- Drop / reconnect inside the seat-hold window: pilot disconnects mid-turn; same pilot reconnects; the hand completes without auto-pass and the record reflects no `auto_pass` event.
- Drop past the seat-hold window: pilot disconnects; `MAHJONG_SEAT_HOLD_SECONDS=1`; wait 2s; the hand completes via `AutoPassAdapter` substitution and the record includes the documented `replaced_by_auto_pass` marker.
- Spectator subscription: a fifth pilot subscribes via SPECTATE; assert it receives the public-projected stream and no `PROMPT`.

**Gate:** **S2 exit artifact** checked in. `python -m mahjong serve` + four scripted TUIs play a hand whose record replays byte-identically. The drop/reconnect and spectator fixtures are green cross-platform.

## Layer 8 — accounts, sessions, persistence (depends on Layer 7, gates S3)

Specs drafted 2026-05-22 per [s2-s3-plan.md §4 Layer 8](../s2-s3-plan.md). Build order respects dependencies inside the layer: schema first, auth on top of schema, persistence on top of both, multi-table orchestration that uses persistence, then lifecycle that ties everything together, then the end-to-end S3 fixture.

### Step 8.1 — SQLite schema + migrations

**Spec:** [sqlite-schema.md](sqlite-schema.md).

**Deliverables:**

- `mahjong/persistence/migrations/__init__.py`: `apply_migrations(conn, target)` runner; tracks `schema_version`.
- `mahjong/persistence/migrations/0001_initial.py`: creates `schema_version`, `accounts`, `sessions`, `hand_index`, `hand_participants` and every documented index.
- `mahjong/persistence/db.py`: connection open with pragmas (`foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`).
- `tests/persistence/expected_schema.sql`: the schema snapshot the regression test diffs against.

**Tests (write first):**

- Initial migration applies to a fresh DB → `schema_version.version == 1`; every table+index present (sqlite-schema.md fixture 1).
- Foreign-key enforcement: `PRAGMA foreign_keys == 1` on every connection; bad-FK INSERT raises IntegrityError (fixture 3).
- CHECK constraints (fixtures 4, 5, 6).
- Cascade delete on `hand_index` → `hand_participants` (fixture 7); SET NULL on `accounts` → `hand_participants` (fixture 8).
- WAL mode and busy_timeout applied (fixture 9).
- Schema snapshot stability (fixture 10) — load-bearing.

**Gate:** fresh-apply produces the expected snapshot byte-identically; CI matrix green on Linux + macOS.

### Step 8.2 — Auth module

**Spec:** [auth.md](auth.md).

**Deliverables:**

- `mahjong/auth/hasher.py`: `PasswordHasher` wrapping argon2-cffi with the documented parameters.
- `mahjong/auth/sessions.py`: session token issuance (32 bytes from `secrets.token_hex`), validation, sliding renewal, revocation.
- `mahjong/auth/service.py`: the auth flow — `authenticate(username, password) -> Account | AuthFailure`, `validate_session(token) -> Account | AuthFailure`. Uses persistence APIs for account/session CRUD.
- Wire-protocol hooks: `AUTH_REQUEST` / `RESUME` handlers that call into `auth.service`.

**Tests (write first):**

- Argon2id round-trip; PHC string format; needs-rehash logic on changed parameters.
- Constant-time failure path: wrong-username and wrong-password produce byte-identical responses (no length difference; static-known-bad-hash verify covers the timing equality).
- Session token lifecycle: insert → validate → renew → revoke → validate fails.
- Sliding renewal updates `last_seen_ms` and `expires_at_ms`.
- Bot-account path: a `kind='bot'` account authenticates via the same flow.
- Disabled-account refused without revealing disabled-vs-wrong-password externally.

**Gate:** all auth.md fixtures green; mypy strict on `mahjong/auth/`.

### Step 8.3 — Persistence API

**Spec:** [persistence-api.md](persistence-api.md).

**Deliverables:**

- `mahjong/persistence/__init__.py`: re-exports `Persistence`.
- `mahjong/persistence/accounts.py`, `sessions.py`, `hands.py`: the typed CRUD helpers.
- `mahjong/persistence/rebuild.py`: `rebuild_index_from_records` walking `records/` → INSERT OR REPLACE.
- Table-manager hooks: `reserve_hand` after HEADER write, `finalize_hand` after FOOTER write.

**Tests (write first):**

- `reserve_hand` round-trip (persistence-api.md fixture 1); atomicity (fixture 3).
- `finalize_hand` round-trip (fixture 2).
- `find_hands_by_account` ordering and pagination (fixtures 4, 5); `find_hands_by_match` ordering (fixture 6).
- `integrity_check` detects missing files (fixture 7), orphans (fixture 8), and checksum mismatches (fixture 9).
- Rebuild from records produces an equivalent DB (fixture 10) — load-bearing.
- Rebuild is idempotent (fixture 11).
- Session and account CRUD round-trips (fixtures 12, 13).

**Gate:** all 13 fixtures green; the table manager's hand-end path writes the index row; the `Persistence` API has zero raw-SQL leaks outside `mahjong/persistence/`.

### Step 8.4 — Multi-table orchestrator

**Spec:** [server-lifecycle.md § Table registry, § Multi-table interaction with persistence](server-lifecycle.md), [s2-s3-plan.md §10.3](../s2-s3-plan.md).

**Deliverables:**

- `mahjong/server/registry.py`: `TableRegistry` holding `{table_id: TableHandle}`. `create_table` / `list_tables` / `get_table` / `close_table` / `drain_all`.
- `TableHandle` bundling one `TableManager` + one `SessionMux`.
- Wire-protocol handlers for `CREATE_TABLE`, `LIST_TABLES`, `CLOSE_TABLE` (admin-gated on `accounts.role == 'admin'` per [auth.md](auth.md)).
- The shared `Persistence` instance threaded into every `TableManager`'s hand-end hook.

**Tests (write first):**

- Two-table isolation (server-lifecycle.md fixture 17): mutation on table A doesn't appear in table B's records / index rows.
- `CREATE_TABLE` rejected post-drain (fixture 18).
- `CLOSE_TABLE` admin gating: non-admin → ERROR `permission_denied`; admin → OK.
- `LIST_TABLES` reflects current state including post-create / post-close transitions.

**Gate:** multi-table fixtures green; the existing single-table tests still pass (regression).

### Step 8.5 — Server lifecycle

**Spec:** [server-lifecycle.md](server-lifecycle.md).

**Deliverables:**

- `mahjong/server/config.py`: `load_config_from_env() -> ServerConfig`; unknown-var warning; type validation.
- `mahjong/server/logging.py`: structured-JSON formatter (`MAHJONG_LOG_FORMAT=json`) and the dev `console` formatter.
- `mahjong/server/health.py`: `/health` HTTP handler riding on the WebSocket listener (or separate via `MAHJONG_HEALTH_LISTEN_ADDR`).
- `mahjong/server/lifecycle.py`: startup sequence, signal handlers, `drain()` coroutine, periodic tasks (`periodic_session_cleanup`, `periodic_wal_checkpoint`).
- `mahjong/cli/serve.py`: `python -m mahjong serve` entry point composing all of the above.

**Tests (write first):**

- Config defaults / validation / unknown-var warning (server-lifecycle.md fixtures 1, 2, 3).
- Startup happy path + existing-DB path (fixtures 4, 5).
- Startup failure modes: corrupt DB (fixture 6), port-bind failure (fixture 7), in-flight ABORTED reconciliation (fixture 8).
- `/health` responses: 200 normal (fixture 9), 503 draining (fixture 10), 500 DB stall (fixture 11).
- `SIGTERM` drain happy path (fixture 12) — load-bearing.
- New-connection rejection during drain (fixture 13).
- Drain timeout escalation (fixture 14); WAL checkpoint on drain (fixture 15).
- SIGKILL recovery (fixture 16) — load-bearing.
- Periodic session cleanup (fixture 19); periodic WAL checkpoint (fixture 20).
- Structured logging emits valid JSON with no leaked secrets (fixture 21).

**Gate:** every server-lifecycle.md fixture except 17 (covered by 8.4) and 22 (deferred to 8.6) is green.

### Step 8.6 — End-to-end S3 fixture

**Spec:** [s2-s3-plan.md §"S3 exit"](../s2-s3-plan.md), [server-lifecycle.md fixture 22](server-lifecycle.md), [server-plan.md S3](../server-plan.md).

**Deliverables:** no new modules — integration of 8.1–8.5 plus the Layer-7 TUI.

**Tests (write first):**

- Account → login → join → play → query: fresh data dir; admin inserts an account row; TUI logs in; joins a new table; plays one hand against three `CannedAdapter`s; server restarts; `find_hands_by_account` returns the played hand.
- Migration from previous schema (placeholder — collapses to fresh-apply for v1 since 0001 is the only migration).
- Multi-table fixture (re-asserts 8.4's two-table isolation in the end-to-end harness).

**Gate:** **S3 exit artifact** checked in. All four bullets in [server-plan.md § S3 exit criteria](../server-plan.md) are green and checked in.

### Step 8.7 — Multi-human-seat tables

**Spec:** [multi-human-seats.md](multi-human-seats.md). Pinned 2026-05-26.

**Context:** As of Step 8.6, every table has exactly one human seat (seat 0) and three `CannedAdapter`-PASS bots (seats 1–3). A second authenticated user can connect and browse tables but cannot sit down — `ATTACH` on seats 1–3 returns `seat_not_yours`. Real multi-player (friends playing each other) requires configurable seat composition plus an explicit hand-start trigger so the server does not begin a hand the instant one seat fills.

**Design decisions resolved up-front** (see [multi-human-seats.md § Alternatives considered](multi-human-seats.md)):

- Open-lobby seat model: `CREATE_TABLE.seats[]` declares only `{kind}`, never `user_id`.
- Explicit `START_HAND` wire message (new) — no auto-start on full attach; no privileged "creator" role.
- `kind: "bot"` slots remain `CannedAdapter`-PASS placeholders in v1 (Layer 9 wires real bot identities).
- `MAHJONG_LISTEN_ADDR` default stays `127.0.0.1:8400`; LAN/Tailscale exposure becomes a documented opt-in, not a default.
- `TableHandle` and `WebOrchestrator` hand-loops stay duplicated per the multi-table architecture memory's Decision 59.

**Sub-step order** (each sub-step ships with the named fixture(s) from multi-human-seats.md):

- **8.7.a — Schema parsing.** Add `SeatComposition` dataclass; thread `seats: tuple[SeatComposition, ...] | None` through `TableRegistry.create_table_direct` and `TableHandle.__init__`; parse `msg["seats"]` in `_handle_create_table`. Fixtures 1–7.
- **8.7.b — Attach widening.** Remove module-level `HUMAN_SEAT = 0`; route attach permission through `TableHandle.is_human_seat(seat)`; build per-seat adapter list from the composition. Fixtures 8–11.
- **8.7.c — `TABLE_LIST.seats[]` population.** Implement `TableHandle.summary_with_seats()`; update `TableSummary.to_wire()` (today returns `seats: []`). Fixtures 12–14.
- **8.7.d — `START_HAND` handler.** New wire kind in [wire-protocol.md](wire-protocol.md); `TableHandle.start_hand()` returning `StartHandOutcome`; `_run_hand_loop` ignition moves from `TableHandle.attach` to `TableHandle.start_hand`. Two new error codes (`humans_not_ready`, `hand_already_started`). Fixtures 15–18.
- **8.7.e — Web client (`mahjong/web/static/app.js`).** Lobby/seat-picker UI; `START_HAND` send after local `ATTACHED` once `TABLE_LIST.seats[]` shows all humans occupied; poll on 2-second timer; treat `humans_not_ready` and `hand_already_started` as silent no-ops in the lobby loop. No fixture (manual verify in browser); the existing single-human regression (fixture 20) is the automated guard.
- **8.7.f — End-to-end + regression.** Two-human full hand fixture (19, load-bearing exit gate); single-human regression (20); one-human-drop multi-human composite (21); persistence rows (22).

**Gate:** all 22 fixtures from [multi-human-seats.md § Verification fixtures](multi-human-seats.md) green. The Step 8.6 single-human end-to-end fixture continues to pass unchanged. Manual browser verify: two browser windows (or one window + one terminal websockets client) play one complete hand against each other + 2 canned bots.

### Step 8.8 — Deferred Layer 8 lifecycle hardening

**Context:** Step 8.5 landed as a "pragmatic cut" — the parts needed to make the server actually playable (`serve` CLI, AUTH wire, persistence wiring, basic graceful drain) shipped; the rest of [server-lifecycle.md](server-lifecycle.md)'s fixture list was deferred. The deferred items have no current user-visible failure pressure but are required for the proper S3 exit gate.

This step ships the deferred items grouped by subsystem. No new spec is needed — each fixture name below references a numbered fixture already in [server-lifecycle.md § Verification fixtures](server-lifecycle.md).

**Sub-steps:**

- **8.8.a — `/health` endpoint.** HTTP `GET /health` riding on the WebSocket listener (or separate via `MAHJONG_HEALTH_LISTEN_ADDR`). Returns 200 normal, 503 during drain, 500 on DB stall. server-lifecycle.md fixtures 9, 10, 11.
- **8.8.b — Drain-timeout escalation.** After `MAHJONG_DRAIN_TIMEOUT_SECONDS` (default 30s) the lifecycle layer cancels remaining hand tasks and force-closes connections. server-lifecycle.md fixture 14.
- **8.8.c — WAL checkpoint hooks.** Checkpoint on drain end (fixture 15); periodic background checkpoint every `MAHJONG_WAL_CHECKPOINT_SECONDS` (default 300s, fixture 20).
- **8.8.d — Periodic session cleanup.** Background task expiring `sessions` rows whose `expires_at_ms < now()`; runs every `MAHJONG_SESSION_CLEANUP_SECONDS` (default 60s). server-lifecycle.md fixture 19.
- **8.8.e — Structured JSON logging.** `MAHJONG_LOG_FORMAT=json` formatter emitting one JSON object per log record; no secret material in fields (the existing log calls already use structured `extra=` kwargs — this only adds the formatter). server-lifecycle.md fixture 21.
- **8.8.f — SIGKILL-recovery standalone fixture.** Already exercised indirectly by the S3 exit fixture; this sub-step extracts the in-progress→ABORTED reconciliation into its own focused test. server-lifecycle.md fixture 16.

**Gate:** every server-lifecycle.md fixture (1–22) is green, with the multi-table fixture 17 still owned by Step 8.4 and 22 still owned by Step 8.6. Step 8.7's introduction of `START_HAND` does not change any lifecycle behavior here.

**Why this step exists separately from 8.7:** the multi-human work is user-visible and motivating; the lifecycle items are correctness hardening with no current symptom. Sequencing 8.7 first lets us validate the new feature against real users; 8.8 closes the original S3 gate cleanly afterward.

## Layer 8 follow-ups (client polish + UX defects surfaced post-8.7)

Three specs landed 2026-05-26 covering work that surfaced once real two-human play started running through the lobby. Each is independent; pick them off in any order, but a single "client polish + decide-timeout" session can close 8.9 + 8.10 together.

- **8.9 — Cardinal-direction table renderer.** Replace `mahjong/web/static/render.js`'s vertical stack with a 3 × 3 cardinal grid (you at south; right/across/left in the other three cardinal cells; center cell carries the last-discarded tile glyph + a turn arrow pointing at `current_actor`). Active-actor highlight tracks `current_actor`. No wire change. Spec: [cardinal-ui.md](cardinal-ui.md), eight fixtures (seat-cell mapping, arrow directions including claim-window `?` and terminal, last-discard rendering, active highlight, own-hand-only-in-south defense-in-depth).
- **8.10 — Human-friendly decide timeouts.** Split `decide_timeout_seconds` into a `(seat_kind, prompt_kind)` table. Defaults: human DISCARD 60s, human CLAIM 20s, bot 30s (current). Adds `SeatAdapter.kind: Literal["human", "bot", "canned"]` to the seat-port protocol. Three env vars: `MAHJONG_DECIDE_TIMEOUT_HUMAN_DISCARD_S`, `..._HUMAN_CLAIM_S`, `..._BOT_S`. Spec: [human-decide-timeout.md](human-decide-timeout.md), six fixtures. Closes the "the game keeps playing without me making choices" defect surfaced 2026-05-26 (the DRAW.tile bug from commit `9f831c7` was the proximate cause, but a 30s deadline is still hostile to new players once that's gone).
- **8.11 — Mid-hand late-join: refuse with `hand_in_progress`.** Today a third party can ATTACH to a previously-UNBOUND human seat at a table whose hand is already running — the attach succeeds, but the joiner receives a pre-hand snapshot with no event replay (the seat's ring buffer was empty during UNBOUND). v1 picks "refuse with a new error code"; alternative B (replay from the record) is parked. Adds `hand_in_progress` to the wire error registry; updates the lobby to suppress Join buttons on `IN_PROGRESS` tables (Spectate stays available). Spec: [late-join-replay.md](late-join-replay.md), six fixtures.

**Order:** 8.9 (cardinal UI) and 8.10 (timeouts) compose well in one session — both are user-visible fixes for the same complaint ("I keep losing my turn"). 8.11 is a wire + lobby change that can ship independently before or after.

## What's deferred

These are explicitly *not* in this implementation order — they live in later S-phases ([server-plan.md](../server-plan.md)):

- **Layer 9+: analysis overlays** (S4, shared with AI components 1–5).
- **Layer 10+: home-rule overlays, rule-set archive** (S5).
- **Layer 11+: ops hardening, systemd, backups** (S7).

Each of these gets its own implementation-order pass when its phase comes up. The Tier-1 specs already cover what each will consume; the implementations are additive on top of Layers 0–8.

## Cross-cutting checklist for every step

For each step above:

- [ ] The relevant spec section was re-read (don't trust memory).
- [ ] Tests were written before implementation.
- [ ] The verification fixture(s) named in this doc are the test cases (not paraphrased; the exact fixtures).
- [ ] The verification ladder is green (format → lint → type-check → unit → integration).
- [ ] Determinism fixtures (where applicable) are green on Linux *and* macOS in CI.
- [ ] The step's "Gate" condition is met.
- [ ] Nothing more was built than the spec described (scope discipline; resist "while I'm here").
- [ ] If a spec disagreement surfaced during implementation, the spec was updated *before* the code (per CLAUDE.md, plans are the source of truth).

## Why this order

A few structural choices worth surfacing:

- **Layers 0–2 are the engine.** Built bottom-up so each layer is fully testable against the prior layer alone. No mocks needed; the dependencies are real.
- **Records (Layer 3) come before adapters (Layer 4).** Records are pure data over engine states. Adapters need records (the table manager writes them). Building records first means the adapter layer doesn't have to mock a record writer.
- **`CannedAdapter` before `BotRunnerAdapter`.** Canned is the testbed for the table manager. Bot-runner adds subprocess complexity; we want the table manager already solid before adding it.
- **S0 (canned-only) before S1 (bot-runner).** This matches server-plan.md and keeps S0 free of subprocess concerns — a clean architectural baseline that S1 then proves can be extended.
- **Self-play (Layer 6) before TUI (S2).** This is a *deviation* from server-plan's strict S0→S1→S2 ordering. The argument: self-play needs only the engine + records + bot-runner; it doesn't need transports, auth, or UI. Building it now means the AI plan has its training-data pipeline ready when AI components start, without waiting for S2/S3 to ship first. Server-plan's S2 (TUI) and S3 (persistence) still come next chronologically — they just happen *after* this implementation order ends, on top of the same engine.
