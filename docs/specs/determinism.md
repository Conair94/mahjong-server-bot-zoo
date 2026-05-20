# Spec 5 — Determinism contract

The cross-cutting contract that makes every other spec testable: **same seed + same inputs → byte-identical trace, across runs, across machines, across years.** Every prior spec (state schema, record format, seat port, bot runner) has assumed this contract; this doc pins it.

Without determinism, the AI plan's verification ladder loses its teeth. "Did this refactor change behavior?" becomes "did the curves look similar?" — and that's the failure mode CLAUDE.md is built to prevent.

**Status:** draft, pre-S0.

## Goals

- **One canonical RNG.** A single, fully-specified algorithm produces every random byte the engine consumes. Not `random.random()`, not `numpy.random`, not "whatever's convenient" — *this* algorithm, defined here.
- **One canonical hash.** A single, fully-specified serialization → SHA-256 procedure produces every `state_hash` and `config_hash`. Two states are equal iff their hashes are equal, by construction.
- **Cross-platform stability.** macOS dev host and Linux production host produce byte-identical hashes for the same seed + actions. CI proves it.
- **Cross-version stability.** A record written today replays cleanly five years from now, *as long as the ruleset's `config_hash` matches*. Engine refactors that change behavior are flagged loudly via hash changes — that's a feature, not a bug.
- **No side channels for randomness.** Bot inference is allowed to be non-deterministic (a neural net may use whatever it wants internally); but the *engine's* state transitions are 100% determined by (state, action). The boundary is clean.

## Non-goals

- **Not a security RNG.** This RNG is not cryptographically secure for game-theoretic adversary purposes (an attacker who can predict the wall has a meaningful edge). Mitigation: seeds are server-generated from `secrets.token_bytes(8)` per hand and never disclosed to seats until the hand is over. Adequate for friends-and-family scale; document the limit.
- **Not a bot-internal contract.** Bots may use any RNG they want internally. The contract is about the *engine*, not the bots inside the engine.
- **Not a wall-clock contract.** Timing data in records (`ts`, `decision_ms`) is intrinsically non-deterministic; it's recorded for diagnostics, never reproduced. Determinism tests exclude these fields.

## The RNG

### Algorithm

A **SHA-256 counter DRBG**. Specified to byte-level precision so it can be reimplemented in any language without ambiguity:

```python
def rng_bytes(seed: int, cursor: int, n: int) -> bytes:
    """Return n bytes of the deterministic stream for (seed, cursor).
    Advances 'cursor' is the caller's job — this function is pure."""
    out = bytearray()
    block_index = cursor // 32        # SHA-256 outputs 32 bytes per block
    byte_offset_in_block = cursor % 32
    while len(out) < n + byte_offset_in_block:
        block_input = (
            seed.to_bytes(16, "big", signed=False) +
            block_index.to_bytes(16, "big", signed=False)
        )
        out.extend(hashlib.sha256(block_input).digest())
        block_index += 1
    return bytes(out[byte_offset_in_block : byte_offset_in_block + n])
```

- **Seed:** 128-bit unsigned integer (16 bytes, big-endian). Generated server-side per hand via `secrets.token_bytes(16)`; stored in `record.HEADER.seed` as an integer.
- **Block index:** 128-bit unsigned integer, big-endian. Concatenated with seed; the concatenation feeds one SHA-256 call to produce 32 bytes of stream.
- **Cursor:** current byte offset into the stream. Stored in `GameState.rng.cursor` and incremented every time the engine consumes randomness.

This is the **only** randomness source the engine ever uses. Not for shuffling, not for tie-breaks, not for anything.

### Operations built on it

Two operations consume RNG bytes. They are the only places in the engine that touch the RNG:

**1. Initial wall shuffle (one operation per hand, at `initial_state`):**

```python
def shuffled_wall(seed: int) -> tuple[list[str], int]:
    """Return (wall, cursor_after) for an initial deal."""
    tiles = canonical_tile_set()       # the 144 tokens in a fixed canonical order
    cursor = 0
    # Fisher-Yates from end to start; for i from n-1 down to 1,
    # j = uniform_int(0, i); swap tiles[i] and tiles[j].
    for i in range(len(tiles) - 1, 0, -1):
        j, cursor = uniform_int(seed, cursor, upper_inclusive=i)
        tiles[i], tiles[j] = tiles[j], tiles[i]
    return tiles, cursor
```

`canonical_tile_set()` returns the 144 tokens in this exact order: `W1×4, W2×4, ..., W9×4, B1×4, ..., B9×4, T1×4, ..., T9×4, F1×4, F2×4, F3×4, F4×4, J1×4, J2×4, J3×4, H1, H2, H3, H4, H5, H6, H7, H8`. Locked here so two implementations of `shuffled_wall` can't disagree on starting order.

`uniform_int(seed, cursor, upper_inclusive)` consumes the minimum number of bytes needed to draw uniformly from `[0, upper_inclusive]` using rejection sampling:

```python
def uniform_int(seed: int, cursor: int, upper_inclusive: int) -> tuple[int, int]:
    n = upper_inclusive + 1
    if n <= 1:
        return 0, cursor
    bits = (n - 1).bit_length()
    bytes_needed = (bits + 7) // 8
    threshold = (1 << (bytes_needed * 8)) - ((1 << (bytes_needed * 8)) % n)
    while True:
        chunk = rng_bytes(seed, cursor, bytes_needed)
        cursor += bytes_needed
        value = int.from_bytes(chunk, "big")
        if value < threshold:
            return value % n, cursor
```

Rejection sampling guarantees a uniform distribution and consumes a *variable* number of bytes per draw. That's fine — the cursor is what makes resumption possible; the absolute byte count per draw is not load-bearing.

**2. Tie-breaks (rare, but specified).**

MCR rarely needs tie-breaking, but when it does (e.g., multiple winners on one discard, deciding seat order in a non-standard match format), the engine uses `uniform_int(seed, state.rng.cursor, len(candidates) - 1)` to pick. Every such call advances the cursor. Tie-break occurrences are recorded as `RNG_TIE_BREAK` events in the record (open: add to the event catalog or leave as a footnote in the resolution event — see open questions).

### What the engine does *not* do

- **No `random.random()`**. Banned in the engine module. Linter rule (S0 task): static check that `random` and `numpy.random` are not imported from `mahjong.engine.*`.
- **No clock-based seeding fallback**. If `initial_state` is called without a seed, it raises — does not silently seed from `time.time()`. A missing seed is a programmer error, not a polite default.
- **No multi-threaded RNG use.** The engine is single-threaded by construction (pure functions). If a caller (e.g., the self-play driver) parallelizes hand-playing across threads, each thread runs its own engine call with its own seed and cursor — no shared RNG state.

## The canonical hash

### Canonical serialization

To hash a `GameState`, first reduce it to a canonical byte string:

1. **JSON serialize** with `json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
    - `sort_keys=True` → key order is deterministic.
    - `separators=(",", ":")` → no whitespace.
    - `ensure_ascii=False` → no `\u` escaping; UTF-8 native (tile tokens are ASCII anyway, but lock the flag).
2. **Encode as UTF-8.**
3. **SHA-256 the bytes.**
4. **Hex-encode** the digest. Prefix with `"sha256:"`.

```python
def canonical_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
```

That's it. One function, applied wherever a hash is needed: `state_hash`, `config_hash`, the record `checksum`, and any future hash.

### Hash inputs are typed

The hash is over the JSON form, not the Python object, so:

- **Floats are forbidden.** JSON serialization of a float can drift across implementations (`1.0` vs `1` etc.). The state schema bans floats in canonical state; this contract is what enforces the consequence. Linter rule: static check that no field in the `GameState` type is `float`.
- **Integers fit in 64 bits.** JSON has no integer size limit, but other languages do. Seeds (128-bit) are the one exception, and they're serialized as decimal strings, not raw integers, to avoid any 64-bit-overflow risk in downstream consumers. (Pin in state-schema: `seed` is stored as `str` in JSON despite being conceptually an integer. Update needed there.)
- **Set-like fields are pre-sorted lists.** `concealed` is sorted by the engine before serialization (state schema already commits). Any other set-like field follows the same rule.
- **No object identity, no references.** Plain data only. (State schema already commits.)

### What gets hashed when

Three uses of `canonical_hash`:

| Hash | Input | Where stored | Validated by |
| --- | --- | --- | --- |
| `state_hash` | the full `GameState` | record events (`DEAL`, `HAND_END`, `FOOTER`; optional per-event in debug mode) | replay tests |
| `config_hash` | the resolved ruleset config dict | `record.HEADER.ruleset.config_hash`, `GameState.ruleset.config_hash` | record-load validation |
| `checksum` (record) | the JSONL bytes excluding the footer line, with LF separators | `record.FOOTER.checksum` | record-load validation |

Note: the record `checksum` is *not* a hash of the canonical state — it's a content hash of the file bytes, for corruption detection. The state hash chain catches a different failure (engine divergence); the file checksum catches storage corruption.

### Ruleset `config_hash`

The resolved ruleset config dict is the merged result of:
1. The named base ruleset (`mcr-2006`) baked into the engine.
2. Any home-rule overlays applied at table creation (S5).

The resolved dict is hashed once at table-creation time and stamped onto every state and record under that ruleset. If the engine's bundled `mcr-2006` definition ever changes (intentionally — say, a bug fix to a yaku weight), the `config_hash` changes, and replays of older records require the *old* config to be loaded from history (a versioned config archive lives in the engine package).

**Working answer for "where does old config come from":** the engine package bundles a `rulesets/` directory containing every released ruleset version keyed by `config_hash`. A record with an unknown `config_hash` is loadable but flagged ("ruleset config not found; replay is best-effort"). Pinning this needs an S5 design pass.

## The replay contract

Given a record file, the contract is:

```
For every event E in the record with a state_hash:
    Let S = initial_state(record.HEADER.ruleset, record.HEADER.seed)
    Apply every action from record up to and including E to produce S_E
    Assert canonical_hash(S_E) == E.state_hash
```

Plus, at the end:

```
Assert state.rng.cursor at TERMINAL == record.FOOTER.rng_cursor_final
Assert canonical_hash(state at TERMINAL) == record.FOOTER.state_hash_final
```

If any assertion fails, the engine has diverged from the recorded behavior. The divergence is either:
- **An intentional behavior change** in the engine. The fix is to update the fixture record with a commit message justifying *why* the behavior changed (per CLAUDE.md).
- **An unintentional regression.** The fix is in the code, not the fixture.

Distinguishing the two requires reading the diff, not running more tests. That's by design — the determinism check tells you *that* something changed; you tell it *whether* the change is intentional.

## Cross-platform stability

The full chain — RNG + canonical serialization + SHA-256 — is platform-independent by construction:

- `hashlib.sha256` is part of the Python standard library; same algorithm, same output bytes on every platform.
- `int.to_bytes` and `int.from_bytes` are deterministic in byte order and signedness; we always specify both explicitly.
- `json.dumps` with our flag set is deterministic across CPython versions back to 3.7. PyPy and other implementations should match; CI proves it.
- We use no floating-point math in the engine, so platform float quirks are irrelevant.

**CI matrix** (added in S0): Linux (Ubuntu LTS) and macOS, Python 3.12 and the latest. The determinism fixture set must produce identical hashes across all four cells. A divergence is a release blocker.

## Refactor protocol

When a code change causes a determinism fixture to fail:

1. **Don't update the fixture first.** The first step is reading the diff and understanding *why* the hash changed.
2. **If the change is a fix to a real bug:** update the fixture in the same commit, with a commit message explaining the behavior change. The new fixture becomes the new contract.
3. **If the change is a refactor that wasn't supposed to change behavior:** the refactor has a hidden bug. Find it. Don't update the fixture.
4. **If the change is genuinely cosmetic** (a comment, a docstring, a rename that doesn't touch logic): the fixture shouldn't have changed. If it did, something downstream depends on a property you thought was incidental. Investigate.

This is the discipline that makes the determinism contract useful. Skipping step 1 — auto-updating fixtures when they fail — converts the determinism gate into noise.

CLAUDE.md says this already; this spec is where the actual hash-comparison code lives, so it's worth restating.

## Alternatives considered

**RNG choice: SHA-256 counter vs. ChaCha20 vs. PCG vs. Python `random.Random`.**

- Considered: ChaCha20 for speed (it's a real stream cipher).
- Considered: PCG for being smaller and faster than SHA-256.
- Considered: just using `random.Random` with a seed (it's Mersenne Twister, deterministic across platforms in CPython).
- Chose SHA-256 counter because (a) SHA-256 is in every standard library on the planet — reimplementable in any language a future bot might be written in, (b) the cursor / seek semantics are trivially correct (`block_index = cursor // 32`), (c) performance is irrelevant at the scale of "one shuffle of 144 tiles per hand," (d) `random.Random`'s Mersenne Twister state is 2.5KB and not cleanly seekable, which makes resuming a hand from disk harder than it needs to be. The factor-of-100 speed difference vs. ChaCha20 is invisible at our throughput.

**Hash choice: SHA-256 vs. BLAKE3 vs. MD5.**

- Considered: BLAKE3 for speed.
- Considered: MD5 because we don't need cryptographic security.
- Chose SHA-256 because (a) same standard-library argument as the RNG, (b) the marginal speed difference doesn't matter, (c) MD5's "good enough for non-security" framing routinely turns out to not be (collisions found in records would be deeply unfunny), (d) SHA-256 is the obvious "I am hashing for integrity" choice and doesn't require justification in a year-from-now code review.

**Canonical serialization: JSON-sorted vs. CBOR vs. msgpack vs. a hand-rolled binary form.**

- Considered: CBOR or msgpack for guaranteed canonical binary form.
- Chose JSON-sorted because (a) the record format is already JSONL — using a different canonicalization for hashing would mean two serializers, which is two places for them to drift, (b) `json.dumps(sort_keys=True, separators=...)` is canonical-enough when we ban floats and pre-sort sets, (c) the human-debuggability of being able to `python -c "import json; print(json.dumps(state, indent=2))"` and *see* what's being hashed is worth a lot during RL debugging.

**Seeding: 64-bit vs. 128-bit.**

- Considered: 64-bit seeds for simpler JSON.
- Chose 128-bit because (a) 64-bit gives a ~50% collision probability after ~4 billion seeds, which sounds infinite until you're running self-play for years, (b) `secrets.token_bytes(16)` is the same call as `token_bytes(8)`, (c) the storage cost is 16 bytes per record. 128-bit is the no-regret choice.

**Storing the wall in records vs. seed-only.**

- Already covered in [record-format.md](record-format.md) under Alternatives. The determinism contract is what makes "seed-only" sound; this spec is the receipt.

**Including RNG state in the canonical state hash.**

- Considered: hash the state *without* `state.rng`, treat the RNG as a side channel.
- Chose to include it because (a) two states with identical observable fields but different RNG cursors are different — the next draw will differ, (b) excluding the RNG would let a refactor silently change cursor accounting without tripping the hash check, (c) the cost (one extra field in the hash) is nothing.

## Verification fixtures this spec implies

These are the determinism-specific fixtures; they exist alongside the per-spec fixtures the prior docs listed.

1. **RNG byte-stream golden vector.** A hardcoded `(seed, cursor, n) → bytes` triple checked in as a literal in the test file. Catches accidental changes to `rng_bytes` itself. Cross-platform.
2. **`canonical_tile_set` golden.** A checked-in fixture asserting the exact 144-token list in order. Catches accidental reordering of the canonical tile set, which would invalidate every record ever written.
3. **`uniform_int` rejection-sampling golden.** A checked-in `(seed, cursor, upper) → (value, cursor_after)` table covering small ranges (n=2, n=34, n=144) and edge cases (n=1, n=power-of-2).
4. **`shuffled_wall(seed=12345)` golden.** A checked-in tile list — the canonical "what should the wall be after shuffling with seed 12345?" answer. The single most load-bearing fixture in the project; a change to it invalidates every prior record.
5. **`canonical_hash` golden.** A checked-in `(input dict → expected hash string)` table covering: empty dict, primitive values, nested dicts, lists with strings, lists with integers, unicode-safe strings. Catches drift in `json.dumps` behavior.
6. **Cross-platform CI.** Run fixtures 1–5 on Linux + macOS, Python 3.12 + latest. Hashes must be byte-identical across all cells. CI fails the matrix on any mismatch.
7. **Record-replay determinism (per record).** For every record in the fixture suite, replay produces matching `state_hash` at every checkpoint. This is the per-record version of the contract; fixtures 1–5 are the per-primitive versions that catch divergence faster.
8. **No-float lint.** A static check (mypy or ast-based) that no field reachable from `GameState` is a `float`. Run pre-commit.
9. **No-random-import lint.** A static check that `random` and `numpy.random` (and any future RNG library) are not imported from `mahjong.engine.*`. Run pre-commit.
10. **Refactor-stability check (CI).** Every PR that touches `mahjong.engine.*` must either keep all determinism fixtures green or update them with a justification in the commit message. CI enforces "fixtures green OR commit message contains `determinism-update:`". Crude but effective.

## Open questions

- **`config_hash` for unknown rulesets at load time.** Replays of records whose `config_hash` doesn't match anything in the bundled `rulesets/` archive are flagged "best-effort." How best-effort? Working answer: load with the closest matching ruleset by `id` (e.g., `mcr-2006`) and mark the resulting trace as "non-authoritative." Pin in S5.
- **`RNG_TIE_BREAK` event.** Tie-breaks consume RNG bytes; the cursor advances. Should there be an explicit record event for them, or just an annotation on whatever resolution event triggered the tie-break? Working answer: annotation on the resolution event (`tie_break: {cursor_consumed: N, candidates: [...], chose: i}`). Avoids cluttering the event catalog with a rare event type. Update record-format event catalog when this lands.
- **Seed-as-string in JSON.** This spec just decided seeds should be serialized as decimal strings (to avoid 64-bit overflow risk in downstream consumers). [state-schema.md](state-schema.md) currently shows `seed` as an integer in the example. Needs a small edit there to match.
- **Bundled `rulesets/` archive layout.** Where in the engine package do versioned ruleset configs live? How are they keyed? Working answer: `engine/rulesets/{config_hash}.json`, one file per released config, with a `MANIFEST.json` mapping human-readable IDs (`mcr-2006`) to their current `config_hash`. Pin in S5.
- **JSON canonicalization edge cases.** Unicode normalization (NFC vs. NFD)? Surrogate pairs? Working answer: tile tokens are pure ASCII so we duck the question for v1. Add a normalization step (NFC) at the canonicalization boundary if user-display strings ever enter hashable state.
- **PyPy / non-CPython determinism.** `json.dumps` *should* match across implementations but isn't formally specified to. Working answer: declare CPython 3.12+ as the supported interpreter; cross-implementation determinism is best-effort and untested. Reconsider if anyone wants to deploy on PyPy.
