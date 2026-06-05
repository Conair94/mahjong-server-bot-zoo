# Spec 28 — Profile home page & client settings menu

Two independent UI features that ship together because they share one entry point (the lobby header chrome): a **client settings menu** (collect every toggleable client preference behind one discoverable surface) and a **profile home page** (per-account play stats, recent-game history, and a point-performance line graph over time).

Tier-2 spec. The settings menu is **client-only** — no wire or server change. The profile page adds **one aggregate query**, **one request/response pair** on the wire, and a new client view; it reads tables that the live game loop already populates (no schema migration, no change to the hot path).

Builds on:
- [sqlite-schema.md](sqlite-schema.md) — `hand_index` + `hand_participants` already record `final_score_delta` per seat, `winner_seat`, `fan_total`, `terminal_kind`, and `started_at_ms`/`ended_at_ms`. This spec only *reads* them.
- [persistence-api.md](persistence-api.md) — adds one read helper (`account_stats`) alongside the existing `find_hands_by_account`.
- [wire-protocol.md](wire-protocol.md) — adds `GET_PROFILE` / `PROFILE` kinds.
- [cardinal-ui.md](cardinal-ui.md) / the existing Lit client (`mahjong/web/static/app.js`) — adds `<settings-menu>`, `<profile-page>`, and a top-level `view` router state.

**Status:** draft, pre-implementation. Branch `feat/profile-and-settings`.

---

## Goals

- **One discoverable home for every client toggle.** Today theme (Alt+T), tile-style (Alt+U), and the three pane toggles (Alt+C/S/W) are keyboard chords with no menu. A settings overlay lists them all with their current value and hotkey. Existing chords keep working unchanged — the menu is a *surface over the same state*, not a replacement.
- **Settings are extensible by adding a row, not by editing layout.** A descriptor registry drives the menu. A future toggle = one new descriptor entry. The user has stated settings will only grow; the registry is the standard *preferences-registry* pattern that keeps the menu from becoming a hand-maintained list.
- **A real profile home page.** Reachable from the lobby, full-width (not cramped into an ASCII pane). Shows headline stats, a recent-games list, and a cumulative-points line graph.
- **Per-hand stat unit** (decided 2026-06-05). A "game" is a hand. Win rate = hands won / hands played; average win size = average score gained on hands you won; total standing = cumulative score delta. This matches both the data (one winner per hand) and current solo-vs-bot play. Matches are still *grouped* in history via `match_id` but are not the stat unit.
- **One round-trip for the home page.** `GET_PROFILE` → `PROFILE` returns stats + recent history + the graph series in a single message. No N+1, no per-row follow-up requests.
- **Stats are derived on read, never materialised.** No counters on `accounts`, no triggers. At friends-and-family scale a `GROUP BY account_id` over a few thousand rows is sub-millisecond. (Per [sqlite-schema.md § Alternatives](sqlite-schema.md): materialised stat columns are explicitly deferred until a query is demonstrably slow.)

## Non-goals

- **Not a global leaderboard / cross-account comparison.** This is *your* profile. A leaderboard is a separate future spec (it needs a different query and a privacy decision about whose stats are public).
- **Not editable profile fields beyond display name.** v1 surfaces `display_name` read-only (there is no rename UI yet — that's a small follow-up). No avatars, bios, or account settings (password change stays a server-side admin/DB operation per [auth.md](auth.md)).
- **Not full history pagination in v1.** `PROFILE.recent` returns the most recent N hands (default 20). Keyset pagination already exists in `find_hands_by_account`; wiring a "load more" request is deferred to a follow-up (open question below).
- **Not a settings *sync* feature.** Settings persist in `localStorage` (per-browser), exactly as theme/tile-style do today. No server-side preference storage.
- **Not match-level stats.** Per-match win/placement aggregates are out of scope for v1 (decided per-hand). Revisit if match play becomes the primary mode.
- **Not a change to the game loop or persistence write path.** Both already record everything needed. This spec is read-only against them.

---

## Part A — Client settings menu

### Surface

A modal overlay, centered, dismissible with `Esc` or a close button or a click on the backdrop. Opened by:
- a `[ ⚙ ]` button in the lobby header **and** the table header, and
- a hotkey (`Alt+,` — comma, the conventional "preferences" chord; does not collide with the existing letter chords).

The overlay is mounted at the `<mahjong-app>` level so it floats above whichever view (lobby / profile / table) is active. It is purely presentational: it reads current values and emits change events; it owns no settings state itself.

### Settings registry

A module `mahjong/web/static/settings.js` exports an ordered list of **setting descriptors**. The menu renders one row per descriptor. Adding a setting later means appending a descriptor — no layout edit.

```js
// settings.js
/**
 * @typedef {Object} SettingDescriptor
 * @property {string}   key       Stable id, also the localStorage suffix.
 * @property {string}   label     Display label, e.g. "Theme".
 * @property {"cycle"}  control   v1 has one control type: cycle-through-values.
 * @property {string[]} values    Ordered cycle, e.g. ["dark", "light"].
 * @property {string}   default   Default value (must be in `values`).
 * @property {string}   hotkey    Display string for the existing chord, e.g. "Alt+T".
 * @property {"global"|"table"} scope
 *           "global": persisted in localStorage, app-level (theme, tiles).
 *           "table":  live pane-visibility, only meaningful at a table; the
 *                     row is shown disabled in the lobby with a hint.
 */
export const SETTINGS = [
  { key: "theme",      label: "Theme",         control: "cycle", values: ["dark", "light"],  default: "dark",  hotkey: "Alt+T", scope: "global" },
  { key: "tile-style", label: "Tiles",         control: "cycle", values: ["ascii", "unicode"], default: "ascii", hotkey: "Alt+U", scope: "global" },
  { key: "pane-chat",      label: "Chat pane",      control: "cycle", values: ["off", "on"], default: "off", hotkey: "Alt+C", scope: "table" },
  { key: "pane-stats",     label: "Stats pane",     control: "cycle", values: ["off", "on"], default: "off", hotkey: "Alt+S", scope: "table" },
  { key: "pane-spectator", label: "Spectator pane", control: "cycle", values: ["off", "on"], default: "off", hotkey: "Alt+W", scope: "table" },
];

// localStorage helpers, wrapped so private-mode throws are non-fatal
// (same defensive pattern as the existing loadInitialTheme()).
export function loadSetting(key, fallback) { /* try/catch getItem */ }
export function saveSetting(key, value)   { /* try/catch setItem */ }
```

The two existing `global` settings keep their current localStorage keys for back-compat (`mahjong-theme`, `mahjong-tile-style`); the descriptor `key` maps to those via a small alias table rather than forcing a migration. (Alternative — rename keys to `mahjong-setting-theme` etc. with a one-time migration: rejected as churn for no user benefit; the alias is two lines.)

### Wiring

- **`global` rows** read/write through `<mahjong-app>`'s existing `theme` / `tileStyle` state (which already persists to localStorage and already has the `_toggleTheme` / `_toggleTileStyle` handlers bound to the chords). The menu's cycle control calls the *same* toggle handlers, so chord and menu are guaranteed consistent.
- **`table` rows** dispatch a `pane-toggle` CustomEvent (the existing pane-toggle path) up to `<table-page>`. When no table is active the rows render disabled with a `(at a table)` hint, because pane visibility is live per-table state, not a persisted preference.

This split keeps pane state where it already lives (per-table) while still presenting all toggles in one menu. Lifting pane state into a global persisted store is a deliberate non-goal for v1 — it would change pane semantics (currently you open Stats at one table without it following you to the next) for a cosmetic consistency win.

### Settings menu — verification

The settings menu is **client cosmetics** per [CLAUDE.md § TDD buckets](../../CLAUDE.md) → covered by a real-browser Playwright check, not unit-tested in isolation:

- S-A1. Open the menu (button + `Alt+,`); assert all five rows render with current values and hotkey labels.
- S-A2. Cycle Theme in the menu; assert `document.documentElement.dataset.theme` flips **and** the chord (`Alt+T`) still flips it (shared handler — no divergence).
- S-A3. In the lobby, the three `table`-scoped rows are disabled with the hint; at a table they cycle and the corresponding pane appears/disappears.
- S-A4. `Esc` and backdrop-click both close the menu.

---

## Part B — Profile home page

### B.1 Persistence query — `account_stats`

New read helper in `mahjong/persistence/hands.py`, surfaced on the `Persistence` facade. Returns a frozen `AccountStats` dataclass. **One** SQL statement, aggregating finalized hands the account participated in.

```python
@dataclasses.dataclass(frozen=True)
class AccountStats:
    account_id: int
    hands_played: int          # finalized hands the account sat in
    hands_won: int             # winner_seat == this account's seat
    draws: int                 # terminal_kind == 'EXHAUSTIVE_DRAW'
    total_score: int           # SUM(final_score_delta) — total point standing
    total_win_points: int      # SUM(final_score_delta) over won hands
    best_win_fan: int | None   # MAX(fan_total) over won hands
    first_played_ms: int | None
    last_played_ms: int | None
```

```sql
SELECT
  COUNT(*)                                                              AS hands_played,
  COALESCE(SUM(hp.final_score_delta), 0)                               AS total_score,
  COALESCE(SUM(hi.winner_seat = hp.seat), 0)                           AS hands_won,
  COALESCE(SUM(hi.terminal_kind = 'EXHAUSTIVE_DRAW'), 0)               AS draws,
  COALESCE(SUM(CASE WHEN hi.winner_seat = hp.seat
                    THEN hp.final_score_delta ELSE 0 END), 0)          AS total_win_points,
  MAX(CASE WHEN hi.winner_seat = hp.seat THEN hi.fan_total END)        AS best_win_fan,
  MIN(hi.started_at_ms)                                                AS first_played_ms,
  MAX(hi.started_at_ms)                                                AS last_played_ms
FROM hand_participants hp
JOIN hand_index hi ON hi.hand_id = hp.hand_id
WHERE hp.account_id = ?
  AND hi.ended_at_ms IS NOT NULL          -- finalized only
  AND hp.final_score_delta IS NOT NULL
  AND hi.source = 'live';                 -- exclude selfplay/replay-import
```

Notes:
- `source = 'live'` filter: a human account only ever appears in live hands today (selfplay uses bot/no accounts), but the filter makes the contract explicit — the profile is "your real games," not training data. (Alternative — no filter: rejected; it would silently fold in any future selfplay run that happened to use a human account.)
- Win rate and average win size are **derived by the caller** (`hands_won / hands_played`, `total_win_points / hands_won`) with divide-by-zero guards. Not stored on the wire to avoid two sources of truth and float-rounding in the protocol.
- `best_win_fan` is nullable (no wins yet → NULL from `MAX` over an empty filtered set).
- Empty case: a brand-new account returns `hands_played=0` and NULLs for the `_ms` fields; the client renders an empty-state ("No games yet — play a hand!").

### B.2 Point-performance series — `account_score_series`

For the line graph. An ordered list (oldest → newest) of **cumulative** score after each finalized hand, capped to the last `N` hands (default 200 — bounds the payload while spanning far more than the 20-row recent list).

```python
@dataclasses.dataclass(frozen=True)
class ScorePoint:
    ended_at_ms: int
    cumulative: int            # running SUM(final_score_delta) up to & incl. this hand
```

Implementation: select the account's finalized hands ordered by `ended_at_ms ASC`, take the last `N`, and accumulate in Python (a running total is clearer and more portable than a SQL window function, and N is small). The cumulative baseline is the *full-history* total minus the sum of the windowed deltas, so a capped window still shows the true standing on the y-axis — **or**, simpler for v1, the series cumulative is relative to the start of the window and the graph is labeled "last N hands." v1 takes the simpler relative-to-window form; absolute baseline is an open question.

### B.3 Wire protocol

Two new kinds added to `KNOWN_KINDS` (and a `test_codec.py` round-trip per the [KNOWN_KINDS allow-list rule](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_wire_codec_known_kinds.md) — every new kind needs both the allow-list entry and a codec round-trip test or the decoder drops the connection silently).

**`GET_PROFILE`** (client → server). No body beyond the envelope; the server scopes it to the connection's authenticated `account_id`. (v1 has no "view someone else's profile" — that would add an `account_id` field and a privacy decision.)

```json
{ "kind": "GET_PROFILE" }
```

**`PROFILE`** (server → client):

```json
{
  "kind": "PROFILE",
  "account": { "account_id": 3, "username": "connor", "display_name": "Connor" },
  "stats": {
    "hands_played": 142,
    "hands_won": 39,
    "draws": 11,
    "total_score": 312,
    "total_win_points": 1880,
    "best_win_fan": 26,
    "first_played_ms": 1717500000000,
    "last_played_ms": 1717589000000
  },
  "recent": [
    {
      "hand_id": "018f...c2",
      "match_id": null,
      "started_at_ms": 1717589000000,
      "ended_at_ms": 1717589120000,
      "terminal_kind": "HU",
      "won": true,
      "score_delta": 48,
      "fan_total": 8,
      "seat": 0,
      "opponents": ["v0", "v0", "v0"]
    }
  ],
  "series": [
    { "ended_at_ms": 1717500120000, "cumulative": -24 },
    { "ended_at_ms": 1717500300000, "cumulative": 24 }
  ]
}
```

- `recent[*].won` = `terminal_kind == "HU" && winner_seat == seat`. `score_delta` is this account's `final_score_delta` for the hand. `fan_total` is `null` unless the account won.
- `recent[*].opponents` — **omitted in v1** (the wire field stays optional). The live path records bot seats as `seat_kind='canned'` with `account_id=NULL` ([registry.py § _reserve_hand_row](../../mahjong/server/registry.py)), so opponent *names* aren't recoverable from the DB — only "human" vs "canned". Rather than show `["seat 1", "seat 2", "seat 3"]`, the history row shows date/result/±pts/fan. Resolving this properly (recording a bot's identity per seat) is follow-up work; see open question #3.
- `stats` carries raw counts/sums only; the client formats `win_rate` and `avg_win_size`.

### B.4 Server handler

In `orchestrator.py`, the Phase-1 admin/discovery loop gains one branch:

```python
elif kind == "GET_PROFILE":
    await self._handle_get_profile(conn)
```

`_handle_get_profile`:
1. `auth = self._auth_state.get(conn)`; if `None` or `self._persistence is None` → `ERROR{code: "not_authenticated"}` (profile requires auth).
2. Off-loop the **synchronous** DB reads via `run_in_executor` (per the [sync-DB rule](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_sync_db_run_in_executor.md) — async WS handlers must not call sync persistence directly): `account_stats`, `find_hands_by_account(limit=20)`, `account_score_series(limit=200)`, plus the per-hand opponent/display-name lookups.
3. Build and `conn.send(...)` the `PROFILE` message.

**Phase placement:** `GET_PROFILE` is a *lobby* concern (opened from the lobby, not mid-game), so it lives only in the admin/discovery loop — it does **not** need the in-game inbound branch. This is the inverse of the [two-phase-handler rule](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_router_two_phase_handlers.md): that rule applies to kinds usable *mid-game*; profile is not one, so one-phase wiring is correct. (If a future "view profile without leaving the table" button is added, it gains the in-game branch then.)

The server advertises support via `HELLO.features += ["profile"]` so the client can hide the profile button against an older server.

### B.5 Client

- **Routing.** `<mahjong-app>` already switches between lobby and table. Add a `view` state of `"lobby" | "profile" | "table"`. Lobby header gets `[ profile ]` (→ sets `view="profile"`, sends `GET_PROFILE`) and the `[ ⚙ ]` settings button.
- **`<profile-page>`** (new component) renders three blocks:
  1. **Stats grid** — games played, win rate (derived %), wins/draws, avg win size, best win (fan), total standing, last played (relative time). Empty-state when `hands_played==0`.
  2. **Point graph** — an ASCII line graph of `series` (see B.6).
  3. **Recent games** — a table: date · result (HU/draw/loss) · ±pts · fan · vs opponents · (match tag if grouped). Newest first.
- A `[ back ]` / `Esc` returns to the lobby.
- The existing stub `<stats-pane>` is left as-is (the in-game pane decision was "home screen only"); it may later mirror a compact summary, but that's out of scope.

### B.6 ASCII point graph

The client is an ASCII/Lit client (no charting lib, no build step — per the [web-ASCII client decision](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_client_vision_web_ascii.md)). The graph is rendered as a character grid: cumulative score on the y-axis (auto-scaled min→max), hand index on the x-axis, plotted with block/line glyphs and a zero baseline.

```
 +312 ┤                                   ╭─●
      │                              ╭─────╯
   +0 ┼──────────────╮      ╭───●────╯            ← zero baseline
      │              ╰──●───╯
  -48 ┤   ╭──●───────╯
      └─┬──────┬──────┬──────┬──────┬──────┬──
       h1     h24    h48    h72    h96    h142
```

- Pure function `renderScoreGraph(series, {width, height})` → string (or a grid of spans for theming), unit-testable without a browser.
- Auto-scales y to `[min(cumulative), max(cumulative)]` with the zero line marked if it falls in range. Degenerate cases (0 or 1 point, all-equal values) render a flat line or empty-state, not a divide-by-zero.
- Follows theme via the existing CSS vars (accent for the line, dim for axes), like the rest of the client.

---

## Alternatives considered

- **Materialised stat columns on `accounts` + triggers.** Fast leaderboard-style reads. Rejected per [sqlite-schema.md § Alternatives](sqlite-schema.md): premature at our scale; the aggregate query is sub-millisecond over thousands of rows. Revisit with a real slow query.
- **Per-match stat unit.** Rejected (decided per-hand 2026-06-05): solo-vs-bot hands have no `match_id`, so match stats would be sparse, and "match win" needs a placement definition the current flow doesn't exercise.
- **Profile as an in-game pane (fill the `<stats-pane>` stub).** Rejected: a full-width ASCII pane fights the opponent rows for width, and profile is a between-games activity. Decided "top-level home screen."
- **Separate `GET_STATS` / `GET_HISTORY` / `GET_GRAPH` requests.** Cleaner separation, but three round-trips for one page open. Rejected for v1: one `PROFILE` message is simpler and the payload is small. Pagination of history can add a `GET_HISTORY{before_hand_id}` later without touching the initial load.
- **Server-derived `win_rate` / `avg_win_size` on the wire.** Friendlier for a dumb client, but duplicates a derivation (drift risk) and bakes rounding into the protocol. Rejected: client derives from raw counts (trivial, with zero-guards).
- **Settings as a full-screen route (like profile).** Rejected: five toggles don't justify a screen; a modal is lighter and stays accessible from any view. The descriptor registry makes the modal grow gracefully.
- **Lift pane-visibility into a global persisted settings store.** Would make all settings uniform, but changes pane semantics (panes would follow you across tables) for a cosmetic win. Rejected for v1; pane rows stay live-per-table.

---

## Verification fixtures

Per [CLAUDE.md § verification ladder](../../CLAUDE.md). Core (persistence + wire) is **test-first**; client is browser-verified.

**Persistence (`account_stats` / `account_score_series`) — unit, test-first:**

1. **Empty account.** No hands → `hands_played=0`, `hands_won=0`, `total_score=0`, `best_win_fan=None`, `first/last_played_ms=None`. (Fails before the function exists.)
2. **One win, one loss, one draw.** Seed three finalized hands for the account (one where it's `winner_seat`, one where another seat wins, one `EXHAUSTIVE_DRAW`). Assert `hands_played=3`, `hands_won=1`, `draws=1`, and `total_score` == sum of the three `final_score_delta`s.
3. **`total_win_points` / `best_win_fan` only count won hands.** A loss with a large negative delta and a win with `fan_total=12` → `total_win_points` excludes the loss; `best_win_fan=12`.
4. **In-progress and non-live hands excluded.** A reserved-but-not-finalized hand (`ended_at_ms IS NULL`) and a `source='selfplay'` hand for the same account are **not** counted.
5. **`account_score_series` is cumulative and ordered.** Three hands with deltas `[-24, +48, +10]` (by `ended_at_ms`) → `cumulative = [-24, +24, +34]`. The cap (`limit`) returns the most recent `limit` points in ascending order.
6. **Score series window cap.** With `limit=200` and 250 hands, exactly 200 points returned, oldest of the 200 first.

**Wire codec — test-first:**

7. **`GET_PROFILE` round-trips.** `encode`→`decode` preserves the kind; it is in `KNOWN_KINDS`.
8. **`PROFILE` round-trips.** A full `PROFILE` dict with `stats`, `recent`, `series` survives `encode`→`decode` byte-stable; unknown future fields are preserved (per codec passthrough contract).

**Server handler — integration:**

9. **Authed `GET_PROFILE` returns `PROFILE` for the right account.** With a seeded persistence and an authenticated connection, `GET_PROFILE` yields a `PROFILE` whose `account.account_id` matches the session and whose `stats.hands_played` matches fixture 2's seeded count. The DB reads happen via `run_in_executor` (no direct sync call on the loop).
10. **Unauthenticated `GET_PROFILE` is refused.** Without auth (or without persistence configured) → `ERROR{code:"not_authenticated"}`, connection stays open.

**Graph render — unit (pure function):**

11. **`renderScoreGraph` degenerate cases.** Empty series → empty-state string; single point → flat line; all-equal values → flat line at that value with zero-line handling; no exceptions, no NaN in output.
12. **`renderScoreGraph` scaling.** A known series produces a grid of the requested `width`×`height` with the max value at the top row and the zero baseline on the correct row.

**Client — real-browser (Playwright), per the [wire→UI seam rule](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_test_wire_to_ui_seam.md):** drive the actual frame dispatch, not pre-set view state.

13. **Profile open → render.** From the lobby, click `[ profile ]`; assert a real `GET_PROFILE` goes out, the `PROFILE` frame is dispatched, and the stats grid + recent list + graph render with the seeded numbers.
14. **Settings menu.** S-A1..S-A4 above.

Fixtures 1–4 and 9 are load-bearing: a silent stat bug (wrong join, counting in-progress/selfplay hands, wrong winner comparison) is exactly the "plausible-but-wrong number" failure this project's verification discipline exists to catch.

---

## Open questions

1. **History pagination.** v1 returns the most recent 20 hands. Add `GET_HISTORY{before_hand_id, limit}` → `HISTORY` for "load more"? Deferred; `find_hands_by_account` already supports the keyset.
2. **Graph baseline.** v1 series is cumulative *relative to the window start* ("last N hands"). Should the y-axis show the absolute lifetime standing (window deltas offset by the pre-window total)? The pre-window total is one extra `SUM` query; deferred unless the relative view is confusing.
3. **`opponents` in `recent`.** Keep the per-hand opponent name lookups (≤60 small reads on profile open) or drop them from v1 to keep the query trivial? Leaning keep — "vs v0" is meaningful with the bot picker — but flagged as the easiest thing to cut if profile-open latency matters.
4. **Display-name rename UI.** Out of scope here, but the profile page is the natural future home for an "edit display name" affordance. Noted, not built.
5. **Bot profiles.** Bots are real accounts; `account_stats` works for them too. Do we expose a bot's profile (useful for "how is v0 doing")? Not in v1 (no UI entry point), but the query is account-kind-agnostic so it's free later.
