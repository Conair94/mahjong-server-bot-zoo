# Implementation checklist

The actionable tracker for the build sequence in [docs/specs/implementation-order.md](docs/specs/implementation-order.md). Tick items as they land; keep this file current — stale tracker > no tracker.

**For each step:** re-read the spec section the step cites, write the listed tests *first*, then implement the smallest thing that turns them green. A red step blocks every dependent step. Don't tick a step until its **Gate** passes.

Cross-cutting checklist that applies to *every* step:

- [ ] Spec section was re-read (don't trust memory).
- [ ] Tests were written before implementation.
- [ ] The verification ladder is green (format → lint → type-check → unit → integration).
- [ ] Determinism fixtures (where applicable) are green on Linux **and** macOS in CI.
- [ ] No scope creep beyond what the spec describes.
- [ ] Any spec disagreement surfaced during work was resolved by updating the spec *first*, then the code.

---

## Layer 0 — primitives

### Step 0.1 — Repo scaffold

- [x] `pyproject.toml` with Python 3.12+, runtime deps, dev deps, pytest/mypy/ruff config.
- [x] `mahjong/` package skeleton matching [docs/specs/engine-api.md](docs/specs/engine-api.md) layout.
- [x] `tests/` directory mirroring the package layout, with `conftest.py` and golden-fixture helpers.
- [x] `.pre-commit-config.yaml` running formatter → linter → mypy → fast pytest.
- [x] `.github/workflows/ci.yml` with Linux + macOS matrix on Python 3.12 and 3.13.
- [x] `tests/test_scaffold.py` confirming every expected module imports.
- [x] **Gate:** `pytest` green, `mypy mahjong/` green, CI matrix green on an empty engine. *(Local verified 2026-05-19; CI matrix pending first push.)*

### Step 0.2 — Tile encoding

Spec: [state-schema.md § Tile encoding](docs/specs/state-schema.md), [determinism.md § canonical_tile_set](docs/specs/determinism.md).

- [x] Tests written (before implementation):
  - [x] Every valid tile token validates; sample of invalid strings rejects.
  - [x] `canonical_tile_set()` returns 144 tokens in the locked order.
  - [x] Canonical sort: random sample sorts to canonical order.
- [x] `mahjong/engine/tiles.py` implementing validation + canonical set + sort key.
- [x] **Gate:** all tests green; canonical tile set matches determinism.md fixture 2. *(Local 2026-05-19; cross-platform CI pending.)*

### Step 0.3 — Determinism primitives

Spec: [determinism.md](docs/specs/determinism.md).

- [x] Tests written (before implementation):
  - [x] `rng_bytes` golden vector (fixture 1) — checked-in `(seed, cursor, n) → bytes` table.
  - [x] `uniform_int` golden table (fixture 3) — small `n`, power-of-2, rejection edge.
  - [x] `shuffled_wall(seed=12345)` golden (fixture 4) — load-bearing.
  - [x] `canonical_hash` golden table (fixture 5) — empty dict, primitives, nested, lists.
  - [x] Lint: no `random`/`numpy.random`/`time`/`datetime`/`logging` imports under `mahjong.engine.*`.
- [x] `mahjong/engine/rng.py` with `rng_bytes`, `uniform_int`, `shuffled_wall`.
- [x] `mahjong/engine/hashing.py` with `canonical_hash`.
- [x] Lint hook (AST-based) wired into pre-commit. *(Runs via `tests/lint/` under the existing pytest hook.)*
- [x] **Gate:** all goldens byte-identical on Linux + macOS in CI. *(Local 2026-05-19 macOS green; Linux CI matrix pending first push.)*

---

## Layer 1 — engine types and PyMahjongGB wrapper

### Step 1.1 — State types

Spec: [state-schema.md](docs/specs/state-schema.md), [engine-api.md § Exception types](docs/specs/engine-api.md).

- [x] Tests written:
  - [x] Round-trip: constructed state hashes stable; permuted-but-equivalent fields hash equal.
  - [x] Validator rejects unsorted `concealed`.
  - [x] Each exception class carries the payload fields engine-api.md fixture 5 enumerates.
- [x] `mahjong/engine/types.py` with `GameState`, `SeatView`, `Action`, `RuleSetRef`.
- [x] `mahjong/engine/errors.py` with the four exception classes.
- [x] **Gate:** mypy strict passes; round-trip and invariant tests green. *(Local 2026-05-19; CI pending.)*

### Step 1.2 — PyMahjongGB wrapper

Spec: [engine-api.md § PyMahjongGB integration boundary](docs/specs/engine-api.md).

- [x] Tests written:
  - [x] Per wrapper function: checked-in MCR-canonical example (fixture 4).
  - [x] PyMahjongGB-boundary lint fires on a synthetic offending file. *(Lint added in Step 0.3; covers this case.)*
- [x] `mahjong/engine/pymj.py` with `calculate_fan`, `shanten`, `shanten_specialized`, `winning_tiles`.
- [x] AST lint wired into pre-commit. *(Same lint covers determinism + PyMahjongGB boundary.)*
- [x] **Gate:** wrapper tests green against real PyMahjongGB; lint catches violations. *(Local 2026-05-19; CI pending.)*

### Step 1.3 — Ruleset loader

Spec: [determinism.md § Ruleset config_hash](docs/specs/determinism.md).

- [x] Tests written:
  - [x] `load_ruleset({"id": "mcr-2006"})` hash matches `MANIFEST.json`.
  - [x] Unknown id → `RulesetError`.
  - [x] Mismatched `config_hash` → `RulesetError`.
- [x] `mahjong/engine/rulesets/mcr-2006.json` (canonical 81-fan config).
- [x] `mahjong/engine/rulesets/MANIFEST.json` (id → config_hash map).
- [x] `load_ruleset` in `mahjong/engine/rulesets/__init__.py`.
- [x] **Gate:** ruleset loads; hash stamps onto states. *(Local 2026-05-19; CI pending.)*

---

## Layer 2 — engine core

### Step 2.1 — `initial_state` and projection

Spec: [state-schema.md § Engine API surface, § Per-seat projection](docs/specs/state-schema.md).

- [ ] Tests written:
  - [ ] `initial_state(mcr-2006, seed=12345)` hash matches a checked-in golden.
  - [ ] Tile-count invariant: wall + concealed + flowers == 144.
  - [ ] Privacy assertion: `project(state, seat)` has zero foreign concealed tokens.
  - [ ] Self-view reversibility: `project(state, seat).seats[seat] == state.seats[seat]`.
- [ ] `mahjong/engine/state.py` with `initial_state`, `project`, `is_terminal`, `state_hash`.
- [ ] **Gate:** deal is byte-stable; projection enforces privacy.

### Step 2.2 — `legal_actions`

Spec: [state-schema.md § Action grammar](docs/specs/state-schema.md), [engine-api.md](docs/specs/engine-api.md).

- [ ] Tests written:
  - [ ] One hand-traced fixture per action type (PLAY, PENG, CHI, GANG variants, HU, PASS).
  - [ ] HU detection via `pymj.winning_tiles` on a tenpai fixture.
  - [ ] CHI legality respects "next seat only."
- [ ] `mahjong/engine/legality/discard.py` and `claim.py`.
- [ ] **Gate:** legality fixtures green per action type.

### Step 2.3 — `apply_action`

Spec: [state-schema.md](docs/specs/state-schema.md), [engine-api.md](docs/specs/engine-api.md).

- [ ] Tests written:
  - [ ] Per action type: `(state_before, action) → state_after` with state_hash golden.
  - [ ] `IllegalAction` payload completeness on every action *not* in `legal_actions`.
  - [ ] Determinism: same input → same output hash across runs.
- [ ] `mahjong/engine/transition/{play,claim,gang,hu,pass_,draw}.py`.
- [ ] **Gate:** every action type has a passing transition fixture.

### Step 2.4 — Engine end-to-end smoke

- [ ] Tests written:
  - [ ] Scripted four-PASS exhaustive-draw game reaches terminal; final hash matches golden.
  - [ ] Scripted dealer-HU toy game reaches terminal with expected fan list.
- [ ] **Gate:** the engine plays a complete hand end-to-end.

---

## Layer 3 — record store

### Step 3.1 — Record writer

Spec: [record-format.md](docs/specs/record-format.md).

- [ ] Tests written: writer round-trip, `diff_to_events` per action type, footer checksum, byte-identical serialization (fixture 1 write half).
- [ ] `mahjong/records/writer.py` + `diff.py`.
- [ ] **Gate:** events parse identically to themselves.

### Step 3.2 — Record reader / replay

Spec: [record-format.md § Verification fixtures](docs/specs/record-format.md).

- [ ] Tests written: round-trip identity, replay reproduces canonical state, per-seat projection consistency, privacy on replay, sequence integrity.
- [ ] `mahjong/records/reader.py` + `replay.py`.
- [ ] **Gate:** any record the writer produces is replayable and deterministic.

### Step 3.3 — Botzone export

Spec: [record-format.md § Botzone export](docs/specs/record-format.md).

- [ ] Tests written: hand-traced fixture → expected Botzone log; round-trip for surviving events.
- [ ] `mahjong/records/botzone_export.py`.
- [ ] **Gate:** export produces well-formed Botzone logs. (Judge acceptance waits for S1.)

---

## Layer 4 — adapter scaffolding and table manager

### Step 4.1 — `SeatAdapter` protocol and trivial adapters

Spec: [seat-port.md § The interface, § Adapter catalog](docs/specs/seat-port.md).

- [ ] Tests written: `CannedAdapter` script execution; `AutoPassAdapter` always returns default; Protocol satisfaction (mypy).
- [ ] `mahjong/adapters/base.py`, `canned.py`, `autopass.py`.
- [ ] **Gate:** adapters exist; protocol checks.

### Step 4.2 — Table manager (asyncio loop)

Spec: [seat-port.md § Lifecycle and concurrency model, § Error model](docs/specs/seat-port.md).

- [ ] Tests written: four-`CannedAdapter` hand → fixture record; timeout/illegal/crash markers + strike counter; observe-fanout independence; claim-window concurrency invariance; `AutoPassAdapter`-substitution replay preservation.
- [ ] `mahjong/table/manager.py` + `mahjong/cli/play_test.py`.
- [ ] **Gate: S0 walking-skeleton exit artifact.** `python -m mahjong play-test` plays a fixture hand; record replays byte-identically; determinism cross-platform.

---

## Layer 5 — bot runner

### Step 5.1 — Manifest loader and sandbox

Spec: [bot-runner-protocol.md § Bot manifest, § Sandboxing](docs/specs/bot-runner-protocol.md).

- [ ] Tests written: manifest validation; `RLIMIT_AS` kill (Linux); network deny (Linux).
- [ ] `mahjong/bots/manifest.py`, `sandbox.py`, `registry.py`.
- [ ] **Gate:** manifests validate; sandbox enforces on Linux.

### Step 5.2 — `BotRunnerAdapter`

Spec: [bot-runner-protocol.md § Process lifecycle, § Wire framing, § Time-budget enforcement](docs/specs/bot-runner-protocol.md).

- [ ] Tests written: HELLO success + skip; spawn failure; per-turn timeout; framing violations; illegal-action surfacing.
- [ ] `mahjong/adapters/bot_runner.py` + `mahjong/bots/sdk/`.
- [ ] **Gate:** a trivial bundled bot plays a hand against three `CannedAdapter`s.

### Step 5.3 — Botzone reference bot (S1 exit)

Spec: [bot-runner-protocol.md fixture 1](docs/specs/bot-runner-protocol.md), [server-plan.md S1](docs/server-plan.md).

- [ ] `bots/sample-botzone/` vendored or submoduled.
- [ ] Tests written: four-sample-bot hand completes (each action type covered across fixtures); Botzone judge accepts exported log.
- [ ] **Gate: S1 exit artifact.** Judge-accepted records checked in.

---

## Layer 6 — self-play harness

### Step 6.1 — `selfplay` subcommand

Spec: [selfplay-harness.md](docs/specs/selfplay-harness.md).

- [ ] Tests written: 10-hand determinism golden; crash recovery; parallel-equivalence; god-view privacy gate; rotation determinism; eval-summary correctness.
- [ ] `mahjong/cli/selfplay.py`, `mahjong/selfplay/{seeds,runner,parallel}.py`.
- [ ] **Gate:** harness produces deterministic, resumable runs.

---

## After Layer 6

Per [docs/server-plan.md](docs/server-plan.md), the next phases are S2 (TUI + WebSocket), S3 (accounts/sessions/SQLite), S4 (analysis overlays), S5 (home rules), S7 (ops hardening), S8 (spectator table). Each gets its own implementation-order pass when its phase comes up.

Per the AI plan, training work begins after the server is hostable (post-S3) and the self-play harness can generate corpus data (after Layer 6 here).
