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
- `mahjong/engine/transition/draw.py`: internal wall-draw and flower replacement, invoked by other transitions.
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

## What's deferred

These are explicitly *not* in this implementation order — they live in later S-phases ([server-plan.md](../server-plan.md)):

- **Layer 7+: TUI client + WebSocket transport** (S2).
- **Layer 8+: accounts, sessions, SQLite persistence** (S3).
- **Layer 9+: analysis overlays** (S4, shared with AI components 1–5).
- **Layer 10+: home-rule overlays, rule-set archive** (S5).
- **Layer 11+: ops hardening, systemd, backups** (S7).

Each of these gets its own implementation-order pass when its phase comes up. The Tier-1 specs already cover what each will consume; the implementations are additive on top of Layers 0–6.

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
