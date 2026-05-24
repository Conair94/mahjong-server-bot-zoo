# Session handoff — 2026-05-24 (end of 7.5c.ii)

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 is 7 of 9 effective sub-steps complete. All committed on `main`.** Step 7.5c was split into 7.5c.i / 7.5c.ii / 7.5c.iii in conversation as the scope became clear. The browser client now renders a live, animating mahjong table from real engine events — just no PROMPT / ACTION round-trip yet.

| # | Step | Commit | Notes |
| --- | --- | --- | --- |
| 7.0 | Public projection amendment | `aa35890` (in 7.2 bundle) | `project(state, seat: int \| None)`, `project_event` |
| 7.1 | Wire codec | `aa35890` | 25 TypedDicts, `encode`/`decode`, `KNOWN_KINDS` |
| 7.2 | WebSocket transport | `aa35890` / `4cd666f` | `WebSocketServer`, subprotocol gate, `/health` |
| 7.3 | Session multiplexer | `e74d265` | `TableSessions`, `SeatSession`, `Spectator`, ring buffer, hold timer |
| 7.4 | HumanAdapter | `a49bcf6` | `SeatAdapter` impl wrapping a `SeatSession` |
| 7.5a | Web-client walking skeleton | `694b790` | Browser + Lit + WS round-trip; visually verified |
| 7.5b | Pane-toggle shell + light theme | `9fb7037` | `<table-page>` host, four pane elements, Alt-modifier hotkeys, theme system |
| **7.5c.i** | **Snapshot rendering** | **`f566340`** | **`SeatView` → ASCII table; tile styling; Unicode toggle** |
| **7.5c.ii** | **applyEvent reducer** | **`e376cde`** | **Live SeatView mutation per EVENT; demo drives real engine** |
| 7.5c.iii | PROMPT / ACTION round-trip | pending | The remaining 7.5c piece |
| 7.6 | End-to-end S2 fixture | pending | The S2 exit gate |

**Verification at end of session:** ruff clean · ruff-format clean · mypy clean (58 source files; +1 over 7.5a from `mahjong/web/demo.py` growth) · **515 tests pass repo-wide** (no new tests — JS-side verification is visual until Playwright lands).

## What this session built

### 7.5b — pane-toggle shell + light theme (`9fb7037`)

- `<table-page>` extracted as the host for the four-pane CSS Grid (game / chat / stats / spectator).
- `<chat-pane>`, `<stats-pane>`, `<spectator-pane>` shipped as Lit stubs with placeholder content. Real implementation blocked on wire-protocol amendments (CHAT frames, STATS request/response — see open items below).
- **Pane-toggle hotkeys use the Alt modifier** so they don't collide with player-action keys: `Alt+C` chat, `Alt+S` stats, `Alt+W` (watch) spectator. Bare `C / P / H / G / B` stay reserved for in-game Chi / Pass-or-Peng / Hu / Gang / Bugang.
- **Light theme** added — classic MCR tile palette (ivory bg `#fbf8ee`, bamboo jade `#1f7a3a`, character vermilion `#b8362a`, deeper vermilion for errors). Toggle via `Alt+T` or the header button; persisted to `localStorage`. Themes are `:root[data-theme=…]` CSS-custom-property swaps; shadow-DOM components inherit through the cascade with no per-component theming.
- Decorative `--accent-red` variable is **distinct** from `--error` so red chrome (header ASCII border, table-page strip, always-on `[G]` indicator) doesn't dilute the alarm signal.

### 7.5c.i — snapshot rendering (`f566340`)

- New `mahjong/web/static/render.js` exposes `renderTable(seatView, ownSeat, options)` as a pure Lit-template function. The `<game-pane>` drops it into a `<div class="table-ascii">` wrapper.
- **Display conventions (locked in conversation):**
  - **Seat layout** top-to-bottom: right opponent → across → left opponent → you. (Mahjong play is counterclockwise, so right = `(own+1)%4`.) Each block labels its geometric position (`your right` / `across` / `your left` / `— YOU`).
  - **Seat labels** are `Wind (Seat N)` — e.g. `East (Seat 1)`. Wind rotates between hands; seat number is fixed 1–4. `fullSeatName(view, idx)` is the single helper used everywhere.
  - **ASCII tile shorthand** rank-first with the suit letter remapped to match English: engine `W` (wan / characters) → display `C` red, engine `B` (bing / dots) → display `D` fg, engine `T` (tiao / bamboo) → display `B` green. Winds: direction+W (`EW`/`SW`/`WW`/`NW`). Flowers: rank+F.
  - **Dragons always render as colored Unicode glyphs** (`🀄`/`🀅`/`🀆`) regardless of `tile_style`. No clean ASCII form.
  - **Unicode toggle** swaps suited/wind/flower shorthand for `U+1F000..U+1F029` glyphs. `Alt+U` or header button; persisted as `localStorage["mahjong-tile-style"]`. Default `ascii` (Unicode mahjong font support is uneven).
  - **Tile font-size** is `1.8em` (2.2em for dragons / face-down) so Unicode glyphs read clearly. Body bumped 14 → 17px.
  - **Section dividers are CSS hairlines, not ASCII rule lines.** A fixed `─×74` overflowed the game pane when side panes opened; `<hr class="ascii-rule">` stretches to the actual container width.
  - **Flowers render as actual tiles** (`Flowers:   6F 3F`), not a count tag. The `[1F]` count notation collided with the rank+F shorthand for tile H1 and read as a duplicate-data bug.

### 7.5c.ii — applyEvent reducer (`e376cde`)

- New `mahjong/web/static/apply_event.js` exposes `applyEvent(seatView, event, ownSeat) → newSeatView` — a pure reducer.
- Coverage of the engine's record event vocabulary (`mahjong/records/diff.py`):
  - **DRAW**: own-seat appends the visible tile, opponent path bumps the concealed count by 1. Wall ticks down. Auto-replaced flowers consume extra wall positions.
  - **DISCARD**: removes tile from concealed (list or count), appends to `seat.discards`, sets `last_discard`, clears `last_drawn`.
  - **CLAIM_WINDOW**: phase + turn_index update only.
  - **CLAIM_DECISION**: PASS / HU informational; PENG / CHI / GANG form melds (arity-aware decrement for opponents), pull the called tile back off the discarder's pile, set `current_actor` to the claimer. GANG handles EXPOSED / CONCEALED / ADDED — ADDED upgrades the existing PENG meld in place.
  - **CLAIM_RESOLUTION**: phase update only.
  - **HAND_END**: sets terminal block + applies `score_delta` per seat.
- `<mahjong-app>` now applies each EVENT to the current seatView and pushes the result back to `<game-pane>`.
- **Demo now drives the real engine** via `apply_action` + `diff_to_events` for 12 ticks at 0.5s intervals. `_drive_one_tick` MUST prefer `state["current_actor"]` over a naive 0..3 sweep, otherwise CLAIM_WINDOW phases loop forever applying seat-0 PASSes.
- **Round/Phase metadata strip moved to the BOTTOM** of the game pane (just above the wire-log toggle). Decided in conversation 2026-05-24 — keeps player attention on the table itself.

## Pinned decisions reaffirmed or added this session

Numbered continuing from prior sessions in `project_layer7_status` memory — decisions 12–20 are new this session.

- **(12) Pane-toggle hotkeys use the Alt modifier.** Picked over chord-prefix and F-keys.
- **(13) Theme + tile-style live on `<mahjong-app>`** as CSS-custom-property swaps on `:root[data-theme]`. Both persisted to `localStorage`; toggled via `Alt+T` / `Alt+U` or header buttons.
- **(14) Tile rendering returns Lit templates, not strings.** Required for per-span CSS coloring. Tile color rules MUST live inside `<game-pane>`'s static styles — document-level CSS does not pierce shadow DOM (only custom properties inherit).
- **(15) ASCII shorthand convention locked**: rank-first; suit letter remapped (W→C, B→D, T→B); winds direction+W; flowers rank+F; dragons always colored Unicode glyphs.
- **(16) Section dividers are CSS hairlines.** ASCII rule lines overflowed the pane when side panes opened.
- **(17) Seat labels include both wind name AND 1-indexed seat number** (`East (Seat 1)`).
- **(18) Round/Phase metadata at bottom of game pane**, not top.
- **(19) `applyEvent` is a pure reducer**, not in-place. `cloneSeatView` does the work; renderer relies on identity comparison.
- **(20) Demo `_drive_one_tick` prefers `current_actor`** before sweeping seats.

## Known limitations carried forward

- **HAND_END double-emit risk** (carried from 7.3). Engine emits HAND_END as a record event; `SeatSession.observe()` would wrap it in an EVENT frame, but per `wire-protocol.md` HAND_END is its own top-level wire frame. Not blocking 7.3–7.5c.ii (unit tests fan via explicit `fanout_hand_end`; the demo handler currently runs `_drive_one_tick` 12 times which doesn't reach a real terminal). Will surface in 7.6 e2e. Likely fix: filter HAND_END out of `SeatSession.observe()`'s EVENT path and route to a dedicated `HAND_END` frame sender.
- **The 7.5c.ii demo handler still isn't a real table manager.** It drives `apply_action` + `diff_to_events` directly. The bridge to a real `TableManager` + `TableSessions` + `HumanAdapter` lands in 7.6.
- **Opponent meld formation can't reconstruct which specific concealed tiles left.** We only have a count. The reducer decrements by the meld arity (2 / 3 / 4) and trusts the meld payload for what to show in the meld bar. This is fine — opponents' specific concealed tiles were never visible anyway.
- **Concealed tile sorting after DRAW.** Newly-drawn tiles append at the end of the hand. The canonical sort invariant lives in the engine, not the projection — matches a physical table.

## What remains for next session

- **7.5c.iii — PROMPT bar + ACTION round-trip + illegal-action banner.** Spec fixtures 7, 8, 9. Scope:
  - Render `PROMPT.legal_actions` as a key-binded action bar in `<game-pane>` when a PROMPT is outstanding (fixture 7).
  - Listen for matching keystrokes; send `ACTION` over `ConnectionManager`. Number keys 1-9 + 0/-/= for tile selection (13 tiles), bare letters `G P C H B` for special actions (fixture 8). MUST early-return when `e.altKey` is set so pane-toggle chords aren't double-fired.
  - On server `ERROR { code: "illegal_action" }`, show a transient banner without closing the prompt (fixture 9).
  - Demo handler will need to issue PROMPTs when its own seat (0) is on-turn and accept ACTIONs back via `apply_action`.
- **7.5c.iv (optional) — bilingual EN/ZH rendering** (fixture 15). All user-visible strings through `t(key, locale)` against `mahjong/web/static/locales/{en,zh,bilingual}.json`. Theme/tile-style toggles get a sibling `Alt+L` locale toggle.
- **7.6 — End-to-end S2 fixture** (the S2 exit gate). Real `TableManager` + `TableSessions` + `HumanAdapter` behind WS; Playwright drives a browser instance through one seat while three `CannedAdapter`s play the others; record byte-identical to a checked-in fixture. Closes Layer 7 and S2.

Then Layer 8 (sub-steps 8.1–8.6) is the full S3 surface — SQLite, auth, persistence, multi-table, server-lifecycle.

## Outstanding questions / decisions for the user

- **PROMPT key bindings detail.** Number keys 1-13 (using `1234567890-=` for 13-tile selection) vs arrow-keys + Enter for tile cursor navigation. Spec's `<game-pane>` template suggests the direct-key approach. Confirm before binding.
- **Playwright setup.** Still not installed. One-time cost. Will be needed for 7.5c.iii fixtures and definitely for 7.6. Probably worth standing up before 7.5c.iii lands.
- **Chat wire-protocol amendment.** Required before the chat pane does anything real. Not blocking 7.5c.iii.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) and [project_client_vision_web_ascii memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_client_vision_web_ascii.md).
- [ ] Verify `git log --oneline -5` shows `e376cde` (Step 7.5c.ii) at HEAD.
- [ ] Run `.venv/bin/python -m pytest` and confirm 515 passing, 2 Linux-only skipped.
- [ ] Optional sanity: `.venv/bin/python -m mahjong.web.demo` → open `http://127.0.0.1:8400/` → watch the table animate through 12 engine ticks.
- [ ] Decide PROMPT key-binding scheme before binding (number keys for tiles vs cursor navigation).
- [ ] Start 7.5c.iii. Spec fixtures 7, 8, 9 are the test-first targets.
- [ ] Address the HAND_END double-emit limitation before 7.6 e2e.
