# Server design plan

This document is the design plan for the mahjong server itself — the part friends will log into and play on. The [AI plan](ai-plan.md) is a sibling doc covering the bot side. The [README](../README.md) is the public-facing overview.

## Purpose

A self-hosted mahjong server that:

1. Is playable by humans (friends-and-family scale, ~10s of accounts, ~handful of concurrent tables) before any AI work is done.
2. Treats AI bots as first-class players from day one, so a bot can take any seat at any table without special-casing.
3. Persists complete game records in Botzone's native format, so the same records train bots and replay games.
4. Runs on commodity hardware (laptop initially, dedicated home box later) with standard self-hosting tooling.

## Non-goals

- Public-internet scale (matchmaking lobbies for strangers, anti-cheat, ranked ladders across many tables).
- Mobile or web clients in v1. ASCII/TUI client only.
- High availability. A home server going down for an hour while it reboots is fine.
- Real-time anti-collusion or anti-cheat enforcement beyond basic server-authoritative play.

## Architectural premise: players are adapters, the engine is the core

The single load-bearing design decision: **the rules engine has no concept of "human" vs "bot" players.** It only knows about *seats*. A seat receives messages describing what it must decide; a seat sends back its chosen action. Whatever lives behind that seat — a human pushing buttons in a TUI, a bot subprocess speaking the Botzone protocol, a replay player feeding canned actions — is interchangeable.

This pattern is called **ports and adapters** (sometimes **hexagonal architecture**). The "port" is the seat interface (a small, stable contract: receive request, send response). The "adapters" are everything that plugs into it. The point is to keep the domain core — the rules engine — completely decoupled from I/O, transports, UIs, and concurrency. You can run the engine in unit tests with four canned-action adapters; you can run it in production with a TUI adapter, two bot adapters, and a replay adapter.

For this project that means:

- Adding bot support is not a "feature" added to a human-only server. The server is bot-shaped from the first commit; the TUI is just one adapter that happens to be slower.
- Adding Botzone competition submission later is also not new work — a Botzone-format adapter is a small wrapper because the engine already speaks the same shape.
- The headless self-play harness in the AI plan is the same engine running with four bot adapters and no TUI adapter. No code duplication.

## Components

```
+-------------------+
|   TUI client(s)   |   <-- humans connect here over SSH or WebSocket
+-------------------+
          |
          v
+-------------------+        +---------------------+
|  Session/auth     |        |  Bot runner         |
|  + connection mux |        |  (spawns subproc,   |
+-------------------+        |   speaks Botzone)   |
          |                  +---------------------+
          v                            |
+--------------------------------------+--+
|  Table manager  (one table = one game)   |   <-- routes per-seat requests
+-------------------+----------------------+
                    |
                    v
            +---------------+
            |  Rules engine | <-- pure functions, no I/O
            +---------------+
                    |
                    v
            +---------------+
            |  Record store | <-- append-only, Botzone replay format
            +---------------+
```

- **Rules engine.** Pure functions over a game-state value object: `legal_actions(state) -> list[Action]`, `apply_action(state, action) -> state'`, `score_hand(...) -> fan list`. No I/O, no randomness except via an explicitly-passed RNG seed. This is the layer that imports PyMahjongGB for fan calculation and shanten. Same code that the AI plan's components 1–5 build on top of.

- **Table manager.** Owns a single game's lifecycle: deal, sequence turns, time out non-responsive seats, write the record, declare game end. Holds the seat-adapter handles (humans + bots). Stateful, in-memory. Persists nothing directly — that's the record store's job.

- **Session/auth + connection mux.** Maps an authenticated user connection to a seat at a table. Handles disconnect/reconnect (a human disconnecting mid-hand should be able to come back; their seat is held by a placeholder adapter that auto-passes or auto-folds depending on policy).

- **Bot runner.** Launches bot subprocesses, marshals between the seat protocol and Botzone's request/response format (stateless — full history each turn). One subprocess per bot per game. Resource-limit them (CPU time, memory) so a misbehaving bot can't take the server down.

- **Record store.** Append-only writes of game records. One file per game, Botzone replay format. Indexed in SQLite for "find games player X was in" queries; the records themselves live on disk.

- **TUI client.** Connects to the server, renders state from server-pushed updates, sends actions. Bilingual (English/Chinese) display layer. Analysis overlays plug in here as renderers over the same game-state stream.

## Tech stack

Following the project's standards-first preference and aligning with the AI plan's Python choice:

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12+ | Matches AI ecosystem (PyMahjongGB, botzone-mahjong-environment, mjdata corpus). One language across the project. |
| Rules engine deps | PyMahjongGB | Official fan calculator + shanten. See AI plan. |
| Server framework | `asyncio` stdlib + a small WebSocket lib (`websockets`) | No need for Django/FastAPI — there's no HTTP API surface initially. WebSockets carry per-seat messages cleanly. |
| TUI | [Textual](https://textual.textualize.io/) | Mature Python TUI, full Unicode/CJK, runs over SSH via `textual serve` or as a local client connecting to the server. |
| Persistence | SQLite (via `sqlite3` stdlib) | Single-file, zero-config, ACID, fast enough for this scale. Game records as files on disk indexed by SQLite. |
| Auth | Password + session token, or SSH public key if connecting via SSH | Friends-and-family scale; no OAuth, no email verification. |
| Process supervision | systemd unit (Linux host) | Standard self-hosted convention. Restart on crash, log to journald, run as non-root user. |
| Reverse proxy / TLS | [Caddy](https://caddyserver.com/) | Automatic Let's Encrypt certs in one config file. Or skip entirely and use Tailscale (see below). |
| Remote access | [Tailscale](https://tailscale.com/) | Lets friends connect to your home server as if on a LAN. No public-internet exposure, no port forwarding, no DDoS surface. Strongly recommended for a home-hosted hobby service. |

**Conventions worth naming:**

- **SQLite is fine.** A common mistake in new server projects is reaching for Postgres "in case we scale." For a home server with tens of users, SQLite is faster, simpler, has fewer moving parts, and backs up by copying one file. The rule: use SQLite until you have a real reason not to.
- **12-factor app.** A set of conventions for portable server apps ([12factor.net](https://12factor.net/)). The relevant ones here: configuration via environment variables (not committed config files), logs to stdout (let systemd/journald handle storage), stateless processes (state lives in SQLite + game-record files, not in process memory beyond the active game), graceful shutdown on `SIGTERM`.
- **Append-only records.** Never edit a game record after it's written. New events get appended; corrections are new records. This keeps the record format simple, makes replays deterministic, and means backups can be rsync'd incrementally without locks.
- **Server-authoritative.** The server is the only thing that decides what's legal. Clients render state but never compute "did I win" — the server tells them. Trivially true here because we're following the engine-as-core pattern, but worth being explicit.
- **Tailscale over public exposure.** For a home server with a known set of users, putting it on a Tailscale tailnet is the lowest-friction, highest-security option. You skip TLS configuration, DDoS concerns, brute-force login attempts, and IPv4/IPv6 port-forwarding headaches.

## Networking

Open question, but the leading candidate:

- **WebSocket** over either Tailscale (private) or Caddy-fronted TLS (public-ish). Carries JSON messages: `{"type": "draw", "tile": "T6"}` and similar.
- The TUI client connects with `websockets`. Reconnect logic on the client; server holds the seat for N seconds (configurable; default ~60) before substituting an auto-pass adapter.

Alternative considered: SSH-only access where the server runs the TUI itself and the user just sees rendered frames. Simpler in some ways (no client to build), uglier in others (latency, no client-side animations, harder to do analysis overlays). Default to WebSocket; revisit if Textual's SSH-serving turns out to be a better fit.

## Persistence

Two stores:

1. **SQLite database** (`mahjong.db`) — accounts, sessions, stats, leaderboard, game-index (game_id, players, start time, winner, score deltas, path to record file). Use migrations from day one ([Alembic](https://alembic.sqlalchemy.org/) or a hand-rolled `schema_version` table). **Convention to know:** *schema migrations* are how production servers evolve their database schema without losing data. Tools like Alembic write paired up/down migration scripts; you run them in order on each deploy. Even at hobby scale, having migrations means you can iterate on the schema without doing destructive drops in production.

2. **Game record files** (`records/{year}/{month}/{game_id}.jsonl`) — one file per game, JSON-Lines format, each line is one event from the Botzone protocol log. Lines are appended as the game progresses; the file is closed when the game ends. Path is stored in the SQLite index for lookups.

**Backup strategy:** the entire data directory (`mahjong.db` + `records/`) is a single tree that can be rsync'd or snapshotted. For a home server, a nightly `borg` or `restic` backup to an external drive is standard. **Convention to know:** *3-2-1 backup rule* — 3 copies of data, on 2 different media, 1 offsite. For hobby projects this often collapses to "the live server + a rotating backup on a USB drive + occasional copy to cloud storage." The point is having a deliberate plan, not the specific tooling.

## Rules and configuration

- **Rule set per table.** The table manager loads a rule-set config when the table is created. Default is `mcr-2006` (the Botzone-canonical 81-fan ruleset); home-rules are named configs that override specific yaku weights, add/remove yaku, or change the 8-fan minimum.
- **Rule sets are versioned and stored with the game record.** A game played under `home-rules-v3` records that fact so analysis/training pipelines five months later still know what was scored under what rules. Without this, your training corpus silently mixes incompatible rule sets.
- **Configuration via environment variables** (12-factor). `MAHJONG_DATA_DIR`, `MAHJONG_LISTEN_ADDR`, `MAHJONG_DEFAULT_RULESET`, etc. No config file in v1.

## Auth and accounts

- Username + password (hashed with `argon2`; **never** plain bcrypt-only defaults from a tutorial). One session token per logged-in client, stored in a cookie or sent on every WebSocket open.
- No email verification, no password reset flow in v1 — you talk to the admin (you) to reset.
- Bots authenticate the same way humans do, with a separate account type flag. A bot account's "session" is just the bot runner spawning it.
- **Convention to know:** *Argon2* is the current standard for password hashing (won the Password Hashing Competition, 2015). Use a maintained library (`argon2-cffi`), never roll your own; never store passwords reversibly; never log them.

## Operations / hosting

This section is what makes the difference between "code runs on my laptop" and "I can leave my friends playing on it overnight." Following standard self-hosting conventions:

- **Run under systemd.** A unit file in `/etc/systemd/system/mahjong.service` runs the server as a non-root user, restarts on crash, logs to journald. `systemctl status mahjong`, `journalctl -u mahjong -f` for ops.
- **Graceful shutdown on SIGTERM.** The server catches `SIGTERM`, finishes the current turn for each active table, writes records, then exits. systemd waits up to its timeout before SIGKILL. This means upgrades don't corrupt in-progress games.
- **Health endpoint.** Even at hobby scale, a `/health` returning 200 if the server is responsive is enough for an external uptime check (UptimeRobot or similar, free tier) to email you when the server dies.
- **Log structure.** Logs go to stdout as JSON lines. systemd captures them. Cheap and standard.
- **Resource limits on bot subprocesses.** Use `resource.setrlimit` or run the bot under a Linux cgroup. A bot that allocates 8 GB or spins forever shouldn't take the host down. **Convention to know:** *sandboxing untrusted code* is what you're doing whenever you run a user-submitted bot. Even for trusted bots, limit memory + CPU time + wall-clock per turn so a bug doesn't manifest as a server outage.
- **Backups: nightly cron, offsite weekly.** Cheap. Do it.

**Hosting target progression:**

1. Laptop (development).
2. Always-on home machine (an old laptop, a Raspberry Pi 5, a mini PC). Tailscale for access. systemd for supervision. This is where v1 ships.
3. (Eventually, if needed) a small VPS for public access. Reverse proxy + TLS + rate limiting come into play here.

## Build phases

Phased to deliver something playable as quickly as possible, while keeping the engine bot-ready throughout. Aligned with the AI plan's component build order — the engine work shared between the two plans is the same work.

**Phase S0: walking skeleton.** Engine + table manager + two trivial seat adapters (canned-action "always pass" and "random legal"). Runs a complete game start-to-finish, writes a record file, scores it via PyMahjongGB. No network, no UI, no auth. **Single command:** `python -m mahjong play-test`. Validates the architectural premise.

**Phase S1: bot-runner adapter + Botzone protocol I/O.** A seat adapter that spawns a subprocess and speaks the Botzone request/response format to it. Run four copies of the official sample-bot-Botzone reference bot against each other in the engine; verify the record matches what the official judge would produce. This is the bot-compatibility milestone. From here on, any bot that runs on Botzone runs here.

**Phase S2: TUI client + WebSocket transport.** Textual-based ASCII client. Plain mode only (no overlays). Local-machine play: server and client on the same box, no auth, hard-coded users. Verifies the human seat adapter works in the same engine.

**Phase S3: accounts, sessions, persistence.** SQLite-backed accounts, password auth, session tokens, persistent stats. Multi-table support. This is the first version that can be left running and played by friends over Tailscale.

**Phase S4: analysis overlays.** Tiles-out, shanten indicator, score calculator, game-phase tracker. These are pure renderers over the game state — no engine change required. Toggleable per player ("weenie mode" vs. plain mode). Some of these (shanten, possible-outs) share implementation with AI components 4–5; build once, use in both places.

**Phase S5: home rules + rule-set versioning.** Configurable rule sets, recorded into every game record. Documentation of the project's specific home rules vs. MCR.

**Phase S6: opponent-aware overlays.** Opponent-hand forecaster, deal-in risk indicator. These wrap AI components 2 and 3 — gated on those being built. First point where the AI plan's progress unblocks server features.

**Phase S7: dedicated host, ops hardening.** Move off laptop. systemd unit, backups, health endpoint, log rotation, resource limits on bot subprocesses.

**Phase S8: permanent spectator table.** Always-on bot-vs-bot game with a spectator view. Requires (a) a runnable bot from the AI plan and (b) a spectator-mode adapter in the table manager. Spectator adapter is a seat that receives all-knowledge events but submits no actions.

S0–S3 deliver "playable with friends." S4–S5 deliver "playable *well* with friends." S6 onward delivers AI features and assumes the AI plan is progressing in parallel.

## Parallel work between server and AI plans

The two plans share these artifacts; build each once:

- The **rules engine** (legal actions, apply action, scoring). Server table manager and AI components 4–6 both consume this.
- The **PyMahjongGB integration layer**. Same wrapper functions called from both sides.
- The **game record format**. AI training pipeline reads what the server writes; same parser, same format.
- The **belief-state and shanten components** (AI components 1–5). These are server-side analysis overlays *and* bot perception modules.

The two plans differ on:

- AI plan owns: bot architectures (v1–v4), training pipelines, evaluation harness, MCR corpus ingest, learned models.
- Server plan owns: networking, auth, persistence, TUI, ops, accounts, multi-table session management.

When in doubt about which doc owns a piece of work, ask: *does this exist on disk after the game ends, or does it only matter at decision time inside a bot?* Persistent → server plan. Decision-time → AI plan.

## Open questions

- **Transport: WebSocket vs. SSH-served TUI.** Defaulting to WebSocket. Revisit if Textual's SSH serve mode turns out cleaner.
- **Authentication: passwords vs. SSH keys vs. magic links.** Defaulting to passwords for v1; SSH keys are tempting if we go the SSH-served route.
- **Bot resource limits.** Memory and CPU caps per turn — what numbers? Need to measure once a real bot exists.
- **Replay/spectator format.** Live spectator view: re-derive from the in-progress record file, or push a separate event stream? Probably the former (one source of truth), but it depends on how lossy the record format is for mid-game state.
- **Chat.** In-game chat is in the README. Trivial to add; defer until S3 ships. Open question: persist chat? (Probably yes, in the game record file as a non-action event.)
- **Time controls.** Per-turn time limits for human players. Default to generous (60 seconds?), expose as a per-table setting. Bots already have their own time budget from Botzone conventions.

## Things to deliberately not do in v1

Applying YAGNI:

- No replicated database. SQLite is enough.
- No microservices. One process.
- No Docker (yet). systemd is enough on a single host; Docker adds complexity for a hobby server. Revisit if hosting moves to a VPS that benefits from container images.
- No metrics stack (Prometheus, Grafana). Logs + uptime check are enough. Add metrics when you have a specific question they'd answer.
- No web UI. TUI only.
- No mobile.
- No spectator chat / spectator betting / spectator gimmicks beyond just watching.
- No matchmaking. Tables are created manually by a host.
