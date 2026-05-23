# Session handoff — 2026-05-23 (end of 7.5a)

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 is 5 of 7+ sub-steps complete. All committed on `main`.** Step 7.5 was split into 7.5a/b/c after a mid-session pivot: the TUI is now a **browser-served ASCII client**, not a Textual terminal app.

| # | Step | Commit | Notes |
| --- | --- | --- | --- |
| 7.0 | Public projection amendment | `aa35890` (in 7.2 bundle) | `project(state, seat: int \| None)`, `project_event` |
| 7.1 | Wire codec | `aa35890` | 25 TypedDicts, `encode`/`decode`, `KNOWN_KINDS` |
| 7.2 | WebSocket transport | `aa35890` / `4cd666f` | `WebSocketServer`, subprotocol gate, `/health` |
| 7.3 | Session multiplexer | `e74d265` | `TableSessions`, `SeatSession`, `Spectator`, ring buffer, hold timer |
| 7.4 | HumanAdapter | `a49bcf6` | `SeatAdapter` impl wrapping a `SeatSession` |
| **7.5a** | **Web-client walking skeleton** | **`694b790`** | **Browser + Lit + WS round-trip; visually verified** |
| 7.5b | Pane-toggle shell + stub panes | pending | Authorized "shell-early" by user |
| 7.5c | Fill game pane (real snapshot render + PROMPT/ACTION) | pending | — |
| 7.6 | End-to-end S2 fixture | pending | The S2 exit gate |

**Verification at end of session:** ruff clean · ruff-format clean · mypy clean (57 source files; +2 from `mahjong/web/`) · **515 tests pass repo-wide** (6 new + 509 prior; 2 Linux-only skipped on macOS). S0 walking-skeleton regression (Step 4.2 gate) still byte-identical.

## The 7.5 pivot (read this first if you're new to the session)

**Decision 2026-05-23:** Layer 7.5 is no longer a Textual terminal app. It is a **web-based ASCII client** accessed via a browser. The terminal aesthetic (monospace, ASCII glyphs, sparse color, no animations) is preserved; the runtime is a browser, not a TTY.

**Why:** install friction. Target users are home-hosted MCR mahjong players (friends, family) who won't install Python or use a terminal. A URL is the lowest-friction handshake.

**What this changed:**

- `docs/specs/tui-client.md` rewritten in place — Textual → Lit web components, `Pilot` → Playwright, "screens" → "pages with toggleable panes." Stack-agnostic contracts (privacy DiD, bilingual EN/ZH, crash resistance, event-application-client-side) preserved verbatim.
- Wire-protocol layer (7.0–7.4) **unaffected** — that's the payoff for building wire-first.
- 7.5 split into 7.5a/b/c so the walking skeleton, shell, and content land separately.

**Locked vision:**

- Browser-served, no client install.
- Aesthetic: monospace, ASCII glyphs (🀇 …), sparse color, no animations.
- **Window model: fixed layout with toggleable panes.** Not free-floating draggable windows.
- Four planned panes: **game** (primary), **chat**, **stats**, **spectator-of-another-table**. Only game is mandatory in v1; the others land per scope decision below.
- **Wire-protocol gaps surfaced and not yet addressed:** no CHAT frames; no stats request/response (cross-game stats also need Layer 8 / SQLite); spectate-while-playing will use a second WebSocket per spectated table rather than a multi-subscription wire amendment.

See [project_client_vision_web_ascii memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_client_vision_web_ascii.md) for the locked decisions; [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) for the broader Layer 7 progress.

## What landed this session (7.5a)

### Step 7.5a — Web-client walking skeleton (`694b790`)

Files added / modified:

| Path | Purpose |
| --- | --- |
| `docs/specs/tui-client.md` | Rewritten in place: web/Lit/Playwright/panes architecture, 18 verification fixtures |
| `mahjong/wire/server.py` | Adds `static_dir=` kwarg to `WebSocketServer`; `/` and `/static/<path>` routes alongside `/health`; path-traversal defense via `Path.resolve().is_relative_to`; content-type map for HTML/JS/CSS/JSON/SVG/woff/etc |
| `mahjong/web/__init__.py` | `static_root()` returns the bundled-assets path |
| `mahjong/web/demo.py` | `python -m mahjong.web.demo` boots a server with the static dir and a scripted handler (HELLO + ERROR-echo) |
| `mahjong/web/static/index.html` | Page shell, import-map for Lit from jsDelivr |
| `mahjong/web/static/style.css` | Terminal aesthetic — monospace, dark green-on-black, no ligatures |
| `mahjong/web/static/app.js` | `ConnectionManager` (WebSocket wrapper, dispatches CustomEvents) + `<mahjong-app>` + `<game-pane>` (wire log) |
| `tests/wire/test_server.py` | 6 new tests: static root, nested asset, path traversal, missing file, no-static-dir fallthrough, WS-upgrade-still-works-with-static-configured |

### Verification artifact (manual browser smoke)

User ran `.venv/bin/python -m mahjong.web.demo` and opened `http://127.0.0.1:8400/` in a real browser. Observed: ASCII header painted, `Connection: connected` (green), HELLO frame rendered in the wire log. The end-to-end loop closes.

## Pinned decisions reaffirmed or added this session

Numbered continuing from prior sessions in `project_layer7_status` memory — decisions 1–7 are pre-7.5a; 8–12 below are new this session.

- **(8) Static assets served from the same Python listener.** Not a separate process / reverse proxy. The `_process_request` hook in `mahjong/wire/server.py` was already serving `/health`; extending it for `/` and `/static/<path>` is a ~30-line addition. Matches the home-server deployment target.
- **(9) Lit loaded from a CDN via import map** in `index.html`. No build step, no vendoring. Trade-off: requires internet at first page load. Acceptable for v1; vendor locally if offline becomes a requirement (1-file drop + import-map path edit).
- **(10) Path-traversal defense via `Path.resolve().is_relative_to(static_root)`.** `resolve()` collapses `..`; `is_relative_to` rejects anything that escaped. Unit-tested with `GET /static/../secret.txt` returning 404.
- **(11) WS-upgrade requests skip the static lookup** via the `Sec-WebSocket-Protocol` header check. Avoids any pathological collision between a WS path and a static file.
- **(12) 7.5b scope decision: shell-early.** User authorized building the pane-toggle architecture next (over filling the game pane first), so the modular-windows vision is visible before content lands. Cheaper to iterate the shell when it's empty than when it's full.

## Known limitations carried forward

- **HAND_END double-emit risk.** Engine emits HAND_END as a record event; `SeatSession.observe()` would wrap it in EVENT, but per `wire-protocol.md` HAND_END is its own top-level frame. Not blocking 7.3/7.4/7.5a (unit tests fan via explicit `fanout_hand_end`; 7.5a demo handler isn't driving an engine). Will surface in 7.6 e2e. Likely fix: filter HAND_END out of `SeatSession.observe()`'s EVENT path.
- **The 7.5a demo handler is scripted, not a real table manager.** It sends HELLO and ERROR-echoes everything else. The bridge between the web client and a real `TableManager` + `TableSessions` lands in 7.5c / 7.6.

## What remains for next session

The remaining Layer 7 sub-steps, in order:

- **7.5b — Pane-toggle shell + stub panes.** User-authorized for next. Scope:
  - Extract `<table-page>` as a host element on `<mahjong-app>`.
  - Add `<chat-pane>`, `<stats-pane>`, `<spectator-pane>` as Lit elements with placeholder content (`(chat pane — not yet implemented)` etc).
  - CSS Grid layout per `tui-client.md` (game on left, chat+stats stacked right, spectator across the bottom).
  - Per-pane visibility state held on `<mahjong-app>` (survives route transitions).
  - **Hotkey collision warning:** player-action keys include `C` (Chi), `P` (Pass/Peng), `H` (Hu), `S` doesn't collide. Pane-toggle hotkeys should pick a namespace that doesn't collide — recommend Alt-modifier (Alt+C, Alt+S) or a chord prefix (`,c`, `,s`). Resolve this before binding.
  - Game-pane content stays as today's wire-log render. The shell change is purely structural.
  - Spec fixture 17 (pane toggle) is the explicit verification target.
- **7.5c — Fill the game pane.** Render `ATTACHED.snapshot` as the real ASCII table layout (three opponent rows, discard pool, own hand, prompt bar). Implement `applyEventToLocalState`. Wire PROMPT → ACTION round-trip. Bilingual EN/ZH rendering. Spec fixtures 5–9 land here.
- **7.6 — End-to-end S2 fixture.** The S2 exit gate. Stand up a real `TableManager` + `TableSessions` + `HumanAdapter` behind the WS server; Playwright drives a browser instance through one seat while three `CannedAdapter`s play the others; record byte-identical to a checked-in fixture. Closes Layer 7 and the S2 milestone.

Then Layer 8 (sub-steps 8.1–8.6) is the full S3 surface — SQLite, auth, persistence, multi-table, server-lifecycle.

## Outstanding questions / decisions for the user

- **7.5b hotkey scheme.** Modifier keys (Alt+C / Alt+S) vs chord prefix (`,c` / `,s`) vs distinct keys (`F2` / `F3`) for pane toggles, so they don't collide with the player-action keys (`C` for Chi, `H` for Hu, etc). Decide before binding.
- **Playwright setup.** Not yet installed. Defer until 7.5b lands or earlier — one-time cost.
- **Chat wire-protocol amendment.** Required before chat pane can do anything real. Not blocking 7.5b's stub.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) and [project_client_vision_web_ascii memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_client_vision_web_ascii.md).
- [ ] Verify `git log --oneline -5` shows `694b790` (Step 7.5a) at HEAD.
- [ ] Run `.venv/bin/python -m pytest` and confirm 515 passing, 2 Linux-only skipped.
- [ ] Optional sanity: `.venv/bin/python -m mahjong.web.demo` → open `http://127.0.0.1:8400/` → confirm HELLO renders.
- [ ] Decide hotkey scheme for 7.5b pane toggles before binding.
- [ ] Start 7.5b shell extraction. Spec fixture 17 is the test-first target.
- [ ] Address the HAND_END double-emit limitation before 7.6 e2e.
