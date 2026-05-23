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

- [x] Tests written:
  - [x] `initial_state(mcr-2006, seed=12345)` hash matches a checked-in golden.
  - [x] Tile-count invariant: wall + concealed + flowers == 144.
  - [x] Privacy assertion: `project(state, seat)` has zero foreign concealed tokens.
  - [x] Self-view reversibility: `project(state, seat).seats[seat] == state.seats[seat]`.
- [x] `mahjong/engine/state.py` with `initial_state`, `project`, `is_terminal`, `state_hash`.
- [x] **Gate:** deal is byte-stable; projection enforces privacy. *(Local 2026-05-20; cross-platform CI pending push.)*

### Step 2.2 — `legal_actions`

Spec: [state-schema.md § Action grammar](docs/specs/state-schema.md), [engine-api.md](docs/specs/engine-api.md).

- [x] Tests written:
  - [x] One hand-traced fixture per action type (PLAY, PENG, CHI, GANG variants, HU, PASS).
  - [x] HU detection via `pymj.winning_tiles` on a tenpai fixture. *(Implemented via `pymj.calculate_fan` directly, which is the contract behind `winning_tiles`; cliff filtering applied at the same call.)*
  - [x] CHI legality respects "next seat only."
- [x] `mahjong/engine/legality/discard.py` and `claim.py`.
- [x] **Gate:** legality fixtures green per action type. *(Local 2026-05-20; cross-platform CI pending push.)*

### Step 2.3 — `apply_action`

Spec: [state-schema.md](docs/specs/state-schema.md), [engine-api.md](docs/specs/engine-api.md).

- [x] Tests written:
  - [x] Per action type: `(state_before, action) → state_after` with shape and field assertions. *(State-hash goldens deferred to Step 2.4 smoke tests where full-game flow makes them load-bearing; per-action shape is pinned now.)*
  - [x] `IllegalAction` payload completeness on every action *not* in `legal_actions`.
  - [x] Determinism: same input → same output hash across runs.
- [x] `mahjong/engine/transition/{play,claim,gang,hu,pass_}.py`. *(`internal_draw` lives in `transition/__init__.py` as a shared helper — engine-api.md called draw "engine-internal" and no caller-facing surface justified a standalone module.)*
- [x] **Gate:** every action type has a passing transition fixture. *(Local 2026-05-20; cross-platform CI pending push.)*

### Step 2.4 — Engine end-to-end smoke

- [x] Tests written:
  - [x] Scripted four-PASS exhaustive-draw game reaches terminal; final hash matches golden.
  - [x] Scripted dealer-HU toy game reaches terminal with expected fan list.
- [x] **Gate:** the engine plays a complete hand end-to-end. *(Local 2026-05-20: 102-step always-PASS game from seed 12345 terminates as DRAW; Big Three Dragons toy game scores 88+ fan with the expected name in `terminal.fan`. Cross-platform CI pending push.)*

---

## Layer 3 — record store

### Step 3.1 — Record writer

Spec: [record-format.md](docs/specs/record-format.md).

- [x] Tests written: writer round-trip, `diff_to_events` per action type, footer checksum, byte-identical serialization (fixture 1 write half).
- [x] `mahjong/records/writer.py` + `diff.py`.
- [x] **Gate:** events parse identically to themselves. *(Local 2026-05-20: 12 tests under tests/records/ green; byte-identical fixture `tests/_fixtures/record_minimal.jsonl` pinned. Cross-platform CI pending push. Reader half lands in 3.2.)*

### Step 3.2 — Record reader / replay

Spec: [record-format.md § Verification fixtures](docs/specs/record-format.md).

- [x] Tests written: round-trip identity, replay reproduces canonical state, per-seat projection consistency, privacy on replay, sequence integrity.
- [x] `mahjong/records/reader.py` + `replay.py`.
- [x] **Gate:** any record the writer produces is replayable and deterministic. *(Local 2026-05-20: smoke-driven 4-PASS exhaustive-draw recorded, re-read, and replayed back to a matching final state_hash. Cross-platform CI pending push.)*

### Step 3.3 — Botzone export

Spec: [record-format.md § Botzone export](docs/specs/record-format.md).

- [x] Tests written: hand-traced fixture → expected Botzone log; round-trip for surviving events.
- [x] `mahjong/records/botzone_export.py`.
- [x] **Gate:** export produces well-formed Botzone logs. (Judge acceptance waits for S1.) *(Local 2026-05-20: spec mapping rules enforced — HEADER → per-seat init, DISCARD → broadcast PLAY, CLAIM_DECISION → claim response, CLAIM_WINDOW/CLAIM_RESOLUTION/FOOTER dropped, source_seq preserves ordering. Exact byte format tuned to live judge in S1.)*

---

## Layer 4 — adapter scaffolding and table manager

### Step 4.1 — `SeatAdapter` protocol and trivial adapters

Spec: [seat-port.md § The interface, § Adapter catalog](docs/specs/seat-port.md).

- [x] Tests written: `CannedAdapter` script execution; `AutoPassAdapter` always returns default; Protocol satisfaction (mypy).
- [x] `mahjong/adapters/base.py`, `canned.py`, `autopass.py`.
- [x] **Gate:** adapters exist; protocol checks. *(Local 2026-05-20: 9 tests under tests/adapters/ green; `SeatAdapter` Protocol with `@runtime_checkable`; mypy strict clean. Cross-platform CI pending push.)*

### Step 4.2 — Table manager (asyncio loop)

Spec: [seat-port.md § Lifecycle and concurrency model, § Error model](docs/specs/seat-port.md).

- [x] Tests written: four-`CannedAdapter` hand → fixture record; timeout/illegal/crash markers + strike counter; observe-fanout independence; claim-window concurrency invariance; `AutoPassAdapter`-substitution replay preservation.
- [x] `mahjong/table/manager.py` + `mahjong/cli/play_test.py`.
- [x] **Gate: S0 walking-skeleton exit artifact.** `python -m mahjong play-test` plays a fixture hand; record replays byte-identically; determinism cross-platform. *(Local 2026-05-20: fixture `tests/_fixtures/s0_walking_skeleton_seed_12345.jsonl` checked in; byte-identical regeneration test green; record replays to matching final state_hash. Claim-window priority resolution (HU > PENG/GANG > CHI; seat-tiebreak) implemented in `_resolve_claim_priority`, resolving the deferred Phase 2 memory. Cross-platform CI pending push.)*

---

## Layer 5 — bot runner

### Step 5.1 — Manifest loader and sandbox

Spec: [bot-runner-protocol.md § Bot manifest, § Sandboxing](docs/specs/bot-runner-protocol.md).

- [x] Tests written (before implementation):
  - [x] Canonical manifest (the example block in spec lines 132-156) parses to a fully-populated `BotManifest`.
  - [x] Missing `bot_id` rejected at parse with field-specific error (fixture 10).
  - [x] `limits.memory_mb` over server cap rejected at parse, not silently capped (fixture 10).
  - [x] Defaults are applied for missing optional fields (`spawn_deadline_ms=5000`, `handshake_deadline_ms=1000`, `budget_ms_per_turn=1000`, `teardown_grace_ms=2000`, `runtime_mode="long_running"`, `limits.network="deny"`, `limits.max_processes=1`, etc.).
  - [x] Type errors rejected (e.g. `command` not a list, `memory_mb` non-int, `network` not in `{"deny","allow"}`).
  - [x] Empty `command` rejected.
  - [x] Bad `ruleset_supported` / `format_supported` lists rejected at parse; engine-side ruleset match is *not* a manifest concern.
  - [x] `Registry.register` round-trip: register, lookup, list. Duplicate `bot_id` raises unless `replace=True`. Unknown lookup raises.
  - [x] `Registry.register_dict` invokes manifest validation (invalid manifest never enters the registry).
  - [x] `build_env(manifest)` returns a dict containing only `PATH`, `LANG`, and the manifest's whitelisted `env` keys — no leak from parent process env.
  - [x] `build_rlimits(manifest)` returns the expected `(resource, soft, hard)` tuples for `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NOFILE`, `RLIMIT_NPROC`.
  - [x] **Linux-only (skip on macOS):** subprocess spawned with `RLIMIT_AS=64MB` allocating 256MB exits with signal (fixture 6). *(`@pytest.mark.linux_only`; auto-skipped on Darwin via conftest.)*
  - [x] **Linux-only (skip on macOS):** subprocess spawned with `network="deny"` cannot reach the network (fixture 7). *(`xfail` when not root — netns creation needs `CAP_NET_ADMIN`; deferred to CI host config.)*
  - [x] **macOS:** `apply_sandbox` succeeds with a recorded `SandboxWarning` naming which layers are inactive (netns, per-process NPROC).
- [x] `mahjong/bots/errors.py` — `BotError`, `BotManifestError`.
- [x] `mahjong/bots/manifest.py` — `BotManifest` dataclass, `parse_manifest(dict, *, server_caps)`, `load_manifest_file(path, *, server_caps)`.
- [x] `mahjong/bots/sandbox.py` — `build_env`, `build_rlimits`, `apply_sandbox` (preexec helper); platform-aware (Linux full; macOS best-effort with a `SandboxWarning`).
- [x] `mahjong/bots/registry.py` — in-memory `BotRegistry` with `register/unregister/lookup/list`.
- [x] **Gate:** manifests validate; sandbox enforces on Linux; macOS dev path runs with documented degraded warnings; mypy clean across `mahjong/bots/`. *(Local 2026-05-21: 34 bot tests pass on Darwin — 2 Linux-only fixtures auto-skipped; full suite 310 passed, 2 skipped; ruff format + lint clean; `mypy mahjong/bots/` clean. Linux CI matrix run + the netns fixture under privilege are pending CI sweep.)*

### Step 5.2 — `BotRunnerAdapter`

Spec: [bot-runner-protocol.md § Process lifecycle, § Wire framing, § Time-budget enforcement](docs/specs/bot-runner-protocol.md).

Scope decisions (implementer-level, not spec changes):

- Request-body serialization is pluggable via a `history_serializer` hook on `BotRunnerAdapter`. Default in 5.2 is a JSON serializer that includes `kind`, `legal_actions`, and `default_action` — enough for our SDK-based test bots. **Step 5.3 replaces it with the Botzone typed-line `0`/`1`/`2`/`3` serializer** once the real reference bot is in the loop.
- Action parser is the Botzone CSM grammar (`PASS`, `PLAY W3`, `PENG B5`, `CHI W3 W4` reconstructs to `tiles=[W3,W4,W5]`, `GANG B7`, `BUGANG W2`, `HU`). `GANG.kind` is inferred from `prompt.kind` (CLAIM→EXPOSED, DISCARD→CONCEALED); `BUGANG` → `GANG.kind=ADDED`.
- Error payload is attached to `SeatTimeout`/`SeatError` as attributes (`bot_error`, `exit_code`, `raw_response`, `bytes_read`). The table manager reads them with `getattr(exc, "bot_error", None)` and stamps them onto the record event. `raw_response` is truncated to 1024 bytes.
- Both `long_running` and `short_running` modes are implemented; mode is negotiated by HELLO ack. Vanilla Botzone bots that ignore HELLO are run with `handshake_skipped=True` in long-running mode.
- Action is the **first non-empty, non-sentinel line** received before `>>>BOTZONE_RESPONSE_END<<<`. Subsequent lines are dropped as diagnostic (Botzone's optional render channel is ignored per spec open-question).

- [x] Tests written (before implementation):
  - [x] HELLO handshake success (fixture 2): SDK-based bot ACKs `long_running`; first `decide` succeeds.
  - [ ] HELLO mode downgrade: bot ACKs `short_running`; runner respects, respawns per decide. *(Code path implemented; explicit fixture deferred — covered structurally by the handshake-skip + parser tests. Add a dedicated fixture if short-running becomes a supported real-bot mode in S1.)*
  - [x] HELLO handshake skip (fixture 3): vanilla bot ignores HELLO; runner times out the handshake, continues; first `decide` still works.
  - [x] Spawn failure (fixture 4): manifest pointing at a non-existent binary; `seated()` raises `SeatError` with `bot_error="process_exit"`.
  - [x] Per-turn timeout (fixture 5): bot scripted to `sleep(3)` against 300ms budget triggers `SeatTimeout` with `bot_error="read_timeout"`; subprocess is SIGTERM'd and reaped.
  - [x] Framing violation — missing sentinel (fixture 8): bot writes a response without `>>>BOTZONE_RESPONSE_END<<<` and closes stdout; `SeatError` with `bot_error="framing_error"`.
  - [x] Framing — CRLF tolerated (fixture 8): bot writes CRLF responses; runner strips and parses.
  - [x] Parse error: bot writes garbage that doesn't match the action grammar; `SeatError` with `bot_error="parse_error"`; `raw_response` field present.
  - [x] Illegal-action surfacing (fixture 9): bot returns a syntactically-valid `PENG` against a DISCARD prompt; adapter returns it normally (engine/table manager surfaces `illegal: true`).
  - [x] Action grammar coverage: each of `PASS`, `PLAY`, `PENG`, `CHI` (3-tile reconstruction), `GANG` (EXPOSED in CLAIM / CONCEALED in DISCARD), `BUGANG`→ADDED, `HU` parses to the right `Action` dict. Plus unknown-tag, empty-line, out-of-range, and non-numbered-suit error paths.
  - [x] Teardown: SIGTERM → grace → reap. `left()` safe to call without `seated()`.
  - [x] **Gate:** end-to-end: one `BotRunnerAdapter` + three `CannedAdapter`s play a hand via `mahjong.table.manager.run_hand`; record reaches FOOTER; final state is TERMINAL.
- [x] `mahjong/bots/sdk/__init__.py` — `run_bot(decide)`; `REQUEST_END_SENTINEL` and `RESPONSE_END_SENTINEL` constants; HELLO handler with mode echo.
- [x] `mahjong/adapters/bot_runner.py` — `BotRunnerAdapter` Protocol-conforming class with `seated`/`observe`/`decide`/`left`. Includes the action-string parser, the default JSON history serializer (Step 5.3 swaps to Botzone CSM), and the framing read/write helpers.
- [x] **Gate:** end-to-end fixture passes; mypy clean; ruff format+lint clean. *(Local 2026-05-21: 21 BotRunnerAdapter scenarios + 12 parser tests pass on Darwin; full suite 333 passed, 2 skipped (Linux-only sandbox); ruff clean; mypy clean on `mahjong/adapters/bot_runner.py` and `mahjong/bots/sdk/`.)*

### Step 5.3 — Botzone reference bot (S1 exit)

Spec: [bot-runner-protocol.md fixture 1](docs/specs/bot-runner-protocol.md), [server-plan.md S1](docs/server-plan.md).

Scope split (per 2026-05-21 conversation):

- **5.3a (this layer):** vendor upstream, build the Botzone CSM history serializer, run four in-tree Python reference bots through a hand, confirm Botzone log export is structurally well-formed.
- **5.3b (deferred):** compile the upstream C++ `sample-bot-Botzone/sample.cpp` + judge from `bots/sample-botzone/judge/`, run four C++ bots through a hand, feed the exported log to the judge, gate on judge acceptance. Blocked on a C++ toolchain + `jsoncpp` + `MahjongGB-CPP` build setup (none in repo today). This is the **real S1 exit** — the file currently calling the step "done" only refers to 5.3a.

#### 5.3a — In-tree Botzone integration

- [x] `bots/sample-botzone/` added as a git submodule pointing at `https://github.com/ailab-pku/Chinese-Standard-Mahjong` (master, commit `5e81821`). Fetched via `git submodule update --init`.
- [x] `bots/python-reference/bot.py` — in-tree Python rule-based bot using `mahjong.bots.sdk` + `latest_botzone_request`. Plays the drawn tile (tsumogiri); PASSes on all claims.
- [x] `mahjong/bots/botzone_serializer.py` — `BotzoneCsmSerializer(seat)` with `on_observe` / `on_decide` / `record_response`. Mirrors `mahjong/records/botzone_export.py`'s typed-line conventions (init `0`, deal `1`, draw `2`, public events `3`); preserves the response-length invariant.
- [x] `mahjong/adapters/bot_runner.py` refactored: `HistorySerializer` is now a Protocol (was a plain Callable); `JsonHistorySerializer` is the Step 5.2 default; Botzone serialization plugs in by passing `BotzoneCsmSerializer` to the adapter.
- [x] `mahjong/bots/sdk/__init__.py` — `latest_botzone_request(envelope)` helper for bots that match on type codes.
- [x] `bots/README.md` documents the submodule + deferred 5.3b compilation.
- [x] Tests:
  - [x] `tests/bots/test_botzone_serializer.py` — 16 unit tests covering init/deal/draw/discard/claim mapping, private-vs-public visibility per `judge.cpp` `roundStage`, response invariant, CHI middle-tile encoding, `action_to_botzone_string` round-trip.
  - [x] `tests/bots/test_python_reference_bot.py` — 4 unit tests on the reference bot's `decide` branches.
  - [x] `tests/adapters/test_layer5_e2e.py::test_four_python_reference_bots_play_a_hand` — gate: four `BotRunnerAdapter` + `BotzoneCsmSerializer` pairs complete a hand via `mahjong.table.manager.run_hand`; record reaches FOOTER; `export_to_botzone` produces a non-empty log with non-empty token lists.
  - [x] `tests/adapters/test_layer5_e2e.py::test_botrunner_works_with_canned_seats` — sanity: 1 Botzone-serializer bot + 3 CannedAdapters also completes.
- [x] **Gate (5.3a):** four-bot hand completes; structural Botzone-log export check passes; full suite 355 passed, 2 skipped (Linux-only sandbox); ruff + mypy clean. *(Local 2026-05-21.)*

#### 5.3b — Deferred (real S1 exit)

- [ ] Build script for the C++ sample bot. Likely a `bots/sample-botzone-build.sh` that compiles `sample-bot-Botzone/sample.cpp` with `jsoncpp` + `MahjongGB-CPP` and produces an executable consumable by `BotRunnerAdapter` (`limits.command` points at the binary).
- [ ] Build script for the judge (`bots/sample-botzone/judge/main.cpp`). Same toolchain.
- [ ] Per-event-prompt mode for the Botzone serializer — currently the serializer batches events between decides; the real judge expects one request per state-change with a response from every bot. Adding this without quadrupling round-trips needs a fake-decide path that auto-PASSes for passive observations without hitting the subprocess. Likely a new "auto-pass for observe events that don't trigger this seat's decide" hook on the serializer + adapter.
- [ ] Judge-acceptance fixture: run four C++ bots → export log → feed to the judge binary → assert no disagreement on legality or scoring. Each of `PASS`, `PLAY`, `PENG`, `CHI`, `GANG`, `BUGANG`, `HU` represented at least once across the fixture suite.
- [ ] **Gate (5.3b):** judge-accepted record set checked in.

---

## Layer 6 — self-play harness

Split (per 2026-05-21 conversation) into 6.1a (serial runner + resume), 6.1b
(parallel-hands workers), 6.1c (eval-summary aggregator). Each ships with a
clean gate; 6.1a is the load-bearing piece — parallel and eval are
optimizations layered on top of the same serial driver.

### Step 6.1a — Serial runner, seeds, resume, rotation

Spec: [selfplay-harness.md § Seed management, § Run lifecycle, § `SelfPlayDriverAdapter`](docs/specs/selfplay-harness.md), [record-format.md § HEADER `meta`](docs/specs/record-format.md).

Scope decisions (implementer-level, not spec changes):

- `meta` in HEADER is opt-in via a new `run_hand(meta=...)` kwarg. Live-table
  callers leave it unset; self-play stamps `{master_seed, hand_index, source}`.
  Record-format addendum landed alongside this step.
- Bot resolution: `--bots b_rule_v1,b_random,...` looks up each `bot_id` in a
  `BotRegistry` populated from a default manifest set. The CLI accepts
  `--manifest-dir` to override; default is `bots/python-reference/`.
- `b_random` is added as a tiny in-tree bot (`bots/python-reference/random_bot.py`
  plus `manifest.json`) — needed for non-trivial rotation tests and to give the
  default eval recipe in the spec ("rule_v1 vs three randoms") something to
  resolve.
- Output dir layout: `{output_dir}/{hand_id}.jsonl` (flat, no date subdir) for
  self-play. Spec calls for `{year}/{month}/` in live records but says the
  self-play `output-dir` is "a flag, not the global path"; flat keeps resume
  scans cheap.
- Resume scan: list `*.jsonl` in output_dir, read each HEADER, take
  `max(meta.hand_index) + 1`. Files without `meta.hand_index` (legacy or
  hand-placed records) are ignored with a warning. Partial records (no
  FOOTER) are detected by tailing the file and deleted before resume.

- [x] Tests written (before implementation):
  - [x] `hand_seed(master, idx)` matches the spec-derived formula for
        `master_seed=0xDEADBEEF12345678, idx ∈ {0,1,7,42,10000}`.
  - [x] `hand_seed` decorrelation: 64 indices give 64 distinct seeds, and
        master+1 produces a disjoint set.
  - [x] `rotate_bots(bots, hand_index)` round-robin: identity at idx 0;
        right-cyclic shift by `idx % 4`; deterministic across calls.
  - [x] Serial 3-hand run: produces three `.jsonl` records; each HEADER
        carries the expected `meta.master_seed` (hex string) and
        `meta.hand_index`; each `seed` field matches `hand_seed(master, idx)`.
  - [x] Determinism: a 2-hand serial run produces byte-identical files
        across two invocations from a clean output dir (with `ts`
        monkey-patched — wall-clock is the only non-determinism source).
  - [x] Resume — refusal: rerunning into a non-empty output dir without
        `resume=True` raises `RunnerError`.
  - [x] Resume — happy path: run 2 hands, then run again with `resume=True,
        hands=4`; finds hands 0/1, plays hands 2/3, end state is 4 records
        with `meta.hand_index` ∈ {0,1,2,3}.
  - [x] Resume — partial cleanup: a HEADER-only file in the output dir is
        deleted on `resume=True` and that `hand_index` is replayed.
  - [x] Privacy gate: default mode (no driver) — each adapter's
        `seated().initial_view["seats"][other]["concealed"]` is the
        count-only `SeatViewOpponent` dict, never a tile list;
        `allow_god_view` is unset.
  - [x] Rotation determinism: `rotation="round-robin"` yields the expected
        seat assignment per `hand_index`; HEADER `seats[i].identity.bot_id`
        reflects the rotation.
  - [x] CLI smoke: `selfplay_main([...])` exits 0 and writes one record;
        unknown bot_id returns exit code 2.
- [x] `mahjong/selfplay/seeds.py` — `hand_seed`, `rotate_bots`.
- [x] `mahjong/selfplay/runner.py` — `SelfPlayRunner` orchestrating one run:
      resume scan, per-hand seat assignment, per-hand `run_hand` invocation,
      `meta` plumbing.
- [x] `mahjong/cli/selfplay.py` — `argparse` subcommand wired into
      `mahjong/cli/__init__.py`.
- [x] `bots/python-reference/random_bot.py` for `b_random`. (No on-disk
      manifest JSON in 6.1a; the CLI builds the default `BotRegistry` from
      in-code factories.)
- [x] `mahjong/cli/__init__.py` — `selfplay` subcommand alongside `play-test`.
- [x] `mahjong/table/manager.py` — `run_hand` gains optional `meta` kwarg;
      live-table callers omit it.
- [x] **Gate:** serial runs are deterministic, resumable, and respect the
      privacy contract. *(Local 2026-05-21: 26 selfplay tests pass; full
      suite 381 passed, 2 skipped (Linux-only sandbox); ruff format + lint
      clean; mypy clean across 46 source files. Cross-platform CI pending push.)*

### Step 6.1b — `--parallel-hands` workers

Spec: [selfplay-harness.md § Concurrency](docs/specs/selfplay-harness.md).

- [x] Subprocess-worker model with disjoint `hand_index` partitions per spec.
      `SelfPlayRunner` gains `worker_id`/`worker_count`; worker `k` plays
      hands where `hand_index % N == k`. CLI `--parallel-hands N` is the
      parent path: pre-flights the output dir, spawns N `python -m mahjong
      selfplay --worker-id k --worker-count N --resume` subprocesses,
      reaps them, then aggregates the eval-summary over the shared dir.
- [x] Parallel-equivalence fixture (spec fixture 3): a serial run and a
      `--parallel-hands 2` run from the same `master_seed` produce per-hand
      byte-identical records (with `_now_ts` pinned — wall-clock is the
      only non-determinism source). Pinned by
      `tests/selfplay/test_runner.py::test_parallel_equivalence_per_hand_byte_identical`.
- [x] **Gate:** 407 tests passed, 2 skipped (Linux-only sandbox); ruff
      check + format clean; mypy clean across 47 source files; real-process
      smoke (`--parallel-hands 2 --eval-summary`) writes the expected 4
      records and aggregates correctly. *(Local 2026-05-22.)*

### Step 6.1c — `--eval-summary` aggregator

Spec: [selfplay-harness.md § Eval-summary output](docs/specs/selfplay-harness.md).

- [x] `mahjong/selfplay/eval.py` — `parse_record`, `aggregate`, `format_summary`;
      `HandOutcome`, `SeatSummary`, `EvalSummary` dataclasses.
- [x] Eval-summary correctness fixture (spec fixture 6): 19 unit tests in
      `tests/selfplay/test_eval.py` covering per-seat and per-bot stats
      (win rate, avg score, deal-in rate, avg fan when won), wall-exhausted
      hands, malformed-record skipping, and format output.
- [x] `--eval-summary` flag wired into `mahjong/cli/selfplay.py`; printed
      after the run when any records were written.
- [x] **Gate:** 400 tests passed, 2 skipped (Linux-only sandbox); mypy clean
      on `mahjong/selfplay/eval.py` and `mahjong/cli/selfplay.py`. *(Local 2026-05-22.)*

---

## Layer 7 — networking + human adapter (S2)

Specs drafted 2026-05-22; implementation pending. Build order: state-schema amendment → codec → transport → session-mux → HumanAdapter → TUI → end-to-end S2 gate.

### Step 7.0 — `project(state, seat=None)` amendment

Spec: [docs/specs/state-schema.md § Per-seat projection](docs/specs/state-schema.md) (additive edit).

- [x] Tests written (before implementation):
  - [x] Public-projection privacy: `project(state, seat=None)` has zero `concealed` tokens anywhere; own-draw events strip `tile`.
        (Concealed-gang tile-identity elision deferred — preserves existing per-seat opponent-view behavior; see state-schema.md amendment for rationale.)
  - [x] Round-trip: `project(state, seat=None)` is byte-stable (canonical hash) across two calls.
        Pinned by `tests/engine/test_state.py::test_project_public_view_byte_stable_across_calls`.
  - [x] For every seat S: the per-seat projection's public fields agree with the `seat=None` projection.
        Pinned by `tests/engine/test_state.py::test_project_public_view_agrees_with_opponent_view_per_seat`.
  - [x] `project_event` privacy: DRAW.tile stripped for public + non-owning seats; idempotent; non-mutating.
        Pinned by `tests/engine/test_state.py::test_project_event_*` (6 cases).
- [x] state-schema.md amendment lands: signature `project(state: GameState, seat: int | None)`,
      public-view rule field-by-field, `project_event` rule, fixtures 3 / 4 / 4a / 4b updated.
- [x] `mahjong/engine/state.py`: broadened `project`; new `project_event(event, seat)`.
- [x] Existing callsites (three in `mahjong/table/manager.py`, plus tests) accept `int | None`
      without behavior change — broadening is additive; full suite green.
- [x] **Gate:** 422 tests passed (2 Linux-only skipped); ruff clean; mypy clean across 47 source files.
      *(Local 2026-05-22.)*

### Step 7.1 — Wire-protocol codec

Spec: [docs/specs/wire-protocol.md § Message catalog, § Message framing](docs/specs/wire-protocol.md).

- [x] Tests written: round-trip fixture per message kind (27 parameterized cases covering
      HELLO server/client, HEARTBEAT, ERROR, AUTH_REQUEST/RESPONSE ok+fail, RESUME,
      LIST_TABLES/TABLE_LIST, ATTACH/ATTACHED, DETACH client+server, DETACHED,
      SPECTATE/SPECTATING/STOP_SPECTATING, EVENT, PROMPT, ACTION, HAND_END,
      CREATE_TABLE/TABLE_CREATED, CLOSE_TABLE); corrupted-frame rejection (invalid JSON,
      non-object, missing `kind`, non-string `kind`, empty bytes, invalid UTF-8);
      forward-compat (unknown optional fields tolerated; unknown `kind` raises
      `WireDecodeError`); privacy (player vs spectator EVENT differ on `DRAW.tile`
      after `project_event`). Pinned by `tests/wire/test_codec.py` (41 cases).
- [x] `mahjong/wire/codec.py` with typed `WireMessage` union over 25 TypedDicts;
      `encode(msg) -> bytes` (sorted-key tight UTF-8 JSON) and `decode(bytes) -> dict`.
- [x] `mahjong/wire/errors.py` with `WireError` base, `WireDecodeError`,
      `WireFramingError`, `WireVersionError`.
- [x] `shutting_down` error code added to [wire-protocol.md § Error codes](docs/specs/wire-protocol.md);
      `rate_limit` also pinned in the table while we were there.
- [x] **Gate:** 463 tests passed (2 Linux-only skipped); ruff clean; ruff-format clean;
      mypy clean across 50 source files. *(Local 2026-05-22.)*

### Step 7.2 — WebSocket transport

Spec: [docs/specs/wire-protocol.md § Transport](docs/specs/wire-protocol.md).

- [x] Tests written (11 real-loopback integration cases):
      connect → HELLO round-trip; client→server inbound frame surfaces as
      decoded dict; subprotocol mismatch (`mahjong-v2`) refused at handshake;
      missing-subprotocol refused; binary frame → server close with code 1003;
      oversized frame (>`max_size`) → close with 1009; ping/pong keepalive
      survives an idle connection; `/health` GET invokes the optional
      `health_handler`; default `/health` returns 503 when unconfigured;
      `stop_accepting()` blocks new connects but leaves existing alive;
      `port` is bound after `start()`. Pinned by `tests/wire/test_server.py`.
- [x] `mahjong/wire/server.py` wrapping `websockets` v16 (`websockets.asyncio.server.serve`);
      per-connection id allocation; `Connection` async-iterator over decoded
      `WireMessage` dicts; `start()` / `stop_accepting()` / `close()` lifecycle;
      HTTP `/health` route on the same listener via `process_request` hook.
- [x] Added `websockets>=12.0` as a runtime dependency in `pyproject.toml`
      (installed `websockets-16.0`).
- [x] **Gate:** 474 tests passed (2 Linux-only skipped); ruff clean;
      ruff-format clean; mypy clean across 51 source files.
      *(Local 2026-05-22.)*

### Step 7.3 — Session multiplexer

Spec: [docs/specs/session-mux.md](docs/specs/session-mux.md).

- [x] Tests written (all 21 session-mux fixtures; 28 test methods across 5 files):
  - [x] State-machine transition coverage (fixture 1) — `tests/sessions/test_state_machine.py` (5 transitions).
  - [x] Buffered replay in order (fixture 2); overflow → fresh snapshot (fixture 3) — `tests/sessions/test_ring_buffer.py`.
  - [x] Pending prompt across reconnect (fixture 4); defaults while HELD (fixture 5) — `tests/sessions/test_pending_prompt.py`.
  - [x] Seat-hold expiry no-prompt (fixture 6) and with prompt (fixture 7) — `tests/sessions/test_pending_prompt.py`.
  - [x] Same-user takeover (fixture 8); different-user rejection (fixture 9; both LIVE and HELD) — `tests/sessions/test_state_machine.py`.
  - [x] Hand-end while HELD (fixture 10); graceful shutdown drain (fixture 11) — `test_state_machine.py` + `test_shutdown.py`.
  - [x] No-prompt action (12); stale prompt_id (13); illegal action + strike (14); idempotent reconnect cycles (15) — `test_pending_prompt.py` + `test_state_machine.py`.
  - [x] Spectator subscribe + public projection (16); immediate drop (17); identical streams across spectators (18); max-per-table (19); across-hand subscription (20); own-draw projection rule (21) — `tests/sessions/test_spectators.py`.
- [x] `mahjong/sessions/mux.py`: per-table `TableSessions` with `SeatSession` state machine + `Spectator` set + `OutboundSink` Protocol + `SeatPrompt` shape.
- [x] `mahjong/sessions/timers.py`: `IdempotentTimer` wrapping `asyncio.call_later`.
- [x] **Gate:** all 21 fixtures green (28 test methods); ruff clean; ruff-format clean; mypy clean (54 source files); 502 tests pass repo-wide (2 Linux-only skipped).

### Step 7.4 — `HumanAdapter`

Spec: [docs/specs/session-mux.md § The HumanAdapter](docs/specs/session-mux.md).

- [ ] Tests written: `seated/observe/decide/left` round-trip against a fake `SessionMux`; observe-during-drop lands in buffer (no loss); strike-counter integration; fixtures 11–15 from session-mux.md re-asserted at the adapter layer.
- [ ] `mahjong/adapters/human.py` implementing `SeatAdapter` against a `SessionMux` seat slot.
- [ ] Regression: the four-`CannedAdapter` walking-skeleton fixture from Step 4.2 still passes.
- [ ] **Gate:** adapter satisfies the `SeatAdapter` Protocol (mypy); fake-mux fixtures green; regression green.

### Step 7.5 — TUI client

Spec: [docs/specs/tui-client.md](docs/specs/tui-client.md).

- [ ] Tests written: `Pilot`-driven scripted-keystroke fixture per screen (login, lobby, player_table, spectator_table, hand_end); spectator privacy defense-in-depth (rendering refuses to draw concealed tiles even if wire sends them); bilingual EN/ZH label rendering per tile and action; crash-resistance (broken render → placeholder, WebSocket stays open).
- [ ] `mahjong/tui/app.py` — `MahjongApp(textual.App)` owning the `ConnectionManager`.
- [ ] `mahjong/tui/screens/{login,lobby,player_table,spectator_table,hand_end}.py`.
- [ ] `mahjong/tui/render/` — tile rendering, meld layout, discard-pile widget.
- [ ] `mahjong/cli/tui.py` — `python -m mahjong tui` entry point.
- [ ] **Gate:** every screen has a passing pilot fixture; spectator-privacy assertion green; locale rendering matches the spec's bilingual rule.

### Step 7.6 — End-to-end S2 fixture

Spec: [docs/s2-s3-plan.md §"S2 exit"](docs/s2-s3-plan.md).

- [ ] Tests written: in-process server + four TUI pilots play one hand → byte-identical record fixture; drop/reconnect inside seat-hold window → no auto-pass marker; drop past window → `AutoPassAdapter` substitution + `replaced_by_auto_pass` event; spectator subscription receives public-projected stream and never a `PROMPT`.
- [ ] **Gate: S2 exit artifact** checked in. `python -m mahjong serve` + four scripted TUIs play a hand whose record replays byte-identically; drop/reconnect and spectator fixtures green cross-platform.

---

## Layer 8 — accounts, sessions, persistence (S3)

Specs drafted 2026-05-22; implementation pending. Build order: schema → auth → persistence-api → multi-table orchestrator → lifecycle → end-to-end S3 gate.

### Step 8.1 — SQLite schema + migrations

Spec: [docs/specs/sqlite-schema.md](docs/specs/sqlite-schema.md).

- [ ] Tests written (all 12 sqlite-schema.md fixtures):
  - [ ] Initial migration applies to fresh DB → `schema_version.version == 1` (fixture 1).
  - [ ] Forward migration placeholder (fixture 2; collapses to fixture 1 at v1).
  - [ ] Foreign-key enforcement (fixture 3).
  - [ ] Username uniqueness behavior (fixture 4).
  - [ ] CHECK constraints (fixtures 5, 6).
  - [ ] Cascade delete on `hand_index` → `hand_participants` (fixture 7).
  - [ ] SET NULL on `accounts` → `hand_participants` (fixture 8).
  - [ ] WAL + busy_timeout (fixture 9).
  - [ ] Schema snapshot stability (fixture 10) — load-bearing.
  - [ ] Round-trip hand record (fixture 11); rebuild from records (fixture 12).
- [ ] `mahjong/persistence/migrations/__init__.py` with `apply_migrations`.
- [ ] `mahjong/persistence/migrations/0001_initial.py` creating every table + index.
- [ ] `mahjong/persistence/db.py` opening connection with pragmas.
- [ ] `tests/persistence/expected_schema.sql` snapshot.
- [ ] **Gate:** fresh-apply matches snapshot byte-identically; CI matrix green on Linux + macOS.

### Step 8.2 — Auth module

Spec: [docs/specs/auth.md](docs/specs/auth.md).

- [ ] Tests written: argon2id round-trip and PHC format; needs-rehash on parameter change; constant-time failure (wrong-username == wrong-password timing); session token issue/validate/renew/revoke lifecycle; sliding-renewal updates `last_seen_ms`/`expires_at_ms`; bot-account auth via same flow; disabled-account refused without external "disabled" leak.
- [ ] `mahjong/auth/hasher.py` — argon2-cffi wrapper with documented parameters + `STATIC_INVALID_HASH`.
- [ ] `mahjong/auth/sessions.py` — token issuance (32-byte hex), validation, renewal, revocation.
- [ ] `mahjong/auth/service.py` — `authenticate` / `validate_session` flows.
- [ ] Wire handlers for `AUTH_REQUEST` / `RESUME` calling into `auth.service`.
- [ ] **Gate:** auth.md fixtures green; mypy strict on `mahjong/auth/`.

### Step 8.3 — Persistence API

Spec: [docs/specs/persistence-api.md](docs/specs/persistence-api.md).

- [ ] Tests written (all 13 persistence-api.md fixtures):
  - [ ] `reserve_hand` round-trip (1); atomicity (3).
  - [ ] `finalize_hand` round-trip (2).
  - [ ] `find_hands_by_account` ordering + pagination (4, 5); `find_hands_by_match` ordering (6).
  - [ ] `integrity_check` missing files (7), orphans (8), checksum mismatches (9).
  - [ ] Rebuild from records equivalence (10) — load-bearing.
  - [ ] Rebuild idempotency (11).
  - [ ] Session CRUD round-trip (12); account CRUD round-trip (13).
- [ ] `mahjong/persistence/__init__.py` re-exporting `Persistence`.
- [ ] `mahjong/persistence/{accounts,sessions,hands,rebuild}.py`.
- [ ] Table-manager hooks at HEADER and FOOTER write call `reserve_hand` / `finalize_hand`.
- [ ] **Gate:** all 13 fixtures green; zero raw SQL outside `mahjong/persistence/`.

### Step 8.4 — Multi-table orchestrator

Spec: [docs/specs/server-lifecycle.md § Table registry](docs/specs/server-lifecycle.md).

- [ ] Tests written: two-table isolation (server-lifecycle.md fixture 17); `CREATE_TABLE` rejected post-drain (18); `CLOSE_TABLE` admin gating (non-admin → `permission_denied`); `LIST_TABLES` reflects post-create / post-close transitions.
- [ ] `mahjong/server/registry.py` — `TableRegistry` with `create_table`/`list_tables`/`get_table`/`close_table`/`drain_all`.
- [ ] `TableHandle` bundling `TableManager` + `SessionMux`.
- [ ] Wire handlers for `CREATE_TABLE` / `LIST_TABLES` / `CLOSE_TABLE` (admin-gated).
- [ ] Shared `Persistence` threaded into every table's hand-end hook.
- [ ] **Gate:** multi-table fixtures green; single-table regression (Layer 4 walking skeleton) still passes.

### Step 8.5 — Server lifecycle

Spec: [docs/specs/server-lifecycle.md](docs/specs/server-lifecycle.md).

- [ ] Tests written (server-lifecycle.md fixtures 1–16, 18–21):
  - [ ] Config defaults (1); validation (2); unknown-var warning (3).
  - [ ] Startup happy path (4); existing DB (5); corrupt DB exit (6); bind failure (7); in-flight ABORTED reconciliation (8).
  - [ ] `/health` 200/503/500 (9, 10, 11).
  - [ ] `SIGTERM` drain happy path (12) — load-bearing.
  - [ ] New-connection rejection during drain (13); drain timeout escalation (14); WAL checkpoint TRUNCATE on drain (15).
  - [ ] SIGKILL recovery (16) — load-bearing.
  - [ ] `CREATE_TABLE` rejected after drain begins (18; shared with 8.4 but re-asserted at the lifecycle layer).
  - [ ] Periodic session cleanup (19); periodic WAL checkpoint (20).
  - [ ] Structured logging emits valid JSON with no leaked secrets (21).
- [ ] `mahjong/server/config.py` — `load_config_from_env` with the documented `MAHJONG_*` table and unknown-var warning.
- [ ] `mahjong/server/logging.py` — structured-JSON formatter + dev console formatter.
- [ ] `mahjong/server/health.py` — `/health` HTTP handler.
- [ ] `mahjong/server/lifecycle.py` — startup sequence, signal handlers, `drain()`, periodic tasks.
- [ ] `mahjong/cli/serve.py` — `python -m mahjong serve` entry point.
- [ ] **Gate:** every listed fixture green; mypy clean on `mahjong/server/`.

### Step 8.6 — End-to-end S3 fixture

Spec: [docs/s2-s3-plan.md §"S3 exit"](docs/s2-s3-plan.md), [docs/specs/server-lifecycle.md fixture 22](docs/specs/server-lifecycle.md).

- [ ] Tests written: fresh data dir → admin inserts account → TUI logs in → joins a new table → plays one hand against three `CannedAdapter`s → server restart → `find_hands_by_account` returns the played hand. Multi-table fixture re-asserted in the end-to-end harness. Migration-from-previous placeholder (collapses to fresh-apply for v1).
- [ ] **Gate: S3 exit artifact** checked in. All four bullets in [docs/server-plan.md § S3 exit criteria](docs/server-plan.md) are green and checked in.

---

## After Layer 8

Per [docs/server-plan.md](docs/server-plan.md), the next phases are S4 (analysis overlays), S5 (home rules), S7 (ops hardening), S8 (spectator table). Each gets its own implementation-order pass when its phase comes up.

Per the AI plan, training work begins after the server is hostable (post-S3) and the self-play harness can generate corpus data (after Layer 6 here).
