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

S2 / S3 specs (drafted 2026-05-22, pre-implementation; tier per `s2-s3-plan.md`):

| # | Spec | Tier | Pins | Consumed by |
| --- | --- | --- | --- | --- |
| 10 | [wire-protocol.md](wire-protocol.md) | 1 | The WebSocket message contract between server and every client: framing, message catalog (player + spectator), error model, version handshake, reconnect token format. Privacy enforced at the wire via `SeatView`-projected payloads. | TUI client, session-mux, `HumanAdapter`, any future non-TUI client. |
| 11 | [session-mux.md](session-mux.md) | 1 | Per-table state machine that binds a WebSocket connection to a seat: attach, detach, reconnect within seat-hold window, replace, and the parallel spectator-set lifecycle. | Server lifecycle, `HumanAdapter`, table manager (consumes substitution events). |
| 12 | [tui-client.md](tui-client.md) | 2 | Textual app architecture: screen layout, input bindings, rendering pipeline over `SeatView` / public projection, spectator screen, plain-mode-only constraint. | TUI client only. |
| 13 | [sqlite-schema.md](sqlite-schema.md) | 2 | DB tables (`accounts`, `sessions`, `hand_index`, `hand_participants`, `schema_version`), indexes, constraints, hand-rolled migration mechanics. | auth, persistence-api, server-lifecycle, tests. |
| 14 | [auth.md](auth.md) | 2 | Argon2id parameters, password hashing round-trip, session token issuance/validation/expiry/revocation, bot-account auth path, no-account-existence-leak failure mode. | session-mux (login on attach), wire-protocol (auth messages), persistence-api. |
| 15 | [persistence-api.md](persistence-api.md) | 2 | Python query/write layer over SQLite + record files. Transactional `reserve_hand` / `finalize_hand`; startup integrity check; records-as-source-of-truth rebuild path. | Table manager (hand-end hook), server-lifecycle, future overlay/stats code. |
| 16 | [server-lifecycle.md](server-lifecycle.md) | 2 | Process startup, env-var configuration (`MAHJONG_*`), `/health` endpoint, multi-table orchestration (`TableRegistry`), graceful `SIGTERM` drain, crash recovery, periodic tasks, structured logging. | `python -m mahjong serve` entry point, ops tests. |

Layer 8 follow-ups (drafted 2026-05-26, post-8.7.e lobby; pickup order per [implementation-order.md § Layer 8 follow-ups](implementation-order.md)):

| # | Spec | Tier | Pins | Consumed by |
| --- | --- | --- | --- | --- |
| 17 | [multi-human-seats.md](multi-human-seats.md) | 2 | Open-lobby `CREATE_TABLE.seats[]={kind}`; explicit `START_HAND` wire message; per-seat `TABLE_LIST` projection; 22 fixtures. **Implemented in 8.7.a-f.** | wire-protocol, session-mux, server-lifecycle, web client. |
| 18 | [cardinal-ui.md](cardinal-ui.md) | 3 | 3 × 3 cardinal-direction renderer (south = you, east/north/west = next/across/previous in CCW play order); turn arrow in center cell pointing at `current_actor`; last-discard tile glyph + caption in the center cell. No wire change. | Web client renderer (`mahjong/web/static/render.js`) only. |
| 19 | [human-decide-timeout.md](human-decide-timeout.md) | 2 | Per-(seat_kind, prompt_kind) decide deadlines: human DISCARD 60s, human CLAIM 20s, bot 30s. New `SeatAdapter.kind` field; three `MAHJONG_DECIDE_TIMEOUT_*` env vars. | seat-port, table manager, server config, web client (renders the longer deadline in PROMPT). |
| 20 | [late-join-replay.md](late-join-replay.md) | 2 | Refuse `ATTACH` to a previously-UNBOUND human seat once the table's hand is `IN_PROGRESS` with new `hand_in_progress` error code; lobby suppresses Join button on in-progress tables (Spectate stays). Replay-from-record path explicitly deferred to a future spec. | wire-protocol (new error code), session-mux (one-line gate), web client lobby. |
| 21 | [layer8-closeout.md](layer8-closeout.md) | 2/3 | Layer 8 close-out bundle: §1 hand-display polish (per-tile spans, selection highlight, just-drawn offset, suit-break gap); §2 bot pacing (5–10s `PacedAdapter` wrapper); §3 pinwheel `?` flicker investigation note; §4 late-join refusal close-out (Alt A pinned); §5 server lifecycle hardening (8.8 subset: SIGTERM drain, /health, integrity check, structured logging); §6 persistence + auth WS wiring (`reserve_hand`/`finalize_hand`, `AUTH_REQUEST`/`RESUME` on the WS handler, real `admin_predicate`). §1/§2/§4 landed 2026-05-27; §3 superseded by Spec 22 § 22.1. | web client renderer, bot adapters, server registry, ws handler, persistence + auth. |
| 22 | [layer8-closeout-r2.md](layer8-closeout-r2.md) | 1/2/3 | Round-2 follow-ups from live two-tab verify: § 22.1 claim-window arrow info leak (one-line renderer fix, supersedes Spec 21 §3); § 22.2 claim-action alerts (visual + future sound); § 22.3 selection highlight under Unicode (CSS); § 22.4 discard tile font size; § 22.5 **BUGANG-from-hand stalls hand loop** (Tier 1 engine bug — first priority); § 22.6 table-creation options + claim-resolution window (per-table bot speed + decide-timeout, 1.5 s minimum claim window for fair multi-human play); § 22.7 re-sort concealed; § 22.9 hand-end scoring summary (fan breakdown + point swing, renderer-only). | engine (gang DRAW), table manager, web client renderer, lobby UI. **Landed 2026-06-01: §22.1/22.2/22.3/22.4/22.5/22.7 + §22.6 Part A (table-creation options). §22.6 Part B found unnecessary (claim resolution already fair). Open: §22.9.** |

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
