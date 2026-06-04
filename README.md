# mahjong-server-bot-zoo

A self-hosted [MCR](https://en.wikipedia.org/wiki/Mahjong_competition_rules) (Mahjong Competition Rules) server with a browser-served ASCII client, built around game-record capture and bot interoperability — so the same engine that runs human games can host bot-vs-bot matches and produce training data. The long-term goal is a *zoo* of bots trained against the live server, with an entry in the Botzone AI mahjong competition.

It is also a personal learning project for AI/ML and server-ops conventions, so the codebase and docs name techniques inline as they come up.

- **How we build:** [CLAUDE.md](CLAUDE.md) — the working agreement (TDD-first for core; every phase gates on a verification artifact, not just code that compiles).
- **What we're building:** [docs/server-plan.md](docs/server-plan.md) (server), [docs/ai-plan.md](docs/ai-plan.md) (AI), [docs/specs/](docs/specs/) (load-bearing interface contracts).
- **Speculative ideas, not yet committed:** [docs/research-ideas.md](docs/research-ideas.md).

## Status

**Layers 0–8 are implemented** (Python, ~950 fast tests). The server is playable end-to-end in a browser, persists accounts and game records, runs a multi-table lobby, and can be exposed to the public internet behind a Cloudflare Tunnel with an admin control console supervising it.

**Layer 9 — the bot zoo and RL training pipeline — is next and not yet started.** The bot-runner plumbing exists, but the only "bot" today is a canned placeholder that always passes/discards (a tsumogiri stand-in), *not* a real policy. No models have been trained yet.

### What works today

- **Rules engine** — MCR legality, terminal detection, wall/draw mechanics, and 81-fan scoring (via the [PyMahjongGB](https://pypi.org/project/PyMahjongGB/) library at a single integration seam). Pure functions over an explicit game-state value object.
- **Deterministic records** — every hand is written as a replayable JSONL event log; seeded rollouts are byte-reproducible (a determinism gate guards against silent RNG/env drift).
- **Multi-table WebSocket server** — `mahjong-v1` subprotocol, a lobby with `CREATE_TABLE` / `LIST_TABLES` / `ATTACH`, multi-human seats plus canned-bot seats, explicit `START_HAND`, per-seat private views, and reconnect/resume.
- **Accounts & persistence** — argon2id password hashing, session tokens with resume, and a SQLite store of per-account hand history and score deltas.
- **Browser ASCII client** — a no-build [Lit](https://lit.dev/) web client served at `/`, rendering the table as ASCII with toggleable panes. Login → lobby → join/spectate works.
- **Server lifecycle** — 12-factor env config, a two-phase graceful drain (finish the current hand, then escalate), a `/health` liveness endpoint, periodic WAL-checkpoint and session-cleanup tasks, and structured JSON logging for journald/log shippers.
- **Self-play harness** — `mahjong selfplay` runs headless N-hand matches between seat policies with an eval summary (the substrate the future RL eval harness plugs into).
- **Admin control console** — `mahjong control` supervises a `serve` child process; a token-gated dashboard with status, invite codes, accounts, live log tail, health/storage, Cloudflare-tunnel control, and a feedback inbox.
- **Public hosting** — invite-code registration and a Cloudflare Tunnel path for exposing a home-hosted server without opening ports.

### Planned (not yet built)

- **The bot zoo (Layer 9):** real random-legal-move and learned policies wired as genuine seat identities, replacing the canned placeholder; RL training loop and an eval harness measuring win-rate against held-out opponents.
- **Botzone competition entry:** native Botzone-JSON bot I/O exists as an *export seam*, but judge-accepted submission (the S1 gate) is deferred pending the C++ toolchain.
- **Analysis overlays:** shanten, tiles-out tracker, possible-outs with fan value, score calculator, game-phase indicators, opponent-hand forecast — these share implementation with the AI components and are a later phase.
- **MCR database ingest** for supervised pretraining.
- **Player-experience polish:** bilingual EN/中文, in-game chat, note-taking, selective hand reveal ("taunt"), animations/sound, achievements.

## Quick start

Requires Python 3.12+. Install the package (and its dependencies, including `PyMahjongGB`) into a virtualenv with `pip install -e .`.

```bash
# 1. Create an admin account (interactive password prompt, or --password-stdin).
MAHJONG_DATA_DIR=./var/mahjong python -m mahjong account create \
    --username alice --display "Alice" --admin

# 2. Run the server (loopback by default).
MAHJONG_DATA_DIR=./var/mahjong python -m mahjong serve
#    → web client at http://127.0.0.1:8400/  (log in as the account above)

# To host on a LAN or Tailscale instead of loopback:
MAHJONG_DATA_DIR=./var/mahjong MAHJONG_LISTEN_ADDR=0.0.0.0:8400 python -m mahjong serve
```

Open `http://<addr>:8400/` and log in. For a multi-human table, append `?humans=N` (1–4): the first browser at `?humans=2` creates a 2-human + 2-bot table; later browsers join its open human seats. Authentication is always required when the server runs with persistence (the default).

### CLI subcommands

```text
python -m mahjong <subcommand>
  serve        run the WebSocket mahjong server
  control      run the admin control console (supervises serve)
  account      account management (create | list)
  selfplay     headless N-hand self-play between bots
  play-test    drive one hand with four canned seats
```

The control console (`mahjong control`, or `./scripts/mahjong-console --autostart-server`) is the recommended way to operate a long-running deploy: it owns the `serve` child, injects a fresh admin token, and exposes the dashboard.

## Architecture

The server is one asyncio event loop; everything is a coroutine on it. Detailed design in [docs/server-plan.md](docs/server-plan.md); interface contracts in [docs/specs/](docs/specs/).

| Package | Responsibility |
| --- | --- |
| `mahjong/engine` | Pure rules engine over an explicit game-state value object. Ruleset selected per table. |
| `mahjong/table` | The per-table hand-orchestration loop (sequences turns, resolves claims). |
| `mahjong/adapters` | The seat interface every player implements — human, canned bot, paced bot, auto-pass. |
| `mahjong/sessions` | Session-mux: maps WebSocket connections to seats, holds/resumes, fans events to spectators. |
| `mahjong/server` | Multi-table orchestrator, registry, lifecycle (config, drain, health, periodic tasks). |
| `mahjong/wire` | The `mahjong-v1` JSON wire protocol codec. |
| `mahjong/web` | The bundled browser ASCII client (Lit, served as static assets). |
| `mahjong/persistence` | SQLite accounts, sessions, and the hand index; record-file store. |
| `mahjong/records` | The on-disk JSONL record format + replay. |
| `mahjong/bots` | Bot-runner plumbing: registry, manifest, subprocess sandbox, Botzone serializer, SDK. |
| `mahjong/selfplay` | Headless self-play runner, seed management, eval summary. |
| `mahjong/control` | Admin control console (control-plane/data-plane split, supervisor, panes). |
| `mahjong/cli` | Subcommand dispatch (`serve`, `control`, `account`, `selfplay`, `play-test`). |

## Roadmap

Each phase ships with a checked-in verification artifact — a fixture, a determinism check, or an eval number — not just code that compiles. Exit criteria live in [docs/server-plan.md](docs/server-plan.md) and [docs/ai-plan.md](docs/ai-plan.md); the granular layer-by-layer order is in [docs/specs/implementation-order.md](docs/specs/implementation-order.md).

1. **Engine + records + bot-runner plumbing.** ✅ MCR rules, JSONL record format, Botzone-format export seam.
2. **Local server + client.** ✅ Multi-table WebSocket server, accounts, persistence, browser ASCII client.
3. **Server lifecycle & hosting.** ✅ Graceful drain, health, JSON logging; invite registration, Cloudflare Tunnel, admin console.
4. **Self-play harness.** ✅ Headless N-hand matches + eval summary (the RL substrate).
5. **The bot zoo (Layer 9).** ⏳ *Next.* Real policies as seat identities; RL training loop; eval against held-out opponents.
6. **Analysis overlays.** Shanten, tiles-out, possible-outs with fan, score calculator, opponent forecast.
7. **AI training pipeline.** MCR database ingest, training scripts, public bot-submission API.
8. **Botzone entry.** Submit a trained bot to the competition (gated on judge acceptance).

## Design decisions

Default bias: prefer the option that already exists in the ecosystem we interoperate with over a custom equivalent. Every conversion layer is a maintenance cost.

### Resolved

- **Server language — Python.** Chosen for the mature MCR tooling (`PyMahjongGB` fan/shanten). The Botzone judge's C++ toolchain is the one remaining native dependency (deferred).
- **Client — browser-served ASCII, not a TUI.** The original plan was a Textual TUI; it pivoted to a no-build Lit web client served by the same server, so there's nothing extra to install to play. (Resolves the old "TUI stack" question.)
- **On-disk record format — the project's own JSONL.** A documented, fully replayable event log is the canonical record; Botzone's format is produced at an **export boundary** for competition I/O, not used as the on-disk store. This is the one deliberate conversion boundary, flagged as such.
- **Bot protocol — Botzone JSON over stdin/stdout.** Non-negotiable; it's what the competition requires.
- **Fan set — MCR 81-fan**, matching Botzone. Zung Jung and Riichi are out of scope for v1.

### Open

- **Home-rule variants.** The engine carries a per-table ruleset reference, but only `mcr-2006` is implemented today. Configurable home rules are a later phase.
- **Hosting hardware.** Develop on macOS; deploy target is Linux (Raspberry Pi 5 / mini-PC) on a Tailscale tailnet. The ops-hardening pass is Linux-specific.

## Prior work to leverage

- [arXiv 2108.06832](https://arxiv.org/abs/2108.06832) — fast shanten calculator (will need extension for home-rule yaku).
- [arXiv 2506.14246](https://arxiv.org/html/2506.14246v1) — a mahjong explainer for interpretable bot decisions (the basis for a future spectator overlay).
- The public MCR game database — a supervised-training corpus.
- The [Botzone mahjong contest](https://botzone.org.cn/static/gamecontest2026a.html) — competition target and a source of opponent bots for evaluation.

## Appendix: achievement ideas

Not implemented — a backlog of flavour for later:

- Limit hand · rob a kong · win within the first / last 10 tiles · discard thirteen orphans · all green · lose to a downstream player on a tile you'd have won on.
