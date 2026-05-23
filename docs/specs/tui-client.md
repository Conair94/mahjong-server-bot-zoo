# Spec 12 — Web ASCII client architecture

The browser-served ASCII client that humans use to log in, browse tables, play hands, and spectate. This is the *only* end-user consumer of [wire-protocol.md](wire-protocol.md) we ship in v1; bots use their own adapters, not this client.

The client runs in a standard web browser (Chrome, Firefox, Safari, modern Edge). It preserves the terminal aesthetic — monospace, ASCII glyphs, sparse color, no animations — but the runtime is the browser, not a TTY. The motivation is reach: many target users (home-hosted MCR mahjong with friends and family) will never install Python or use a terminal. A URL is the lowest-friction handshake.

This is a tier-2 spec — single consumer, lower blast radius if wrong. The interface that *would* be high-blast-radius (the wire) is already pinned in [wire-protocol.md](wire-protocol.md); this spec covers how the web client translates the wire into pixels-in-a-browser and keystrokes back onto the wire.

Builds on [wire-protocol.md](wire-protocol.md) (the only network surface), [state-schema.md](state-schema.md) (the shape of `SeatView` and `PublicView` the client renders), and [seat-port.md](seat-port.md) (only conceptually — the client ↔ server flow mirrors the seat-port's `observe` / `decide` cycle, but the client doesn't import any seat-port code).

**Status:** draft, pre-S2 implementation. Stack pivoted from Textual to web-served Lit components on 2026-05-23.

## Goals

- **Plain mode in v1.** No analysis overlays, no shanten hint, no opponent-hand forecaster. Just clear rendering of the public table state and the player's own hand. Overlays are S4; the renderer must be structured so an overlay is a pluggable layer, but no overlay code ships in v1.
- **Browser-accessible, no install.** The Python server hosts both the static client and the WebSocket endpoint; a user opens a URL and plays. No Python install on the client side; no bundler running on the client side; no separate web server.
- **Spectator is first-class.** Per [s2-s3-plan.md](../s2-s3-plan.md), spectating is heavy early-stage usage. The client must expose a "watch a table" flow from the lobby, render the public projection without leaking concealed-information *even if the wire accidentally sends some* (defense in depth at the rendering layer), and let the spectator switch tables or upgrade to a player without quitting.
- **Toggleable panes within a table page.** Once a user is at a table, they can show/hide auxiliary panes (chat, stats, spectator-of-another-table) alongside the primary game pane. Layout is fixed (CSS Grid slots); panes are not free-floating draggable windows. Toggle via hotkey or pane-header button.
- **Headless-testable.** Every page and pane is exercised in CI via [Playwright](https://playwright.dev/). No real browser screenshots reviewed by hand in the verification ladder.
- **Bilingual labels (EN/ZH).** Tile names, action names, screen labels carry both forms (e.g. "5 Bamboo / 五条"). The display layer takes a `Locale` setting that picks which side renders prominently; v1 ships both languages always-rendered. Future work can hide one per user preference.
- **Crash-resistant.** A bug in a rendering path must not lose the WebSocket connection or the user's seat. Rendering errors are caught at the component boundary, logged, and replaced with an `[render error]` placeholder the user can dismiss.

## Non-goals

- **Not the wire protocol.** All messages, framing, error codes, and reconnect semantics are [wire-protocol.md](wire-protocol.md)'s. This spec consumes them.
- **Not a generic mahjong UI toolkit.** Tile rendering, meld layout, and discard-pile rendering are coded for MCR; supporting other rule sets means adding rendering branches when that work ships (S5+).
- **Not animations.** v1 renders state changes as immediate re-renders. No tile-flying animations, no draw animations. A discard appears in the pile the moment the `EVENT` arrives.
- **Not voice / sound.** Pure text.
- **Not a Textual / terminal client.** Earlier drafts targeted Textual. Pivoted 2026-05-23 to a browser client to lower install friction. The terminal *aesthetic* is preserved.
- **Not free-floating windows.** Panes are fixed-layout toggleable slots, not draggable windows. WM-style behavior is out of scope.
- **Not configuration UI.** Settings (locale, key bindings, server URL) live in URL query params and `localStorage` in v1. A settings panel comes later if real users want it.
- **Not analysis overlays.** S4. The renderer must be structured to admit overlays additively; no overlay code ships here.
- **Not a build step.** v1 uses ES modules served as-is. Lit ships an ES-module entry point that browsers run directly. Adding a bundler is a future decision once asset count justifies it.

## Tech stack

- **[Lit](https://lit.dev/) 3.x web components**, loaded via native ES modules. Each pane and reusable widget is a custom element. Lit is a ~5KB declarative-template wrapper around the Web Components standard (Custom Elements + Shadow DOM + tagged-template literals). Browsers run it natively; no transpilation needed for modern targets.
- **Native browser [`WebSocket`](https://developer.mozilla.org/docs/Web/API/WebSocket) API.** Same JSON-over-WS frames the wire codec already emits — no client-side codec; `JSON.parse` / `JSON.stringify` are enough since the wire is JSON.
- **CSS Grid for pane layout.** Monospace system font stack (`ui-monospace, "SF Mono", Menlo, Consolas, monospace`). Sparse color palette (terminal-green-on-black default theme; light theme later).
- **No framework router.** Page transitions (login → lobby → table) are handled by a top-level `<mahjong-app>` Lit element with a single reactive `route` property. No `react-router`, no `vue-router`, no HTML5 history API in v1 (URL fragments only if needed later).
- **Static assets served from `mahjong/wire/server.py`.** The existing `process_request` hook (which already serves `/health`) is extended to serve files from `mahjong/web/static/`. Avoids a second process for the home-server deployment target — see [project_hosting_target memory](../../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_hosting_target.md).
- **Testing with [Playwright](https://playwright.dev/) (Python bindings).** Per-page and per-pane scripted-keystroke tests. The same `tests/wire/` fixtures back the scripted server side.

This is *behavior cloning of an existing pattern* — the v1 server already serves `/health` as an HTTP route on the same listener as the WebSocket. We're extending the same pattern for `/`, `/static/*` rather than introducing a separate ASGI app or reverse proxy. **Why this pattern exists:** for a single-process home-server deployment, fewer moving parts means fewer ways to misconfigure ports, TLS, and Tailscale endpoints.

## App architecture

```text
                ┌───────────────────────────────────────────────┐
                │             <mahjong-app>                      │
                │  (Lit element; owns ConnectionManager,         │
                │   route state, user identity, locale)          │
                └──────────────────────┬────────────────────────┘
                                       │ route ∈
                ┌──────────────────────┼─────────────────────────┐
                │                      │                          │
                v                      v                          v
       <login-page>            <lobby-page>             <table-page>
                                                       (hosts panes:
                                                         <game-pane>,
                                                         <chat-pane>,
                                                         <stats-pane>,
                                                         <spectator-pane>)
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │       ConnectionManager        │   <-- thin wrapper around
                       │  (one per <mahjong-app>)       │       browser WebSocket,
                       │                                │       reconnects with token,
                       │                                │       routes inbound by `kind`
                       └───────────────────────────────┘
                                       │
                                       ▼
                              wire-protocol.md
```

### `<mahjong-app>`

The root Lit custom element. Mounted into `<body>` once. Owns:

- The `ConnectionManager` instance (singleton per app).
- `route: 'login' | 'lobby' | 'table'` — controls which page element renders.
- The local user identity (`user_id`, `display_name`, `session_token`) after login.
- The locale setting (`en` / `zh` / `bilingual`).
- The currently-active table (`table_id` and role: `player` or `spectator`) when `route === 'table'`.

Lifecycle:

1. Page loads → `<mahjong-app>` upgrades → load config (URL query params, `localStorage`) → `ConnectionManager.connect()`.
2. On WebSocket open, HELLO is exchanged; we are at the auth phase.
3. App attempts `RESUME` with a stored token (if any). On success → `route = 'lobby'`. On failure → `route = 'login'`.
4. From the Lobby, the user picks a table → `route = 'table'` with role+table_id set.
5. Hand end → `<hand-end-modal>` (overlay) → either dismiss for next hand or pop to `route = 'lobby'`.
6. Tab close → browser tears down the WebSocket; server treats as a normal drop and holds the seat per `session-mux.md`.

### `ConnectionManager`

A plain TypeScript-flavored ES class (not a Lit element). Exposes:

```js
class ConnectionManager {
  async connect(url)           // open WebSocket, await HELLO
  send(message)                // JSON.stringify and ws.send
  // inbound is push-only via events:
  addEventListener('message', handler)     // (msg: parsed JSON) => void
  addEventListener('disconnect', handler)
  addEventListener('reconnect', handler)
  async close(code = 1000)
}
```

Inbound messages are dispatched as DOM `CustomEvent`s on the manager; pages subscribe and filter by `kind`. The pages do not poll; rendering is driven by inbound events.

Reconnect policy is *manager-internal* — on WebSocket drop the manager attempts a single `RESUME` after a 1s backoff. If it fails, the manager dispatches `disconnect`; the active page shows a banner. The client does not aggressively spam-reconnect; one attempt, then user-prompted retry.

## Pages

### `<login-page>`

```text
 ╔══════════════════════════════════════════════╗
 ║                                              ║
 ║          Mahjong  / 麻将                      ║
 ║                                              ║
 ║   Username   _____________________________   ║
 ║                                              ║
 ║   Password   _____________________________   ║
 ║                                              ║
 ║              [ Log in ]    [ Quit ]          ║
 ║                                              ║
 ║   server: ws://mahjong.tail-xxxx.ts.net      ║
 ║                                              ║
 ╚══════════════════════════════════════════════╝
```

Renders centered inside the browser viewport using CSS Grid. The ASCII border characters are rendered as text inside a `<pre>`-like block; CSS pins the monospace font and disables ligatures.

Submits `AUTH_REQUEST` with `username` + `password`. On `AUTH_RESPONSE { ok: true }` stores the `session_token` in `localStorage` (under `mahjong.session_token`) for resume-on-reload, then sets `<mahjong-app>.route = 'lobby'`. On `ok: false` displays a generic "Login failed" message — no distinction between unknown-user and wrong-password (per [auth.md](auth.md) failure-shape requirement).

Skip-able if `?no_auth=1` URL param is set (S2 local dev mode); the app jumps straight to Lobby with a hard-coded user.

### `<lobby-page>`

```text
 ╔════════════════════════════════════════════════════════════╗
 ║  Lobby — Alice / 爱丽丝                                     ║
 ║                                                            ║
 ║   Tables:                                                  ║
 ║   ┌──────────────────────────────────────────────────┐    ║
 ║   │ #17  MCR-2006   [E:Alice][S:Bob][W:bot][N:open]  │ ►  ║
 ║   │ #18  MCR-2006   [E:Carol][S:bot][W:bot][N:bot]   │    ║
 ║   │ #19  MCR-2006   ◐ 3 watching                     │    ║
 ║   └──────────────────────────────────────────────────┘    ║
 ║                                                            ║
 ║   [ J ] Join open seat   [ W ] Watch                       ║
 ║   [ N ] New table        [ R ] Refresh    [ Q ] Quit       ║
 ╚════════════════════════════════════════════════════════════╝
```

Receives `TABLE_LIST` after sending `LIST_TABLES` on mount. Refreshes on `R`. Selecting a table + pressing `J` sends `ATTACH` with the first open seat. Pressing `W` sends `SPECTATE`. `N` opens a create-table modal (S3+; in S2 anyone can create).

Keyboard handling: a top-level `keydown` listener on the page element dispatches by key. Pressing `Tab` between tables moves the selection; arrow keys also supported.

### `<table-page>`

The main game view. Hosts a fixed-layout grid of **panes**, of which only the **game pane** is mandatory. Each pane is a Lit custom element. Panes can be toggled on/off via hotkey or a pane-header button.

```text
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Table #17  MCR-2006  Hand 1/N  Wind: East  Wall: 38   Panes: G C S Sp│
 ├────────────────────────────────────────────┬─────────────────────────┤
 │                                            │                         │
 │            <game-pane>                     │     <chat-pane>          │
 │   (full table layout, own hand, prompt)    │   (toggle: C)            │
 │                                            │                         │
 │                                            ├─────────────────────────┤
 │                                            │     <stats-pane>         │
 │                                            │   (toggle: S)            │
 ├────────────────────────────────────────────┴─────────────────────────┤
 │            <spectator-pane>                                          │
 │   (watch another table; toggle: Sp)                                  │
 └──────────────────────────────────────────────────────────────────────┘
```

Pane visibility is held in `<mahjong-app>` so it survives route transitions; toggling a pane re-runs CSS Grid template auto-fitting. Each pane is *independent* — closing the chat pane does not disconnect the WebSocket, does not stop receiving chat events (when wire support exists), and does not affect the game pane.

**v1 (7.5a walking skeleton) ships only the game pane.** The pane-toggle shell and other pane stubs are deferred to 7.5b. Reason: smallest verifiable artifact first.

#### `<game-pane>` — player variant

```text
 ╔═══════════════════════════════════════════════════════════════════════════╗
 ║                              Bob (North) [3F]                              ║
 ║                            🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇                              ║
 ║                                                                            ║
 ║  Carol (West)               ┌─Discard pool─┐               Dave (East)     ║
 ║  [4F]                       │ 🀇🀈 🀙 🀂 🀞   │               [2F]            ║
 ║  🀇🀇🀇🀇🀇🀇🀇                │ 🀚 🀇 🀘 🀛 🀚   │               🀇🀇🀇🀇🀇🀇🀇🀇    ║
 ║                             │ 🀜 🀉 🀊 🀋 🀌   │                              ║
 ║                             └─────────────┘                                ║
 ║                                                                            ║
 ║                            Alice (South) — you                             ║
 ║   Concealed: [🀇  W2  W2  W3  B4  B5  B6  T1  T1  T1  T9  F1  F2 ]          ║
 ║   Melds:     [ PENG B5 from East ]                                         ║
 ║                                                                            ║
 ║   Your turn — what do you do?                                              ║
 ║   [ 1-13 ] Discard tile     [ G ] Gang concealed F1                        ║
 ║   [ H ] Hu (self-draw 🀚)    [ P ] Pass                                     ║
 ║                                                                            ║
 ║   Time: 23s                                                                ║
 ╚═══════════════════════════════════════════════════════════════════════════╝
```

Layout regions:

- **Three opponent rows** (top, left, right): seat name + flower count + concealed-tile count (rendered as face-down tiles) + meld bar.
- **Center discard pool**: every discard in turn order, with the most-recent highlighted.
- **Own seat row** (bottom): full concealed hand + melds + active prompt.
- **Prompt bar**: rendered only when a `PROMPT` is outstanding. Lists legal actions with key bindings.
- **Status footer**: prompt countdown + connection status.

State held in `<game-pane>` reactive properties:

- `seatView: SeatView` — initial from `ATTACHED.snapshot`, mutated by applying each inbound `EVENT` projected to this seat.
- `currentPrompt: Prompt | null` — the outstanding `PROMPT`, if any.
- `selectedTile: number | null` — cursor position for tile selection.

Inputs (default bindings, configurable later):

- Number keys `1`–`9`, `0`, `-`, `=` to select a tile from the concealed hand (13 tiles max; the keys span enough).
- `Enter` to confirm the selected tile as a `PLAY` action.
- Letter keys for special actions: `G` (Gang), `P` (Pass / Peng — disambiguated by phase), `C` (Chi), `H` (Hu), `B` (Bugang). The prompt bar shows which are legal *now*; pressing an illegal key is a no-op (no client-side legality check beyond "is this in `prompt.legal_actions`").

#### `<game-pane>` — spectator variant

When the table-page role is `spectator`, the same `<game-pane>` element renders a different template:

```text
 ╔═══════════════════════════════════════════════════════════════════════════╗
 ║   Spectating (3 watching, including you)                                   ║
 ║                                                                            ║
 ║                              Bob (North) [3F]                              ║
 ║                            🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇                              ║
 ║                                                                            ║
 ║  Carol (West)               ┌─Discard pool─┐               Dave (East)     ║
 ║  [4F]                       │ 🀇🀈 🀙 🀂 🀞   │               [2F]            ║
 ║  🀇🀇🀇🀇🀇🀇🀇                │ 🀚 🀇 🀘 🀛 🀚   │               🀇🀇🀇🀇🀇🀇🀇🀇    ║
 ║                             └─────────────┘                                ║
 ║                                                                            ║
 ║                            Alice (South) [3F]                              ║
 ║                            🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇🀇 ◀ last to act              ║
 ║                                                                            ║
 ║   [ S ] Stop watching    [ J ] Try to join (if seat opens)                 ║
 ╚═══════════════════════════════════════════════════════════════════════════╝
```

Layout differences from the player variant:

- **All four seats render as opponents.** No "own row" — every concealed hand is face-down.
- **No prompt bar.** Spectators never receive `PROMPT`.
- **No tile selection.** No keys map to actions.
- **"Last to act" indicator** floats next to whichever seat the engine is currently waiting on, derived from the most recent `EVENT`.
- **`S` to stop spectating** (sends `STOP_SPECTATING`, `route = 'lobby'`).
- **`J` to attempt to join** when a seat opens — sends `ATTACH` to the first open seat; on success the pane transitions to player variant (state preserved, no reconnect).

State maintained:

- `publicView: PublicView` — initial from `SPECTATING.snapshot`, mutated by applying inbound public-projected `EVENT`s.
- `currentActingSeat: number | null` — derived for the "last to act" indicator.

**Privacy defense in depth:** the renderer takes a `PublicView`, not a `SeatView`. Even if the wire codec had a bug and sent a player-path event to a spectator, the spectator-variant template *only* reads `PublicView` fields and `seatView` is never assigned. The `EVENT`-application code is shared between player and spectator paths, but the spectator path runs through a public-projection assert *again* before mutating state. The render template references no field that a `PublicView` would not have.

#### `<chat-pane>`, `<stats-pane>`, `<spectator-pane>` — deferred

Stubs only in 7.5a. Documented here for the vision; not implemented yet.

- **`<chat-pane>`** — players talk to each other at the table. **Wire-protocol gap:** [wire-protocol.md](wire-protocol.md) currently defines no CHAT frames. Implementing this pane requires a wire amendment (CHAT_SEND inbound, CHAT_RECV outbound, scoped per-table). Toggle key: `C`. Render: scrolling list of `[hh:mm] alice: text` lines + an input field at bottom.
- **`<stats-pane>`** — per-hand and per-game stats (deals played, win rate, average fan per win, etc). Two layers: client-side aggregates (derivable from observed events) and server-side aggregates (cross-game, persisted — Layer 8 / SQLite). v1-of-pane ships only client-side aggregates. Toggle key: `S`.
- **`<spectator-pane>`** — watch *another* table while playing your own. Implementation note: the simplest path is **a second WebSocket connection** (the manager exposes a `subscribe(table_id)` that opens an auxiliary socket), avoiding a wire amendment for multi-subscription on one socket. Toggle key: `Sp` (or a longer keybinding to avoid `S` collision).

### `<hand-end-modal>`

A `dialog`-element overlay on top of `<table-page>` when `HAND_END` arrives. Shows:

- Terminal kind (HU / Exhaustive draw / Aborted).
- Winner + loser (HU only).
- Fan list with totals.
- "Continue" button → if `next_hand_seq != null`, dismisses and waits for next-hand `ATTACHED`; otherwise sets `route = 'lobby'`.

The underlying `<table-page>` stays mounted (CSS `inert` on the background); when the user dismisses, the page wakes up to receive the next hand's `ATTACHED`.

## Rendering pipeline

```text
   inbound wire message
            │
            ▼
   ConnectionManager.recv → CustomEvent('message', msg) ─┐
                                                          │ kind == "EVENT"
                                                          ▼
                                              <game-pane>.onEvent(event)
                                                          │
                                                          ▼
                              applyEventToLocalState(event, seatView)
                                                          │
                                                          ▼
                                  Lit reactive property assignment
                                                          │
                                                          ▼
                                          Lit re-renders template
```

State is held in Lit `@property`-decorated reactive attributes on each pane; assignment triggers a re-render of the affected widgets. Lit handles diffing inside the template; we never call `requestUpdate` manually unless we mutate a property in place (which we avoid — assign new objects).

The `applyEventToLocalState` function is the renderer-side equivalent of the engine's `apply_action`/diff loop. It is *not* the same code as the server's apply_action: the server applies actions to canonical state; the client applies events to projected state. The two are structurally analogous but operate on different inputs.

**Why apply events client-side instead of asking for a fresh snapshot per turn:** bandwidth and latency. A full snapshot per event is ~5 KB times ~200 events per hand = 1 MB. Event-application is ~100 bytes per event. The client gets one snapshot per attach + small deltas thereafter.

## Bilingual rendering

Every user-visible string passes through a `t(key, locale)` lookup against `mahjong/web/static/locales/{en,zh,bilingual}.json` (loaded once at app start). The `bilingual` locale concatenates the two with a separator (`"5 Bamboo / 五条"`). v1 ships:

- All action names (PLAY, PASS, PENG, CHI, GANG, BUGANG, HU).
- All tile names (1–9 of each suit, winds, dragons, flowers).
- Screen titles, button labels, error messages.

Tile glyphs (the `🀇` characters) are Unicode "mahjong tiles" code points (U+1F000..U+1F02B). Browsers with system mahjong fonts handle these. The fallback rendering — ASCII shorthand like `[W3]`, `[B5]` — is selectable via `?tile_style=ascii` URL param for environments where glyph rendering breaks.

## Error and disconnect UX

- **Wire `ERROR` received:** the active page displays a transient banner. `code` is shown to the user as a localised string; `message` is shown verbatim (since it's diagnostic, not localised).
- **`illegal_action` error:** the prompt stays outstanding; the banner shows "Server rejected that action — try again". This is a client bug if it happens; logged to the browser console at WARN.
- **`auth_failed` error:** `route = 'login'` with "Session expired — please log in again".
- **WebSocket drops:** banner appears at top of current page ("Reconnecting…"); `ConnectionManager` attempts `RESUME`. On success the banner clears; on failure ("Disconnected") a retry button is shown.
- **Render error:** the affected component is replaced with a placeholder (`[render error — press R to retry]`) via a Lit error boundary pattern (a wrapping `<error-boundary>` element catches exceptions from `render()` calls in children). The rest of the page continues to function.

## Configuration

Loaded at app start, in priority order:

1. URL query params (`?server=ws://...&locale=zh`).
2. `localStorage` (`mahjong.server_url`, `mahjong.locale`).
3. Built-in defaults.

Keys:

| Key | Default | Meaning |
| --- | --- | --- |
| `server` | (same origin as the page, `ws://` derived) | Where to connect. |
| `locale` | `bilingual` | `en` / `zh` / `bilingual`. |
| `tile_style` | `unicode` | `unicode` (🀇) / `ascii` ([W3]). |
| `no_auth` | `false` | S2 local-dev: skip login, hardcoded user. |

## Server-side static asset serving

`mahjong/wire/server.py` extends `_process_request` to handle two new path categories alongside the existing `/health`:

- `GET /` → serves `mahjong/web/static/index.html`.
- `GET /static/<path>` → serves `mahjong/web/static/<path>` if the path resolves inside `static/` (path traversal defense via `Path.resolve().is_relative_to`).
- Any other unrecognized HTTP path → 404.

Content-Type detection: a small map (`html` → `text/html; charset=utf-8`, `js` → `text/javascript`, `css` → `text/css`, `json` → `application/json`). Unknown extensions default to `application/octet-stream`.

The WebSocket subprotocol enforcement is unchanged: a WS upgrade attempt without `mahjong-v1` still returns 400; the subprotocol gate runs *after* the static-route checks.

**Why bundle static serving into the WebSocket process:** for the home-server deployment target (single binary on a RPi 5 / mini PC behind Tailscale), introducing a second process or a reverse proxy is overhead that doesn't earn anything in v1. If load ever justifies it, `nginx` in front is a 30-minute config change.

## Headless testability

Every page and pane has a Playwright-driven test. The pattern:

```python
async def test_login_then_lobby(playwright, fake_wire_server):
    fake_wire_server.script([
        ("recv", {"kind": "HELLO", "protocol_version": 1}),
        ("send", {"kind": "HELLO", ...}),
        ("recv", {"kind": "AUTH_REQUEST", "username": "alice", "password": "pw"}),
        ("send", {"kind": "AUTH_RESPONSE", "ok": True, ...}),
    ])
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    await page.goto(fake_wire_server.url())
    await page.fill("input[name=username]", "alice")
    await page.fill("input[name=password]", "pw")
    await page.click("text=Log in")
    await expect(page.locator("mahjong-app")).to_have_attribute("route", "lobby")
```

The `fake_wire_server` is a test helper that simulates the server side of the wire over a real local WebSocket. It's deterministic (scripted; no real engine) and produces a record of what the client sent so tests can assert wire-level behavior. The same helper backs the wire-protocol fixture tests.

Visual regression — capturing the rendered DOM and asserting it byte-equals a checked-in fixture — runs on representative states (login empty, lobby with 3 tables, table mid-hand with prompt outstanding, hand-end modal). Playwright's `page.locator(...).inner_text()` exposes the rendered text content for byte-equality assertions. We do not check screenshots (PNG comparison is flaky across fonts and DPI); we check rendered ASCII text.

## Alternatives considered

- **Textual / a terminal TUI.** Was the original v1 choice (the prior draft of this spec targeted it). Rejected on 2026-05-23: install friction for target users is the bigger cost than running a separate static-serving HTTP route. Reach beats elegance for the home-hosted use case.
- **A bundler (Vite / esbuild / Rollup) + npm dependency tree.** Rejected for v1. Lit + native ES modules works without a build step. Add a bundler when asset count or browser-compat justifies it.
- **A framework router (`react-router`-style).** Rejected. Three routes (login, lobby, table). A property switch on the root element is enough.
- **A separate web-server process (FastAPI / Starlette) for static assets.** Rejected. The home-server deployment target wants one process. Extending the existing `_process_request` hook is a 30-line addition.
- **Server-side rendering / Jinja templates.** Rejected. The client renders state from `EVENT`s arriving over WebSocket; server-rendered HTML would have to be re-synced after every event, defeating the purpose of having a wire protocol.
- **Free-floating draggable windows.** Rejected on 2026-05-23 in favor of fixed-layout toggleable panes. Movable windows would multiply test surface (resize, overlap, focus) without buying meaningful UX for a 4-pane app.
- **A native Python `urwid` or `prompt_toolkit` UI.** Same rejection as Textual — pivoting away from terminal-based clients entirely for v1.
- **One unified `<table-page>` with a `role` flag controlling whether to render the own-seat row, no separate spectator variant.** Tempting; rejected for the same reason `SPECTATE` is a separate message from `ATTACH`: spectator and player are structurally different (state type, input handling, prompt presence). The single component switching between variants keeps the URL/route the same (`route = 'table'`) but the *rendered template* branches cleanly between the two.
- **Animations.** Rejected for v1. State changes render immediately on `EVENT`.
- **Hot-reload of locale files.** Rejected: rare changes, page reload is fine.
- **HTML5 history API / deep linking.** Rejected for v1. Single-process, single-user-session.

## Verification fixtures

Acceptance criteria for impl step 7.5 (web client). 7.5a (walking skeleton) only requires fixtures 1, 5, 6 and a smaller end-to-end fixture (a static page loads, opens a WS, renders one snapshot). Remaining fixtures gate 7.5b/7.5c/7.6.

1. **Static asset serving.** `GET /` returns the HTML page (200, `text/html`). `GET /static/app.js` returns the JS (200, `text/javascript`). `GET /static/../../etc/passwd` returns 404 (path traversal blocked).
2. **Login → Lobby happy path.** Playwright script types credentials, clicks Log in, asserts `AUTH_REQUEST` sent, asserts `route="lobby"` after fake `AUTH_RESPONSE { ok: true }`.
3. **Login failure.** Playwright: assert page stays at `route="login"` after `AUTH_RESPONSE { ok: false }`; banner contains "Login failed"; no token stored in `localStorage`.
4. **Lobby → join open seat.** Playwright: select table with open seat, press `J`, assert `ATTACH { table_id, seat: <first open> }` sent.
5. **`<game-pane>` (player) renders snapshot.** Given a checked-in `ATTACHED.snapshot` fixture (state-schema.md fixture 2), the rendered text content byte-equals a checked-in `.txt` fixture.
6. **Apply EVENT mutates local state correctly.** Parameterised JS unit test (run via Playwright or a pure-Node harness): starting `SeatView` + each `EVENT` kind → expected resulting `SeatView`.
7. **PROMPT renders legal action bar.** Given a `PROMPT` fixture with 3 legal actions, the prompt bar lists exactly those 3 with their key bindings.
8. **Action submission round-trip.** Playwright: prompt outstanding, press key for legal action, assert `ACTION` sent with correct `prompt_id` and action payload.
9. **Illegal action displayed but doesn't close.** Playwright: prompt outstanding, server replies `ERROR { code: "illegal_action" }`, assert banner shown, page still at `route="table"`, prompt still rendered.
10. **WebSocket drop shows banner.** Playwright: drop the fake socket mid-hand, assert "Reconnecting…" banner appears within 100ms.
11. **Resume after drop replays buffered events.** Playwright: drop, fake reconnect with `RESUME` + buffered events, assert local state reflects all replayed events in order.
12. **`<game-pane>` (spectator) never renders concealed tiles.** Given fixture `EVENT`s including own-draw payloads (which a spectator should *not* receive), the renderer asserts that all four seats' concealed regions render as face-down tiles only. (Defense in depth: even if a buggy server sent the field, the renderer would refuse to display it.)
13. **Spectator stop subscribing.** Playwright: press `S`, assert `STOP_SPECTATING` sent, assert `route="lobby"` on `DETACHED` ack.
14. **`<hand-end-modal>` shows fan list.** Given a `HAND_END` fixture, the modal renders the terminal block, fan list, and total in both EN and ZH labels.
15. **Bilingual rendering.** Snapshot test for the same page under `en`, `zh`, `bilingual` locales — three checked-in text fixtures, all byte-stable.
16. **Render error placeholder doesn't crash app.** Force a render exception in a Lit component; assert the error boundary renders the placeholder and the rest of the page remains interactive.
17. **Pane toggle.** Playwright: in `<table-page>`, press `C` to toggle the chat pane on, assert `<chat-pane>` mounts. Press `C` again, assert it unmounts. Repeat for stats and spectator panes.
18. **End-to-end scripted hand.** The S2 exit fixture from server-plan.md §S2: a Playwright script drives one browser instance against a real server (other three seats are `CannedAdapter`s); the resulting record file replays byte-identically. (Same fixture that wire-protocol.md fixture 16 pins on the server side.)

Fixture 18 is the load-bearing one for the S2 exit gate.

## Open questions

- **Single source of HTML vs. component HTML-only files.** Currently planned: one `index.html` shell, components register themselves via JS imports. If a component's template grows past ~200 lines we may extract it to a separate `.html` template — decide when it happens.
- **TLS / Tailscale interaction with WebSocket.** `wss://` over Tailscale should "just work" but needs a verification fixture once we wire the deployment topology (Step 8.x). Not blocking 7.5.
