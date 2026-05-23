# Spec 12 — TUI client architecture

The Textual-based ASCII client that humans use to log in, browse tables, play hands, and spectate. This is the *only* consumer of [wire-protocol.md](wire-protocol.md) we ship in v1; any other client (web, mobile) is future work and lives in its own spec when it happens.

This is a tier-2 spec — single consumer, lower blast radius if wrong. The interface that *would* be high-blast-radius (the wire) is already pinned in [wire-protocol.md](wire-protocol.md); this spec covers how the TUI translates the wire into pixels-on-a-terminal and keystrokes back onto the wire.

Builds on [wire-protocol.md](wire-protocol.md) (the only network surface), [state-schema.md](state-schema.md) (the shape of `SeatView` and `PublicView` the client renders), and [seat-port.md](seat-port.md) (only conceptually — the TUI ↔ server flow mirrors the seat-port's `observe` / `decide` cycle, but the TUI doesn't import any seat-port code).

**Status:** draft, pre-S2 implementation. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md).

## Goals

- **Plain mode in v1.** No analysis overlays, no shanten hint, no opponent-hand forecaster. Just clear rendering of the public table state and the player's own hand. Overlays are S4; the TUI's *renderer* must be structured so an overlay is a pluggable layer, but no overlay code ships in v1.
- **Spectator screen is first-class.** Per [s2-s3-plan.md](../s2-s3-plan.md), spectating is heavy early-stage usage. The TUI must expose a "watch a table" flow from the lobby, render the public projection without leaking concealed-information *even if the wire accidentally sends some* (defense in depth at the rendering layer), and let the spectator switch tables or upgrade to a player without quitting.
- **One Textual app, multiple screens.** Login, lobby (table list), player table, spectator table, hand-end result. Navigation between screens is a state transition, not a process restart. The wire connection persists across screen transitions.
- **Headless-testable.** Every screen is exercised in CI via Textual's [`Pilot`](https://textual.textualize.io/api/pilot/) driver. No real terminal needed; no manual screenshot review in the verification ladder.
- **Bilingual labels (EN/ZH).** Tile names, action names, screen labels carry both forms (e.g. "5 Bamboo / 五条"). The display layer takes a `Locale` setting that picks which side renders prominently; the other appears as a parenthetical or tooltip-equivalent. v1 ships both languages always-rendered; future work can hide one per user preference.
- **Crash-resistant.** A bug in a rendering path must not lose the wire connection or the user's seat. Rendering errors are caught at the screen boundary, logged, and replaced with a "render error" placeholder that the user can dismiss.

## Non-goals

- **Not the wire protocol.** All messages, framing, error codes, and reconnect semantics are [wire-protocol.md](wire-protocol.md)'s. This spec consumes them.
- **Not a generic mahjong UI toolkit.** Tile rendering, meld layout, and discard-pile rendering are coded for MCR; supporting other rule sets means adding rendering branches when that work ships (S5+).
- **Not animations.** v1 renders state changes as immediate re-renders. No tile-flying animations, no draw animations. A discard appears in the pile the moment the `EVENT` arrives.
- **Not voice / sound.** Pure text.
- **Not configuration UI.** Settings (locale, key bindings, server address) live in environment variables and a small TOML config file in v1. A settings screen comes later if real users want it.
- **Not analysis overlays.** S4. The renderer must be *structured* to admit overlays additively; no overlay code ships here.

## Tech stack

- **[Textual](https://textual.textualize.io/) 0.50+.** Python TUI framework, async-native (matches the server's asyncio loop), full Unicode/CJK support (needed for tile names and Chinese labels), built-in `Pilot` driver for headless testing.
- **`websockets`.** Same library as the server. Single `WebSocketClientProtocol` per running TUI, owned by the app's root.
- **`textual.app.App` + multiple `Screen`s.** Textual's idiomatic structure.
- **No HTTP requests.** The TUI talks only WebSocket. Anything that looks like an HTTP request (health check, version probe) is a wire-protocol message instead.

The Textual choice is pinned in [server-plan.md § Tech stack](../server-plan.md). The alternative (a hand-rolled curses app) was rejected there for maintenance cost.

## App architecture

```text
                ┌───────────────────────────────────────────────┐
                │                  MahjongApp                    │
                │  (single asyncio loop; owns WebSocket client)  │
                └──────────────────────┬────────────────────────┘
                                       │
              ┌────────────────────────┼─────────────────────────┐
              │                        │                          │
              v                        v                          v
       ┌────────────┐          ┌────────────┐             ┌──────────────┐
       │ LoginScreen│          │LobbyScreen │             │TableScreen   │  ◄── two variants:
       └────────────┘          └────────────┘             │              │      PlayerView,
                                                          │              │      SpectatorView
                                                          └──────────────┘

                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │     ConnectionManager          │   <-- wraps websockets client,
                       │  (one per MahjongApp instance) │       exposes async send/recv,
                       │                                │       reconnects with token,
                       │                                │       routes inbound by `kind`
                       └───────────────────────────────┘
                                       │
                                       ▼
                              wire-protocol.md
```

### `MahjongApp`

The Textual `App` subclass. Owns:

- The `ConnectionManager` instance (singleton per app).
- The currently-mounted `Screen`.
- The local user identity (`user_id`, `display_name`, `session_token`) after login.
- The locale setting (`en` / `zh` / `bilingual`).

Lifecycle:

1. App starts → load config (`MAHJONG_SERVER_URL`, locale, key bindings) from env + `~/.config/mahjong/tui.toml`.
2. App tries to connect → `ConnectionManager.connect()`. On success the WebSocket is up; HELLO has been exchanged; we are at the auth phase.
3. App attempts `RESUME` with a stored token (if any). On success → push `LobbyScreen`. On failure → push `LoginScreen`.
4. From the Lobby, the user picks a table → push `TableScreen(role=Player|Spectator, table_id=...)`.
5. Hand end → `HandEndModal` (overlay screen) → either return to `TableScreen` for next hand or pop to Lobby.
6. App quits → `ConnectionManager.close()` → WebSocket clean close (1000).

### `ConnectionManager`

A thin wrapper around `websockets.client.connect`. Not a Textual concept; pure asyncio. Exposes:

```python
class ConnectionManager:
    async def connect(self, url: str) -> None: ...
    async def send(self, message: dict) -> None: ...
    async def recv(self) -> dict: ...     # parsed, validated against the catalog in wire-protocol.md
    async def close(self, code: int = 1000) -> None: ...

    on_message: Callback[[dict], None]    # for screens to subscribe to inbound
    on_disconnect: Callback[[], None]     # for screens to react to drops
    on_reconnect: Callback[[], None]      # after a successful RESUME
```

Inbound messages are dispatched to the currently-mounted screen via `on_message`. Each screen filters by `kind`: `LobbyScreen` reacts to `TABLE_LIST`; `TableScreen` reacts to `EVENT`/`PROMPT`/`HAND_END`/`DETACH`; the app's root reacts to `ERROR { code: "auth_failed" }` etc.

Reconnect policy is *manager-internal* — on WebSocket drop the manager attempts a single `RESUME` after a 1s backoff. If it fails (token expired, network still down), the manager surfaces `on_disconnect`; screens display a banner. The TUI does not aggressively spam-reconnect; one attempt then user-prompted retry.

## Screens

### `LoginScreen`

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

Submits `AUTH_REQUEST` with `username` + `password`. On `AUTH_RESPONSE { ok: true }` stores the `session_token` in OS keyring (via `keyring` library) for resume-on-restart, then pushes `LobbyScreen`. On `ok: false` displays a generic "Login failed" message — no distinction between unknown-user and wrong-password (per [auth.md](auth.md) failure-shape requirement).

Skip-able if `--no-auth` config flag is set (S2 local dev mode); the app jumps straight to Lobby with a hard-coded user.

### `LobbyScreen`

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

Spectator counts (`◐ 3 watching`) are populated from `TABLE_LIST.tables[].spectator_count` (an optional field; if absent, omit the indicator).

### `TableScreen`

The main game view, with two render variants:

#### `TableScreen.PlayerView`

```text
 ╔═══════════════════════════════════════════════════════════════════════════╗
 ║   Table #17    MCR-2006    Hand 1/N    Wind: East    Wall: 38 left         ║
 ║                                                                            ║
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

- **Status bar** (top): table id, ruleset, hand index, prevailing wind, wall remaining.
- **Three opponent rows** (top, left, right): seat name + flower count + concealed-tile count (rendered as face-down tiles) + meld bar.
- **Center discard pool**: every discard in turn order, with the most-recent highlighted.
- **Own seat row** (bottom): full concealed hand + melds + active prompt.
- **Prompt bar**: rendered only when a `PROMPT` is outstanding. Lists legal actions with key bindings.
- **Status footer**: prompt countdown + connection status.

State maintained:

- `SeatView` — initial from `ATTACHED.snapshot`, mutated by applying each inbound `EVENT` projected to this seat. This is the same mutation the engine performs server-side, but client-only — the server's `EVENT`s are authoritative.
- `current_prompt: Prompt | None` — the outstanding `PROMPT`, if any.
- `selected_tile: TileIndex | None` — cursor position for tile selection.

Inputs (default bindings, configurable):

- Number keys `1`–`9`, `0`, `-`, `=` to select a tile from the concealed hand. (13 tiles max; the keys span enough.)
- `Enter` to confirm the selected tile as a `PLAY` action.
- Letter keys for special actions: `G` (Gang), `P` (Pass / Peng — disambiguated by phase), `C` (Chi), `H` (Hu), `B` (Bugang). The prompt bar shows which are legal *now*; pressing an illegal key is a no-op (no client-side legality check beyond "is this in `prompt.legal_actions`").

#### `TableScreen.SpectatorView`

```text
 ╔═══════════════════════════════════════════════════════════════════════════╗
 ║   Table #17    MCR-2006    Hand 1/N    Wind: East    Wall: 38 left         ║
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

Layout differences from `PlayerView`:

- **All four seats render as opponents.** No "own row" — every concealed hand is face-down.
- **No prompt bar.** Spectators never receive `PROMPT`.
- **No tile selection.** No keys map to actions.
- **"Last to act" indicator** floats next to whichever seat the engine is currently waiting on, derived from the most recent `EVENT`.
- **`S` to stop spectating** (sends `STOP_SPECTATING`, returns to Lobby).
- **`J` to attempt to join** when a seat opens — sends `ATTACH` to the first open seat; on success the view transitions to `PlayerView` (state preserved, no reconnect).

State maintained:

- `PublicView` — initial from `SPECTATING.snapshot`, mutated by applying inbound public-projected `EVENT`s.
- `current_acting_seat: int | None` — derived for the "last to act" indicator.

**Privacy defense in depth:** the renderer takes a `PublicView`, not a `SeatView`. Even if the wire codec had a bug and sent a player-path event to a spectator, the spectator's renderer is *typed* to accept only `PublicView` and the spurious fields would either fail validation or be dropped. The `EVENT`-application code is shared between player and spectator paths, but the spectator path runs through a public-projection assert *again* before mutating state.

### `HandEndModal`

Pops over the `TableScreen` on `HAND_END`. Shows:

- Terminal kind (HU / Exhaustive draw / Aborted).
- Winner + loser (HU only).
- Fan list with totals.
- "Continue" button → if `next_hand_seq != null`, dismisses and waits for next-hand `ATTACHED`; otherwise pops back to Lobby.

The modal is a Textual `ModalScreen`; the underlying `TableScreen` stays mounted (transparent freeze). When the user dismisses, the table screen wakes up to receive the next hand's `ATTACHED`.

## Rendering pipeline

```text
   inbound wire message
            │
            ▼
   ConnectionManager.recv  ─┐
                            │ kind == "EVENT"
                            ▼
                  screen.on_event(event)
                            │
                            ▼
            project_event_to_local_state(event, state)
                            │
                            ▼
                     screen.refresh()
                            │
                            ▼
                  Textual reactive re-render
```

State is held in Textual `reactive` attributes on each screen; changes trigger a re-render of the affected widgets. There is no manual "redraw" call site — Textual handles it.

The `project_event_to_local_state` function is the renderer-side equivalent of the engine's `apply_action`/diff loop. It is *not* the same code as the server's apply_action: the server applies actions to canonical state; the client applies events to projected state. The two are structurally analogous but operate on different inputs.

**Why apply events client-side instead of asking for a fresh snapshot per turn:** bandwidth and latency. A full snapshot per event is ~5 KB times ~200 events per hand = 1 MB. Event-application is ~100 bytes per event. The client gets one snapshot per attach + small deltas thereafter.

## Bilingual rendering

Every user-visible string passes through a `t(key: str, locale: Locale) -> str` lookup against `mahjong/cli/tui/locales/{en,zh,bilingual}.toml`. The `bilingual` locale concatenates the two with a separator (`"5 Bamboo / 五条"`). v1 ships:

- All action names (PLAY, PASS, PENG, CHI, GANG, BUGANG, HU).
- All tile names (1–9 of each suit, winds, dragons, flowers).
- Screen titles, button labels, error messages.

Tile glyphs (the `🀇` characters) are Unicode "mahjong tiles" code points (U+1F000..U+1F02B). All terminals that handle CJK handle these. The fallback rendering — ASCII shorthand like `[W3]`, `[B5]` — is selectable via config for environments where the glyph rendering breaks.

## Error and disconnect UX

- **Wire `ERROR` received:** the screen displays a transient banner. `code` is shown to the user as a localised string; `message` is shown verbatim (since it's diagnostic, not localised).
- **`illegal_action` error:** the prompt stays outstanding; the banner shows "Server rejected that action — try again". This is a TUI bug if it happens; logged at WARN.
- **`auth_failed` error:** the screen pops to `LoginScreen` with "Session expired — please log in again".
- **WebSocket drops:** banner appears at top of current screen ("Reconnecting…"); `ConnectionManager` attempts `RESUME`. On success the banner clears; on failure ("Disconnected") a retry button is shown.
- **Render error:** the affected widget is replaced with a placeholder (`[render error — press R to retry]`). The rest of the screen continues to function.

## Configuration

Loaded at app start, in priority order:

1. CLI args (`python -m mahjong.cli.tui --server ws://... --locale zh`).
2. Environment variables (`MAHJONG_SERVER_URL`, `MAHJONG_TUI_LOCALE`).
3. Config file (`~/.config/mahjong/tui.toml`).
4. Built-in defaults.

Keys:

| Key | Default | Meaning |
| --- | --- | --- |
| `server_url` | `ws://127.0.0.1:8400/socket` | Where to connect. |
| `locale` | `bilingual` | `en` / `zh` / `bilingual`. |
| `tile_style` | `unicode` | `unicode` (🀇) / `ascii` ([W3]). |
| `keymap` | `default` | Named keymap; remappable in the config file's `[keymap]` table. |
| `no_auth` | `false` | S2 local-dev: skip login, hardcoded user. |

## Headless testability

Every screen has a Pilot-driven test. The pattern (mirrors Textual's docs):

```python
async def test_login_then_lobby() -> None:
    fake_server = FakeWireServer(scripted=[
        ("recv", {"kind": "HELLO", "protocol_version": 1}),
        ("send", {"kind": "HELLO", ...}),
        ("recv", {"kind": "AUTH_REQUEST", "username": "alice", "password": "pw"}),
        ("send", {"kind": "AUTH_RESPONSE", "ok": True, ...}),
    ])
    app = MahjongApp(connection=fake_server.client())
    async with app.run_test() as pilot:
        await pilot.press("a", "l", "i", "c", "e", "tab", "p", "w", "enter")
        assert isinstance(app.screen, LobbyScreen)
```

The `FakeWireServer` is a test helper that simulates the server side of the wire. It's deterministic (no real socket, no real timing) and produces a record of what the TUI sent so tests can assert wire-level behavior. The same helper is used in the wire-protocol fixture tests.

A "snapshot test" — capturing the rendered screen content and asserting it byte-equals a checked-in fixture — runs on representative states (login screen empty, lobby with 3 tables, table mid-hand with prompt outstanding, hand-end modal). Textual exposes `app.export_screenshot()` for this.

## Alternatives considered

- **A web client instead of TUI for v1.** Lower friction for non-technical friends, broader accessibility. Rejected for v1: web clients add HTTP, asset serving, browser-compat concerns, and a build pipeline — none of which are needed when Textual ships a fully-functional TUI today. A web client is additive in S2+ when the wire-protocol is locked.
- **A native Python `urwid` or `prompt_toolkit` UI instead of Textual.** Both work. Rejected: Textual has the modernest dev experience, the best documentation, async-native (matches our asyncio loop), and ships Pilot for headless testing — none of which is true of the others without significant glue.
- **One unified `TableScreen` with a `role` flag controlling whether to render the own-seat row.** Tempting; rejected for the same reason `SPECTATE` is a separate message from `ATTACH`: spectator and player are structurally different (state type, input handling, prompt presence). One screen with branching everywhere is harder to test than two screens sharing rendering helpers.
- **Animations (tile-flying discards, deal animation).** Rejected for v1. Animations require a smarter rendering loop, double-buffering coordination, and timing tests. The "appears immediately on `EVENT`" rule is simpler and faster to render. Animations are a S4-ish polish item.
- **Hot-reload of locale files.** Rejected: locale changes are rare; restart-to-apply is fine.
- **Mouse support.** Textual supports it; we don't enable it in v1. Keyboard-only is faster for repeat play and easier to test deterministically.
- **A separate `CreateTableScreen` for `N` in the lobby.** Rejected in favor of a `ModalScreen` overlay; simpler nav.

## Verification fixtures

Acceptance criteria for impl step 7.5 (TUI client).

1. **Login → Lobby happy path.** Pilot script: type credentials, press Enter, assert `AUTH_REQUEST` sent, assert lobby appears after fake `AUTH_RESPONSE { ok: true }`.

2. **Login failure.** Pilot: assert `LoginScreen` stays mounted after `AUTH_RESPONSE { ok: false }`; banner contains "Login failed"; no token stored.

3. **Lobby → join open seat.** Pilot: arrow to a table with an open seat, press `J`, assert `ATTACH { table_id, seat: <first open> }` sent.

4. **Lobby → spectate.** Pilot: press `W` on a table, assert `SPECTATE { table_id }` sent, assert spectator screen mounts on `SPECTATING` response.

5. **PlayerView renders snapshot.** Given a checked-in `ATTACHED.snapshot` fixture (matching state-schema.md fixture 2), the rendered screen byte-equals a checked-in screenshot.

6. **Apply EVENT mutates local state correctly.** Parameterised test: starting `SeatView` + each `EVENT` kind → expected resulting `SeatView`. Mirrors the engine's apply_action tests but on projected state.

7. **PROMPT renders legal action bar.** Given a `PROMPT` fixture with 3 legal actions, the prompt bar lists exactly those 3 with their key bindings.

8. **Action submission round-trip.** Pilot: prompt outstanding, press key for legal action, assert `ACTION` sent with correct `prompt_id` and action payload.

9. **Illegal action displayed but doesn't close.** Pilot: prompt outstanding, server replies `ERROR { code: "illegal_action" }`, assert banner shown, screen still mounted, prompt still rendered.

10. **WebSocket drop shows banner.** Pilot: drop the fake socket mid-hand, assert "Reconnecting…" banner appears within 100ms.

11. **Resume after drop replays buffered events.** Pilot: drop, fake reconnect with `RESUME` + buffered events, assert local state reflects all replayed events in order.

12. **SpectatorView never renders concealed tiles.** Given fixture `EVENT`s including own-draw payloads (which a spectator should *not* receive), the renderer asserts that all four seats' concealed regions render as face-down tiles only. (Defense in depth: even if a buggy server sent the field, the renderer would refuse to display it.)

13. **Spectator stop subscribing.** Pilot: in spectator view, press `S`, assert `STOP_SPECTATING` sent, assert lobby screen mounts on `DETACHED` ack.

14. **HandEndModal shows fan list.** Given a `HAND_END` fixture, the modal renders the terminal block, fan list, and total in both EN and ZH labels.

15. **Bilingual rendering.** Snapshot test for the same screen under `en`, `zh`, `bilingual` locales — three checked-in screenshots, all byte-stable.

16. **Render error placeholder doesn't crash app.** Force a render exception in a widget; assert the widget renders the error placeholder and the rest of the screen remains interactive.

17. **End-to-end scripted hand.** The S2 exit fixture from server-plan.md §S2: a Pilot script drives four TUI instances against a single server; the resulting record file replays byte-identically. (This is the same fixture that wire-protocol.md fixture 16 pins on the server side.)

Fixture 17 is the load-bearing one for the S2 exit gate.

## Open questions

None at v1. The TUI is a single consumer of locked specs; design choices that ripple back into the wire (e.g. additional event metadata for animation) are deferred to when animations are designed (S4+).
