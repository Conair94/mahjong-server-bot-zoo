# Spec 9 — Self-play harness

The headless driver that plays hands between bots with no human, no UI, no network. Per the architectural premise in [server-plan.md](../server-plan.md), this is *the same engine* running with four bot adapters and no TUI adapter — not a separate code path.

The harness exists to produce training data and evaluation deltas. Its design priorities are throughput, determinism, and clean signal (no noise from the live-table machinery — transports, auth, persistence to SQLite, etc.).

**Tier:** 2. The harness affects the AI training pipeline directly, but the *records it produces* are governed by [record-format.md](record-format.md) (Tier 1). Anything the AI pipeline reads goes through that contract.

**Status:** draft, pre-S0.

## Goals

- **Same engine, no live-table machinery.** No WebSocket, no SQLite session table, no auth, no TUI. Just the engine + four `SeatAdapter`s + the record store.
- **Throughput-shaped.** Hands per second, not turns per second of human pace. Live tables are slow on purpose; this is fast.
- **Reproducible runs.** A run is fully described by `(master_seed, bot_manifest_set, hand_count, ruleset)`. Re-running with the same tuple produces byte-identical records.
- **Crash-resumable.** A run that dies after N hands can resume from N+1 without re-playing the prior hands. Important because long self-play runs are measured in days.
- **One-flag eval mode.** A degenerate "play N hands of A vs. B vs. C vs. D and print win/loss/score deltas" mode is the same code path as a training run — just with a different output writer attached.

## Non-goals

- **Not a training loop.** The harness produces records; a separate training script reads them. Decoupling means a slow training step doesn't stall data generation, and the same records feed multiple training experiments.
- **Not multi-machine.** Single-host only in v1. If we ever need distributed self-play, the seed-partitioning scheme described below extends cleanly (each host owns a disjoint seed range).
- **No live observability beyond stdout.** Hand counts, throughput, win rates printed to stdout. No web dashboard, no Prometheus. The records on disk are the source of truth; ad-hoc analysis runs over them after the fact.
- **No bot training inside the harness.** Inference happens via the same `BotRunnerAdapter` machinery as a live table. A bot that wants to learn from its own play does so out-of-band by reading the records.

## Entry point

A subcommand of the project CLI:

```bash
python -m mahjong selfplay \
    --master-seed 0xDEADBEEF12345678 \
    --hands 10000 \
    --bots b_rule_v1,b_random,b_rule_v1,b_random \
    --ruleset mcr-2006 \
    --output-dir records/selfplay/run-2026-05-20/ \
    --resume \
    --eval-summary
```

Flags:

- `--master-seed`: 128-bit integer (decimal or `0x`-prefixed hex). Required. Per-hand seeds are derived from it (see below).
- `--hands`: number of hands to play. Required.
- `--bots`: comma-separated list of four `bot_id`s registered with the server. The harness wires each `bot_id` to one seat (positional).
- `--ruleset`: ruleset reference. Default: `mcr-2006`.
- `--output-dir`: where records get written. Default: `records/selfplay/{utc_date}/{master_seed_short}/`.
- `--resume`: if the output directory has records, continue from `max(hand_index_in_match) + 1` instead of refusing to overwrite. Default: false (refuses to overwrite a non-empty output dir).
- `--eval-summary`: at the end, print win rates, average score deltas, deal-in rates per seat. Default: off.
- `--parallel-hands N`: run N hands concurrently in worker subprocesses (see Concurrency). Default: 1 (serial).
- `--bot-rotation {none, round-robin}`: whether to rotate which `bot_id` sits at which seat across hands. Default: `none`. Round-robin shuffles the four-bot tuple deterministically per hand to control for seat-luck.

## Seed management

The master seed determines every per-hand seed. The derivation:

```python
def hand_seed(master_seed: int, hand_index: int) -> int:
    """Per-hand seed = SHA-256(master_seed_bytes || hand_index_bytes) truncated to 128 bits."""
    payload = master_seed.to_bytes(16, "big") + hand_index.to_bytes(16, "big")
    return int.from_bytes(hashlib.sha256(payload).digest()[:16], "big")
```

Why derive rather than just use `master_seed + hand_index`:

1. **Spreads the seed space.** Sequential seeds plus the SHA-256 DRBG don't *necessarily* produce decorrelated walls, but derived seeds do by construction.
2. **Resumability.** Hand N's seed is computable from `(master_seed, N)` alone — no need to read prior records.
3. **Partition-friendly.** Two parallel runs on the same `master_seed` with disjoint `hand_index` ranges produce non-overlapping records that compose into a single dataset.

The `master_seed` is recorded in every hand's `record.HEADER` as `meta.master_seed` (a new optional field; pin in record-format addendum). Live-table records leave it unset.

## Concurrency

Default: **serial.** One hand at a time, in a single asyncio loop. The simplest model; debuggability is the priority for v1.

Optional: **`--parallel-hands N`** spawns N worker subprocesses. Each worker plays a disjoint slice of `hand_index`es (worker `k` of N plays hands where `hand_index % N == k`). Workers write to the same output directory; filenames don't collide because `hand_id` is UUIDv7. Parent process reaps workers and aggregates the eval summary.

**Why subprocess workers, not asyncio concurrency:** the engine is pure but `BotRunnerAdapter` spawns subprocesses, and orchestrating four-bot-subprocesses-per-hand across many concurrent hands inside one asyncio loop is more complex than running multiple harness processes. Subprocess workers are also CPU-isolated, which matters once neural-net bots are involved.

Each worker is itself serial. Parallelism scales by *process count*, not by per-process concurrency.

## Record output

Records are written by the same `RecordStore` the live table uses. Two differences:

1. **Output directory is a flag**, not the global `records/{year}/{month}/` path. Lets self-play runs stay grouped.
2. **No SQLite index writes.** The live table indexes every game into SQLite for "find games player X was in" queries; self-play records are queried in bulk by reading the directory tree, so the index is unnecessary overhead. A separate `python -m mahjong index <dir>` command exists for after-the-fact indexing if needed.

The records themselves are identical in format to live records — same JSONL schema, same Botzone-exportability, same projection rules. **This is the load-bearing claim of the AI plan**: the corpus the bots train on is the same shape as the corpus the bots will be evaluated against. No second format, no impedance.

## `SelfPlayDriverAdapter` (the god-mode case)

Per [seat-port.md](seat-port.md), the self-play driver is the *one* adapter allowed canonical-state access via `allow_god_view: true`. The harness wires it as follows:

- **Default mode:** the harness does *not* use a `SelfPlayDriverAdapter`. Each of the four seats gets an ordinary `BotRunnerAdapter` (one subprocess per bot per hand). Bots see only their `SeatView`s. This is exactly the live-table setup, minus the human seats.
- **God-view mode (`--driver-bot <id>`):** the harness instantiates a single `SelfPlayDriverAdapter` holding all four seats, backed by the named in-process bot. The driver receives canonical states. This is the path that supports research-ideas like oracle-guiding — the bot uses opponent-hand information at training time that it won't have at serving time.

God-view mode requires the bot to be an *in-process* bot (`runtime: "in_process"` in its manifest), not a subprocess. Subprocesses are sandboxed (no god view possible across the pipe without a wider protocol).

## Run lifecycle

```
1. Validate flags. Resolve bot_ids → manifests. Resolve ruleset.
2. If --resume: scan output dir, find max(hand_index_in_match), set start = that + 1.
                Otherwise: assert output dir is empty (or doesn't exist), set start = 0.
3. For each hand_index in [start, --hands):
    a. seed = hand_seed(master_seed, hand_index)
    b. If --bot-rotation round-robin: seat_assignment = rotate(bots, hand_index % 4)
       Otherwise: seat_assignment = bots
    c. Instantiate four adapters per seat_assignment.
    d. Run one hand via the table manager (same code as live tables).
    e. Record is written to output_dir/{date}/{hand_id}.jsonl.
    f. Update progress counters; periodically print (every --progress-every hands, default 100).
4. After loop: if --eval-summary, aggregate per-seat stats from this run's records and print.
5. Exit 0 on clean completion, 1 on any unhandled exception.
```

Crash recovery: every hand's record is fully written and fsync'd before the next hand starts. A crash mid-hand leaves a partial record that the resume scan detects (no `FOOTER` line) and deletes; the next run replays that hand.

## Eval-summary output

When `--eval-summary` is passed, the harness aggregates over the run's records and prints:

```
Self-play run: 10000 hands, master_seed=0xDEADBEEF12345678
Bots (seat 0..3): b_rule_v1, b_random, b_rule_v1, b_random
Ruleset: mcr-2006

                 seat 0     seat 1     seat 2     seat 3
Win rate          0.298      0.156      0.302      0.144
Avg score/hand    +4.21      -3.14      +4.07      -5.14
Deal-in rate      0.082      0.211      0.079      0.218
Avg fan when won  18.4       9.7        18.1       9.2

Bot-aggregated (regardless of seat):
                 b_rule_v1  b_random
Win rate          0.300      0.150
Avg score/hand    +4.14      -4.14
Deal-in rate      0.081      0.214
```

These are the metrics enumerated in the AI plan's "Evaluation" section. The harness computes them once from records, in one place — no per-experiment reimplementation.

## Worked example: a tiny eval run

Evaluating a fresh `b_rule_v1` against three `b_random`s for 100 hands, controlling for seat luck with rotation, with a known seed:

```bash
python -m mahjong selfplay \
    --master-seed 0x1234 \
    --hands 100 \
    --bots b_rule_v1,b_random,b_random,b_random \
    --bot-rotation round-robin \
    --output-dir records/eval/rule-v1-vs-random-2026-05-20/ \
    --eval-summary
```

This run is deterministic: rerun the same command, get byte-identical records and identical summary numbers. If the summary changes between runs of the same command, one of: the engine changed, `b_rule_v1` changed, or `b_random` changed — and the determinism contract pins which.

## Alternatives considered

**Asyncio concurrency vs. subprocess workers for `--parallel-hands`.**

- Considered: a single asyncio loop running N hands concurrently, with each hand's four `BotRunnerAdapter`s as tasks.
- Chose subprocess workers because (a) one runaway bot doesn't slow the whole loop, (b) CPU-bound neural-net bots benefit from process-level parallelism for free, (c) the worker model is dead simple to reason about ("each worker is a serial self-play"). Asyncio-within-process would be a useful optimization when bot inference is I/O-bound (e.g., remote inference server), but that's not v1.

**Records-only output vs. emitting training tensors directly.**

- Considered: the harness writes pre-featurized training tensors (the input shape the value head consumes) alongside records.
- Chose records-only because (a) feature extractors will change as the AI plan evolves (component 1's tracker output today vs. v2's classifier output later), and pre-baked tensors would need re-baking on every change, (b) decoupling means the data side and the training side iterate independently, (c) record-only output keeps the harness aligned with live tables, no special paths.

**Master seed in records vs. only at run-config level.**

- Considered: leave master_seed in the run-config file (`selfplay-run.json`) and just put per-hand `seed` in records.
- Chose to also put `master_seed` in record `meta` because records often outlive their run config, and "what run was this from?" is a question we'll want to answer from a record alone.

**Bot rotation off vs. on by default.**

- Considered: rotate by default to always control for seat luck.
- Chose off by default because the most common training case is "all four seats are the same bot self-playing," where rotation is a no-op and adds confusion. Eval runs explicitly turn it on.

**Crash recovery: skip incomplete hands vs. replay them.**

- Considered: leave partial records on disk for forensic value.
- Chose to delete partial records on resume because (a) the seed-derivation scheme means replaying produces a *better* fixture than the partial, (b) partial records would break the "every record is a complete hand" invariant in the format spec.

## Verification fixtures this spec implies

1. **Determinism of a small run.** A 10-hand run with a fixed master seed produces a checked-in fixture of (record file hashes + eval summary). Re-running produces byte-identical hashes and summary.
2. **Crash recovery.** A 20-hand run that's killed mid-hand-10 and resumed produces the same final record set as a clean 20-hand run from the same master seed.
3. **Parallel-hands equivalence.** A 100-hand serial run and a 100-hand `--parallel-hands 4` run with the same master seed produce the same set of records (set equality on `hand_id`; per-hand byte-identical given the same `(master_seed, hand_index)`).
4. **God-view privacy gate.** Without `--driver-bot`, no adapter receives a canonical state — only `SeatView`s. Assertion baked into the harness; a test inspects what the adapters got.
5. **Bot rotation determinism.** `--bot-rotation round-robin` with seed S produces the same seat assignment per `hand_index` across runs.
6. **Eval summary correctness.** Given a checked-in fixture record set with known per-seat outcomes, the summary numbers match a hand-computed reference.

## Open questions

- **Live progress reporting beyond stdout.** Tail-able log file? JSON-Lines progress events? Working answer: stdout is sufficient for v1; redirect to a file with `>` if needed. Reconsider when the first multi-day run happens.
- **In-process bot interface for `SelfPlayDriverAdapter`.** What does the API for an in-process bot look like? Working answer: a Python class implementing a small `Bot` protocol (`decide(view) -> action`), registered like manifest bots but with `runtime: "in_process"`. Pin when the first in-process bot is written (AI plan v1, the rule-based bot).
- **Per-run config file.** A YAML/JSON file with all flag values, for reproducibility. Working answer: defer — `--master-seed` + the exact CLI command in shell history is sufficient documentation for v1. Add a config file when a run takes more than ~5 flags to specify.
- **Streaming eval mode** (compute summary incrementally without holding all records in memory). The aggregation is straightforward to stream; defer until a run with hundreds-of-thousands-of-records makes it necessary.
- **Self-play between *different versions* of the same bot.** The flag interface assumes one `bot_id` per seat. A v2-vs-v2-old run would need versioned bot references (`b_imitation@0.2.0`). Working answer: address when v2 lands — `bot_id@version` syntax with no version meaning "latest."
