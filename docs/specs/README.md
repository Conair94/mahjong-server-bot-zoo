# Server design specs

Tier-1 design artifacts for the server. The [server plan](../server-plan.md) covers *what* and *why* at a high level; the docs here pin the *interfaces* — the data shapes and protocols that will be load-bearing across the whole system (rules engine, table manager, bot runner, TUI, record store, AI training pipeline).

These specs are written **before S0 implementation** for one reason: the AI plan's biggest failure mode is *training/serving skew* (training scorer disagrees with judge scorer; training feature extractor disagrees with serving feature extractor). The cheapest way to prevent that is to lock the interface shapes once, in one place, before any of the consumers exist.

Each spec is the source of truth for its contract. When the spec and the code disagree, the spec wins until updated — changes to a Tier-1 contract are deliberate edits to the spec, accompanied by the test/fixture changes that prove the new contract.

## The specs

Build order reflects dependencies — each spec assumes the ones above it.

| # | Spec | Pins | Consumed by |
| --- | --- | --- | --- |
| 1 | [state-schema.md](state-schema.md) | The single game-state value object the engine operates on. Tile encoding, seat layout, wall representation, pending-claim resolution, rule-set reference. | Engine, table manager, every adapter, every bot, every overlay, training feature extractors. |
| 2 | [record-format.md](record-format.md) | The on-disk JSONL event format. One event per line; full game replayable from records alone. Public-vs-concealed field split for per-seat replay. | Record store, replay system, AI training corpus loader, eval harness. |
| 3 | [seat-port.md](seat-port.md) | The async interface every seat adapter implements (human-TUI, bot-runner, canned-action, spectator, self-play-driver). Request/response shape, timeout semantics, error model. | Table manager (consumes), all adapters (implement). |
| 4 | [bot-runner-protocol.md](bot-runner-protocol.md) | How the bot-runner adapter speaks to a bot subprocess in Botzone format. Framing, handshake, time-budget enforcement, sandboxing limits, illegal-action surfacing. | Bot runner, every bot (must conform). |
| 5 | [determinism.md](determinism.md) | The seed-and-hash contract that makes "same seed + same inputs → byte-identical trace" hold across all components. Where seeds enter, how they serialize, what's included in the canonical hash. | Engine RNG, record store (serializes seed), test fixtures, AI determinism gates. |

Tier 2 specs (internal structure; one consumer each, lower blast radius if wrong):

| # | Spec | Pins | Consumed by |
| --- | --- | --- | --- |
| 6 | [engine-api.md](engine-api.md) | Full public surface of the rules engine: function signatures, exception taxonomy, the single PyMahjongGB integration seam, pure-function discipline, internal submodule layout. | Table manager (calls), tests (call & stub). |
| 9 | [selfplay-harness.md](selfplay-harness.md) | The headless self-play driver: CLI, seed-derivation scheme, concurrency model, record output, crash recovery, eval-summary metrics, god-view gate. | AI training pipeline (consumes records), evaluation harness. |

## Implementation order

The bottom-up build sequence that respects the dependencies above lives in [implementation-order.md](implementation-order.md). It groups work into layers, names the fixtures that gate each step, and reaches the S0 walking skeleton, S1 Botzone-bot integration, and the self-play harness in a single linear sequence.

## Conventions

- **No future-proofing.** Specs describe what's needed for v1. Extension points are added when a second consumer needs them, not in anticipation.
- **Examples are normative.** Every spec includes worked examples. If the prose and an example disagree, the example wins (and the prose gets fixed).
- **Alternatives considered, inline.** Each spec has an "Alternatives considered" section so the *why* survives. No separate ADR directory.
- **Verification fixtures are listed, not deferred.** Each spec ends with the test fixtures it implies. Those fixtures are what S0 must produce to claim conformance.

## Authoritative external reference

The [Botzone Chinese Standard Mahjong wiki](https://wiki.botzone.org.cn/index.php?title=Chinese-Standard-Mahjong/en) is the source of truth for: tile token format, request/response protocol, action grammar and priorities, time budget per language, the 81-fan MCR scoring table, and judge behavior on edge cases. When any spec in this directory disagrees with the wiki, the wiki wins and the spec gets updated.

## What lives elsewhere

- High-level architecture and phasing: [../server-plan.md](../server-plan.md).
- AI components and bot architectures: [../ai-plan.md](../ai-plan.md).
- Speculative ideas (not committed): [../research-ideas.md](../research-ideas.md).
- Behavioral working agreement (TDD, verification ladder): [../../CLAUDE.md](../../CLAUDE.md).
- Tier-2 / Tier-3 specs (wire protocol for TUI, SQLite schema, auth, ops) — drafted in their phase, not pre-built.
