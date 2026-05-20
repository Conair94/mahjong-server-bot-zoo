# mahjong-server-bot-zoo

A self-hosted mahjong server with an ASCII client, modular rule sets, and first-class support for AI opponents. Designed around game-record capture and bot interoperability so the same engine that runs human games can also host bot-vs-bot matches and produce training data.

A long-running bot-vs-bot table is intended to be available for spectating at all times while the server is up.

How we work on this project (TDD-first for core, verification artifacts gate every phase): [CLAUDE.md](CLAUDE.md). Detailed plans: [server](docs/server-plan.md), [AI](docs/ai-plan.md). Speculative ideas not yet committed: [research](docs/research-ideas.md).

## Goals

1. Run a playable mahjong server on a home machine for friends.
2. Support MCR rules and configurable home-rule variants from a single engine.
3. Capture complete, replayable game records in an existing standard format so they are usable by external tools without conversion.
4. Speak the Botzone protocol natively so bots developed here can enter the 6th annual AI mahjong competition without a translation layer.
5. Provide in-client analysis tools that help human players study their own play.
6. Prefer existing standards and widely-used libraries over custom equivalents wherever a reasonable option exists. Every conversion layer is a maintenance cost and a source of bugs.

## For players

ASCII client, bilingual English/Chinese. Features:

- Account, persistent stats, leaderboard, in-game chat.
- Configurable analysis overlays (toggleable per player):
  - Tiles-out tracker.
  - Possible-outs list with fan value for each candidate hand.
  - Score calculator.
  - Shanten and game-phase indicators (tiles remaining in wall, tiles each opponent has drawn and kept).
  - Opponent-hand forecast.
- "Plain mode" disables all overlays for unaided play.
- Note-taking during a hand.
- Selective hand reveal ("taunt") as an explicit action.
- Animations and sound on tile placement.
- Achievements (see appendix).

## For researchers and bot authors

Detailed AI component plan: [docs/ai-plan.md](docs/ai-plan.md). Speculative techniques parked outside the committed roadmap: [docs/research-ideas.md](docs/research-ideas.md).

- **Botzone-compatible bot interface.** Bots run as subprocesses communicating via the Botzone JSON request/response format over stdin/stdout. Any bot that runs on Botzone runs here unchanged.
- **Full game records.** Every game persists the wall order, every draw, every discard, every call, and every decision point with the acting player's legal options. Format is documented and exportable.
- **MCR database ingest.** Converter for the public MCR game database into the project's record format for supervised training.
- **Spectator mode for bot-vs-bot games**, with optional explainer overlays (see [arxiv 2506.14246](https://arxiv.org/html/2506.14246v1)).
- **Pluggable rule sets** so a bot trained on MCR can be evaluated against home-rule variants without re-implementing the engine.

## Architecture

Detailed server design: [docs/server-plan.md](docs/server-plan.md).

- **Rules engine** — pure functions over game state. Rule set (MCR, home rules, future variants) selected per table.
- **Game server** — owns table state, sequences turns, persists records.
- **Bot runner** — launches bot subprocesses, brokers Botzone-format messages between bot and server.
- **ASCII client** — connects to the server; renders state and analysis overlays.
- **Analysis overlays** — composable modules that read public game state plus the requesting player's private hand and emit display data. Shanten, possible-outs, opponent-hand forecast, and the explainer are all overlays under the same interface.

## Roadmap

Each phase ships with a checked-in verification artifact — a fixture, a determinism check, or a judge-acceptance recording — not just code that compiles. Detailed exit criteria are in [docs/server-plan.md](docs/server-plan.md) (per-phase Verification) and [docs/ai-plan.md](docs/ai-plan.md) (per-component Verification).

1. **Engine + Botzone I/O.** Rules engine for MCR, game-record format, bot runner speaking Botzone protocol. Exits when four reference bots play a recorded game accepted by the official Botzone judge.
2. **ASCII client and local server.** Single-machine play, accounts, persistence, in-game chat. Plain mode only. Exits when a scripted TUI session reproduces a recorded server-side fixture.
3. **Analysis overlays.** Shanten, tiles-out, possible-outs with fan, score calculator, game-phase indicator. Opponent-hand forecast. Overlays share implementation with AI components 1–5; the component fixtures gate this phase.
4. **Home-rule support.** Configurable rule sets, recorded into every game record. Exits when the same fixture replays correctly under each declared rule-set version.
5. **Permanent spectator table.** Long-running bot-vs-bot game with public spectator view.
6. **Dedicated host.** Move off laptop to always-on hardware. Exits when a backup-restore drill succeeds end-to-end.
7. **AI training pipeline and API.** MCR database ingest (preprocessor exists upstream — no custom pipeline), training scripts, evaluation harness against held-out opponents, public bot-submission API.
8. **Botzone entry.** Submit a trained bot to the 6th annual competition.

## Open decisions

Default bias on every decision below: pick the option that already exists in the ecosystem we are interoperating with, even if a custom alternative would be marginally nicer.

- **Game-record format.** Use Botzone's native MCR replay/log format as the canonical on-disk record. It is the format the competition produces, the format opponent bots already emit, and the format any future Botzone replay tool will consume. No conversion at the boundary that matters most. Tenhou mjlog is Riichi-specific and not applicable.
- **Bot protocol.** Botzone JSON over stdin/stdout. Non-negotiable; this is what the competition requires.
- **Server language.** Pick the language with the most mature existing mahjong tooling (shanten libraries, fan calculators, MCR rule implementations) rather than the one that is most pleasant to write from scratch. Python and C++ both have viable MCR libraries published alongside Botzone.
- **TUI stack.** Whichever mature, Unicode-capable TUI library matches the server language choice. Textual if Python, Bubble Tea if Go, Ratatui if Rust. Decision follows server language, not the other way around.
- **Fan set.** MCR 81-fan, matching Botzone. Zung Jung and Riichi are out of scope for v1.

## Prior work to leverage

- [arxiv 2108.06832](https://arxiv.org/abs/2108.06832) — fast shanten calculator. Will need extension for home-rule yaku.
- [arxiv 2506.14246](https://arxiv.org/html/2506.14246v1) — mahjong explainer for interpretable bot decisions.
- Public MCR game database — supervised training corpus.
- [Botzone 2026 mahjong contest](https://botzone.org.cn/static/gamecontest2026a.html) — competition target and source of opponent bots for evaluation.

## Appendix: achievement ideas

- Limit hand.
- Lose mahjong to a downstream player on a tile you would have won on.
- Rob a kong.
- Win within the first 10 tiles.
- Win within the last 10 tiles.
- Discard thirteen orphans.
- All Green.
