# Spec 25 — Admin control console

**Status:** Draft 2026-06-03. Not started.
**Tier:** 2 (operator-facing surface; blast radius is the ops layer, not the game protocol).
**Branch:** `spec-25-admin-console`.

A single host-side GUI for the operator to run and observe the mahjong server:
start/stop the process, mint invites, manage accounts, watch who's connected and
what games are live, and read CPU/memory. Built so it keeps being useful as the
server grows — new server state is surfaced by extending one JSON status shape,
not by reworking the GUI.

## Goals

- **Lifecycle control.** Start / stop / restart the `serve` process from a button.
  Show status (running / stopped / crashed), uptime, and the listen URL.
- **Invites.** Mint, list, revoke invite codes.
- **Accounts.** List accounts; create; disable / re-enable; promote to admin.
- **Live view.** Connected players and active tables (which seats, which phase).
- **Resource usage.** CPU% and resident memory of the `serve` process.
- **One-click bootstrap.** A single launcher brings the console up and (optionally)
  the server with it, so the operator's whole ideal — *one click → server running +
  GUI monitoring* — is satisfied without touching a terminal after the first launch.
- **Live log tail.** Stream the server's stdout into the GUI.
- **Cloudflare tunnel control.** Start / stop `cloudflared`; show + copy the public URL.
- **Feedback inbox.** Read the bug/feature reports the server already writes to disk.
- **Health & storage.** `/health`, DB integrity, data-dir disk usage, WAL size.

## Non-goals (v1 — explicit deferrals)

- **AI-training dashboard.** A stub pane reserves the slot; no functionality yet.
- **Control-plane authentication and remote (non-loopback) binding.** v1 binds the
  control plane to loopback; the *loopback bind is the security boundary*. Reaching
  it from a remote laptop is done with the same Tailscale/tunnel you already use.
  Adding a control-plane login + network bind is a follow-up (see Open questions).
- **Attaching to a server the console did not start.** The console manages the
  child process *it* spawned. A manually-started `serve` is observable only via its
  public `/health`; the console can't read its admin status or stop it.
- **Multi-host / multiple concurrent server instances.** One host, one server.
- **Spectate-a-table, records/replay browser, one-click DB backup, live config
  editor.** Suggested but not selected for v1; deferred.

## Architecture

Three pieces. The naming convention worth stating: the console is a **control
plane** — it manages and observes the **data plane** (the game server). A process
can't start itself, so the control plane must be a *separate* process; this is the
**supervisor** pattern (cf. systemd / pm2 / supervisord).

```text
  browser (laptop)                  host machine
  ┌────────────┐   HTTP+WS    ┌──────────────────────────────────────┐
  │ admin web  │◀────────────▶│ control plane  (mahjong.control)      │
  │  UI (Lit)  │  ws://…:8500 │  - ServerSupervisor (spawns child)    │
  └────────────┘              │  - psutil metrics sampler             │
                              │  - log ring buffer                    │
                              │  - TunnelSupervisor (cloudflared)     │
                              │  - persistence (invites/accounts)     │
                              │      │ spawns                         │
                              │      ▼                                │
                              │  serve  (python -m mahjong serve)     │
                              │   GET /admin/status  (token-gated) ◀──┘ polled
                              │   ws://…:8400  game protocol  (public)
                              └──────────────────────────────────────┘
```

### 1. `serve` admin-status endpoint (data-plane addition)

The live "who's connected / what's playing" state lives in the running server's
**memory** (`TableRegistry`), not the DB. So the server must expose it. A new
read-only HTTP route on the *existing* listener (same mechanism as `/health`, via
`WebSocketServer._process_request` — no new dependency):

`GET /admin/status` →

```json
{
  "uptime_s": 11532,
  "listen_addr": "0.0.0.0:8400",
  "players_connected": 5,
  "tables": [
    {
      "table_id": 1,
      "ruleset": "mcr-2006",
      "hand_index": 3,
      "phase": "IN_PROGRESS",
      "seats": [
        {"seat": 0, "kind": "human", "occupied": true,  "user_id": "u_7"},
        {"seat": 1, "kind": "human", "occupied": true,  "user_id": "u_9"},
        {"seat": 2, "kind": "bot",   "occupied": true,  "bot_id": "canned-pass"},
        {"seat": 3, "kind": "human", "occupied": false}
      ]
    }
  ]
}
```

`tables[]` is exactly `registry.list_tables()` projected via the existing
`TableSummary.to_wire()` — reuse, no new projection. `players_connected` is the
count of distinct occupied human seats across tables (v1 approximation; a true
connection count can be added later without changing the shape).

**Why the data plane only exposes in-memory state:** uptime, PID, and metrics are
known to the control plane already (it spawned the child and holds the PID), so the
endpoint stays minimal — it reports only what the control plane *can't* derive.

**Auth.** The endpoint is mounted **only when `MAHJONG_ADMIN_TOKEN` is set** in the
`serve` environment, and every request must carry `Authorization: Bearer <token>`.

- Missing env var → route absent → `404`. A hand-started server has *no* admin
  surface. Secure by default.
- Present but wrong/absent token → `401`.

This matters because the server's listener may be **publicly exposed through the
tunnel** — the token is what keeps `/admin/status` safe on a port the internet can
reach. The control plane generates a fresh random token at spawn and injects it
into the child's environment (§2); they share it without the operator handling it.

`MAHJONG_ADMIN_TOKEN` is added to `_KNOWN_VARS` in `server/config.py` and read in
`cli/serve.py`; when set, `serve` registers the `/admin/status` handler on the
`WebSocketServer`.

### 2. Control plane (`mahjong.control`)

A new package and CLI entry point: `python -m mahjong control`. Async, single
process. Components:

**`ServerSupervisor`** — owns one `serve` child.

- States: `STOPPED → STARTING → RUNNING → STOPPING → STOPPED`, plus `CRASHED`
  (child exited unexpectedly while `RUNNING`).
- `start()` spawns `sys.executable -m mahjong serve` via
  `asyncio.create_subprocess_exec`, with environment = the operator's `MAHJONG_*`
  config **plus** a freshly generated `MAHJONG_ADMIN_TOKEN`. stdout/stderr are
  captured line-by-line into the log ring buffer (§ log buffer). Transition to
  `RUNNING` once the child's listener answers `/admin/status` (readiness probe),
  with a startup timeout.
- `stop()` sends `SIGTERM`, waits up to `shutdown_timeout_s`, then `SIGKILL`.
- `restart()` = `stop()` then `start()`.
- A watcher task `await`s the child; an exit while state is `RUNNING` → `CRASHED`
  (last log lines retained for post-mortem). An exit during `STOPPING` → `STOPPED`.

**Metrics sampler** — a background task polls `psutil.Process(pid)` every
`metrics_interval_s` (default 2.0): `cpu_percent(interval=None)` (needs two samples,
hence the timer) and `memory_info().rss`. Latest sample is read by `/api/status`.
`psutil` is a **new dependency** (added to `pyproject.toml`).

**Log ring buffer** — a bounded `collections.deque(maxlen=N)` (default 2000) of
the child's most recent stdout/stderr lines, each tagged with a monotonic line
number so the live-tail WS can resume from a cursor.

**`TunnelSupervisor`** — same spawn/stop pattern for
`cloudflared tunnel --url http://127.0.0.1:<serve_port>`. Parses the public URL
from cloudflared's stderr with `https://[a-z0-9-]+\.trycloudflare\.com`. Exposes
`{running, url}`. Independent of the server supervisor (you can run the server
without a tunnel and vice-versa). `cloudflared` is an **external binary**, not a
pip dep; if it's absent, `tunnel.start` returns a clear error.

**Persistence operations** — invite/account reads and writes go directly through
the existing `mahjong.persistence` layer (reuse `mint_invite`, `get_invite`,
`set_invite_disabled`, `create_account`, `set_account_disabled`, …). The control
plane opens its **own** DB connection. SQLite is in **WAL mode** (confirmed in
`persistence/db.py`): concurrent readers + a single serialized writer, so the
console writing an invite while `serve` reads accounts at auth time is safe.
Small persistence additions needed (the account CLI currently uses raw SQL):
`list_accounts()`, `list_invites()`, and a `set_account_role(id, role)` helper —
add them to the persistence API and have both the CLI and the console use them
(removing the CLI's inline SQL).

**Feedback inbox** — lists/reads `data_dir/reports/*.txt` (written by the server's
existing `_handle_feedback`). Parses the `type/submitted/submitter` header the
server writes.

**Health & storage** — server `/admin/status` reachability + `/health`,
`persistence.integrity_check()`, `shutil.disk_usage(data_dir)`, and the size of
`mahjong.db-wal`.

### Bootstrapping (the "one click" path)

The GUI is *served by* the control plane, so the control plane must be launched
before any browser interaction — a process can't start itself, and the GUI lives
*inside* the control plane. Once the control plane is up, starting/stopping the
**server** is a GUI button (the core feature). The bootstrap step is therefore
about launching the *control plane*, made one-click:

- **`python -m mahjong control`** flags:
  - `--open` — after binding, open the operator's browser at the dashboard URL
    (`webbrowser.open`).
  - `--autostart-server` — call `supervisor.start()` immediately on boot, so the
    server is already `RUNNING` by the time the browser loads. `--open
    --autostart-server` together = *one launch → server up + GUI monitoring*.
- **`scripts/mahjong-console`** — a portable bash launcher (`exec python -m mahjong
  control --open "$@"`) using the repo's venv. A sibling **`mahjong-console.command`**
  (same body) is Finder-double-clickable on macOS — no terminal after the first run.

The control plane *itself* still needs that one launch. Making it come up on boot
(systemd user service on Linux, launchd agent / login item on macOS) is the
production follow-up noted in Open questions; v1 ships the script.

### 3. Admin web UI (`mahjong/control/static/`)

Lit + CDN import-map, **no build step** — the exact stack as the game client
(`mahjong/web/static/`): an `index.html` with an import map for `lit`, a root
`<admin-app>` custom element, panes as child elements. Served by the control plane
over HTTP on the same listener that carries the admin WS.

**Transport (GUI ↔ control plane).** One WebSocket (subprotocol `mahjong-admin-v1`)
carries everything — commands, status snapshots, and the log stream — as JSON
messages. This mirrors the game client and avoids HTTP-POST-body handling under the
`websockets` library. Static assets are served over plain HTTP on the same
listener (same single-listener pattern as `serve`).

Panes: **Status** (running state, uptime, listen URL, CPU/mem, start/stop/restart),
**Tables**, **Players**, **Invites**, **Accounts**, **Logs**, **Tunnel**,
**Feedback**, **Health**, and a disabled **Training** stub.

**Flexibility mechanism.** The GUI renders from the `STATUS` snapshot and ignores
fields it doesn't recognise (additive evolution, same philosophy as the wire
protocol's `HELLO.features`). Adding a server capability = adding a field to
`/admin/status` and a read in the GUI; no protocol break.

## Control-plane WS protocol (`mahjong-admin-v1`)

Request/response by `kind`, plus server-pushed `STATUS` and `LOG` frames. v1 has
no auth handshake (loopback boundary). All frames are JSON objects with a `kind`.

### Client → control plane

| kind | fields | effect |
| --- | --- | --- |
| `SERVER_START` | — | `supervisor.start()` |
| `SERVER_STOP` | — | `supervisor.stop()` |
| `SERVER_RESTART` | — | `supervisor.restart()` |
| `TUNNEL_START` | — | `tunnel.start()` |
| `TUNNEL_STOP` | — | `tunnel.stop()` |
| `INVITE_CREATE` | `max_uses?`, `expires_days?` | mint; reply `INVITE_LIST` |
| `INVITE_REVOKE` | `code` | disable; reply `INVITE_LIST` |
| `INVITES_LIST` | — | reply `INVITE_LIST` |
| `ACCOUNT_CREATE` | `username`, `display?`, `password`, `admin?` | reply `ACCOUNT_LIST` |
| `ACCOUNT_SET_DISABLED` | `account_id`, `disabled` | reply `ACCOUNT_LIST` |
| `ACCOUNT_SET_ROLE` | `account_id`, `role` | reply `ACCOUNT_LIST` |
| `ACCOUNTS_LIST` | — | reply `ACCOUNT_LIST` |
| `FEEDBACK_LIST` | — | reply `FEEDBACK_LIST` |
| `LOG_SUBSCRIBE` | `from_line?` | begin `LOG` frames from cursor |

### Control plane → client

`STATUS` (pushed every `status_interval_s`, default 2.0, and after any command):

```json
{
  "kind": "STATUS",
  "server": {
    "state": "RUNNING",
    "pid": 48213,
    "uptime_s": 11532,
    "listen_url": "ws://0.0.0.0:8400",
    "cpu_pct": 4.2,
    "mem_rss_bytes": 96329728,
    "players_connected": 5,
    "tables": [ /* …registry projection from /admin/status… */ ]
  },
  "tunnel": { "running": true, "url": "https://calm-tree-1234.trycloudflare.com" },
  "health": { "admin_status_ok": true, "db_integrity_ok": true,
              "disk_free_bytes": 51200000000, "wal_bytes": 131072 }
}
```

When the server is `STOPPED`/`CRASHED`, `server.tables` is `[]`, metrics are
`null`, and `health.admin_status_ok` is `false`. `LOG` frame:
`{"kind":"LOG","line":1843,"text":"2026-06-03 12:00:01 INFO mahjong.serve server.ready …","stream":"stdout"}`.
Errors: `{"kind":"ERROR","code":"cloudflared_not_found","message":"…"}`.

## Configuration

Control-plane knobs (own `MAHJONG_CTL_*` namespace so they never collide with the
server's `MAHJONG_*`):

| var | default | meaning |
| --- | --- | --- |
| `MAHJONG_CTL_LISTEN_ADDR` | `127.0.0.1:8500` | control-plane bind (loopback by default — the v1 security boundary) |
| `MAHJONG_CTL_METRICS_INTERVAL_S` | `2.0` | psutil sample period |
| `MAHJONG_CTL_LOG_BUFFER_LINES` | `2000` | ring-buffer size |
| `MAHJONG_CTL_STARTUP_TIMEOUT_S` | `15` | readiness-probe deadline |

The `serve` child's config is the operator's existing `MAHJONG_*` environment
(reuse `load_config_from_env`), so the console and the server agree on `data_dir`,
`db_path`, and the listen port. The console derives the server's `/admin/status`
URL from `MAHJONG_LISTEN_ADDR` (loopback variant) + the generated token.

## Implementation order (walking skeleton first)

Per the working agreement: an end-to-end thin slice before depth. Steps 1–5 reach
a browser-verifiable skeleton (start/stop + live status); the rest layer on.

1. **`/admin/status` endpoint** (data plane, **test-first**): token gating
   (404 unset / 401 bad / 200 + shape), registry projection reuse. + `config.py`
   `MAHJONG_ADMIN_TOKEN`, `serve.py` mounts it.
2. **`ServerSupervisor`** (**test-first**): state machine, spawn/stop/restart,
   crash detection, readiness probe.
3. **Metrics sampler + log ring buffer** (sampler integration test may be `slow`).
4. **Control-plane WS server + `STATUS` aggregation + `SERVER_*` commands**
   (**test-first** on the message contract); static-asset serving.
5. **Web UI shell**: Status pane + start/stop/restart buttons, plus the bootstrap
   launcher (`--open`, `--autostart-server`, `scripts/mahjong-console[.command]`).
   **Walking skeleton complete — browser-verify the one-click path.**
6. **Invites**: persistence `list_invites`; pane + `INVITE_*`.
7. **Accounts**: persistence `list_accounts` + `set_account_role`; pane + `ACCOUNT_*`.
   Refactor the account CLI onto the shared helpers.
8. **Logs pane** + `LOG_SUBSCRIBE` live tail.
9. **`TunnelSupervisor`** + Tunnel pane (URL display + copy).
10. **Feedback inbox**: pane + `FEEDBACK_LIST`.
11. **Health & storage**: pane + `health` block.
12. **Docs/runbook** (extend the public-deployment runbook) + Training stub pane.

**TDD buckets** (per `CLAUDE.md`): test-first for the supervisor state machine, the
`/admin/status` contract, the WS message contract, and the persistence helpers
(protocol/contract surfaces). Pragmatic-cover for the Lit UI, CLI arg parsing, log
formatting, and tunnel-URL scraping.

## Alternatives considered

- **Serve the dashboard from inside `serve` itself.** Rejected: a process can't
  start itself — the chicken-and-egg breaks the core start/stop requirement. The
  console *must* be a separate supervisor.
- **systemd unit + `systemctl --user` from the GUI.** systemd is the production
  standard on the Linux target and *will* be the deploy mechanism — but it doesn't
  exist on the macOS dev box, and we want one console that works in both places. v1
  uses a portable subprocess supervisor; **systemd remains the documented
  production deploy path** (public-deployment § 24.5). Flagged conversion boundary:
  the supervisor and a systemd unit are two ways to own the same process; we keep
  the supervisor for the GUI and let systemd own the process in production
  (the GUI then observes via `/admin/status` rather than supervising — a known
  v1→prod seam, see Open questions).
- **Control plane talks to `serve` over the game WS (`LIST_TABLES`) instead of
  `/admin/status`.** Rejected for v1: it would require the console to hold an admin
  *account* + password and a persistent WS, and `LIST_TABLES` lacks uptime/player
  counts. A token-gated HTTP pull is smaller and account-free.
- **REST API (`/api/*`) for the GUI instead of one WS.** REST is the more standard
  admin-API shape and is curl-testable, but reading POST bodies under the
  `websockets` library is awkward, and the project's established in-codebase
  standard is WS+JSON messages. One WS also gives log streaming and status push for
  free. Static assets still go over plain HTTP.
- **aiohttp/starlette for the control plane.** Avoided: a new web framework/toolchain
  for one small surface. Reusing the `websockets` single-listener pattern keeps the
  only new dependency `psutil`.
- **Native desktop (Electron/Tauri) or a TUI.** The web dashboard reuses the
  existing Lit/no-build stack, is reachable from the laptop over the tunnel, and
  matches the project's earlier pivot away from Textual.

## Verification fixtures

- **`admin_status_token`** — `/admin/status`: env unset → 404; bad token → 401;
  good token → 200 with the documented JSON; `tables[]` equals the registry
  projection for a fixture with one in-progress and one waiting table.
- **`supervisor_lifecycle`** — `start()` spawns a child that binds the port and
  reaches `RUNNING`; `stop()` SIGTERMs it and the port frees; `restart()` cycles;
  an unexpected child exit flips state to `CRASHED` with last log lines retained.
- **`supervisor_metrics`** (may be `slow`) — running child reports `mem_rss_bytes
  > 0` and `cpu_pct >= 0`.
- **`ctl_status_aggregation`** — `STATUS` merges supervisor metrics + the server's
  `/admin/status` + tunnel + health; STOPPED server → `tables: []`, null metrics,
  `admin_status_ok: false`.
- **`ctl_invite_ops`** — `INVITE_CREATE` writes a row reachable by `get_invite`;
  `INVITE_REVOKE` disables it; `INVITES_LIST` reflects both.
- **`ctl_account_ops`** — `ACCOUNT_CREATE` produces an account that can log in via
  the auth path; `ACCOUNT_SET_DISABLED`/`SET_ROLE` mutate as listed.
- **`tunnel_url_parse`** — a recorded cloudflared stderr line yields the
  `trycloudflare.com` URL; absent binary → `cloudflared_not_found` error.
- **`log_ring_buffer`** — N lines in → last K retrievable; `LOG_SUBSCRIBE
  from_line` resumes from the cursor and delivers new lines.
- **`feedback_inbox`** — a report file on disk is listed with parsed
  type/submitter/text.
- **`ctl_binds_loopback`** — with default config the control-plane listener binds
  `127.0.0.1` (the v1 security boundary is enforced, not assumed).

## Open questions

1. **v1 → production supervision seam.** In production the server is owned by
   systemd, not the console's subprocess supervisor. Does the console then (a) shell
   out to `systemctl --user` for start/stop, or (b) drop to observe-only
   (`/admin/status` + metrics by PID lookup) and leave lifecycle to systemd? Leaning
   (a) behind a `MAHJONG_CTL_SUPERVISOR=subprocess|systemd` switch — but deferred
   until the RPi deploy is real.
2. **Control-plane auth for remote use.** When the laptop is remote from the host,
   the loopback boundary no longer suffices. Add a single admin password +
   non-loopback bind, or rely entirely on Tailscale ACLs / the tunnel's own access
   control? Decide when remote management is actually needed.
3. **`players_connected` fidelity.** v1 counts occupied human seats (misses
   spectators and lobby-but-unseated connections). A true connection count needs the
   orchestrator to track live sockets — additive to `/admin/status` later.
