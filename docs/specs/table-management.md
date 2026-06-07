# Spec 33 — Table management & multi-human join UX (FB-05)

Makes it clear, for **several humans on the same server**, which table they're joining,
which seats are open, who's there, and who starts the hand. Closes
[feedback-backlog.md](feedback-backlog.md) FB-05 ("there is no way for multiple players
to manage which table they are joining etc. This connects to the not being able rejoin
bug").

Builds on [multi-human-seats.md](multi-human-seats.md) (the open-lobby `CREATE_TABLE` /
`START_HAND` model — already built), the `TABLE_LIST` wire surface in
[wire-protocol.md](wire-protocol.md), and [seat-bot-picker] (per-seat bot selection on
create). Tightly coupled to [reconnect-rejoin.md](reconnect-rejoin.md) (FB-03): the
lobby is also where a returning player sees and re-enters a held seat.

**Status:** draft. **Spec only this session** — no implementation. FB-05 is `triaged`
(P2) moving to `in-progress` (spec written). Lowest priority of the three; sequence it
after FB-03, with which it shares the lobby surface.

## The reframe: the lobby exists; multi-human *coordination* is thin

FB-05 isn't "there's no lobby" — there is one. Grounding:

- `<lobby-view>` (`web/static/app.js`) lists active tables, supports **create** (with
  per-seat kind/bot selection, [seat-bot-picker]) and **join** (`ATTACH` to a chosen
  seat). Hand ignition is the explicit `START_HAND` (no auto-fire on attach, post-8.7.d).
- `TABLE_LIST.tables[].seats[]` already carries `{kind, occupied, user_id}` per seat —
  the data to render "who's where" is on the wire.
- The server enforces start authority: `registry.py` returns `humans_not_ready` /
  `hand_already_started` / `not_authorized` error codes around `START_HAND`.

The whole *mechanism* works for one human creating a table and filling the rest with
bots. What's thin is the **multi-human coordination UX** — the affordances several people
need to converge on the same table without out-of-band ("hey, join table 3") coordination:

1. **Discoverability of *people*, not just tables.** The list shows occupancy counts but
   not *who* (display names) is seated, so players can't recognize "that's my friend's
   table."
2. **Live lobby freshness.** The table list is fetched, not pushed — a second human
   joining doesn't update the first human's lobby without a manual refresh.
3. **Seat-selection clarity for the second+ human.** Joining an existing table and
   picking an *open human seat* (vs. a bot seat) is underspecified in the UI.
4. **Start authority + readiness display.** "Everyone's seated, who presses start, and
   what are we waiting for" has server rules but no clear lobby surface.
5. **The FB-03 seam.** A held seat (yours) should appear in the lobby as "▶ Rejoin,"
   which [reconnect-rejoin.md](reconnect-rejoin.md) owns but renders *here*.

So FB-05 is **lobby UX + a live-refresh push + display-name plumbing**, on top of a
working create/join/start mechanism. P2 polish, not new architecture.

## Goals

- **See who's at a table, not just how many.** Each table row shows seated players'
  display names and seat kinds (human/bot/open), so humans can recognize and converge on
  the right table.
- **The lobby stays fresh without a manual refresh.** When a seat's occupancy changes
  (someone joins, drops, or a hand starts), other clients viewing the lobby see it
  promptly.
- **Joining the second+ seat is obvious.** A human joining an existing table sees which
  seats are open-for-humans vs. bot-filled vs. taken, and picks one in one action.
- **Start authority and readiness are visible.** The lobby shows who may press
  `START_HAND` and what's blocking start (e.g. "waiting for seat 2 to be claimed"),
  matching the server's existing rules rather than failing silently.
- **Held seats surface for rejoin.** Integrate FB-03's `seat_holds`: your held seat
  appears as a distinct "▶ Rejoin" row.

## Non-goals

- **Not the rejoin mechanism.** Seat-hold, replay, and the re-`ATTACH` flow are
  [reconnect-rejoin.md](reconnect-rejoin.md) (FB-03). FB-05 only *renders* the held-seat
  affordance in the lobby.
- **Not matchmaking / ELO seeding.** Auto-balancing humans across tables, skill-based
  seating, queues — out of scope. This is manual, human-driven table choice.
- **Not private/invite-only tables, passwords, or kicking.** Access control beyond the
  existing auth/invite model is a separate concern (open question).
- **Not changing the open-lobby model.** `CREATE_TABLE` / explicit `START_HAND` /
  `CannedAdapter`-PASS bots stay exactly as [multi-human-seats.md](multi-human-seats.md)
  pins them. FB-05 is presentation + freshness over that model.
- **Not spectator UX.** Choosing a table to *spectate* overlaps but is driven by the
  existing `SPECTATE` path; FB-05 focuses on *playing* coordination.

## Current state (grounded)

| Capability | Where | Status |
| --- | --- | --- |
| Lobby lists tables; create + join + start | `web/static/app.js` `<lobby-view>` | ✅ built |
| Per-seat `{kind, occupied, user_id}` on the wire | `registry.py` → `TABLE_LIST.seats[]` | ✅ built |
| Per-seat bot selection on create | [seat-bot-picker] | ✅ built |
| Explicit `START_HAND` + start-authority error codes | `registry.py` | ✅ built |
| **Seated players' display names in the list** | — | ⚠️ `user_id` only, no display name |
| **Live lobby refresh (push on occupancy change)** | — | ❌ **gap** (fetch-only) |
| **Open-human-seat vs bot-seat join clarity in UI** | `<lobby-view>` | ⚠️ thin |
| **Readiness / "waiting for…" display** | — | ❌ **gap** |
| **Held-seat "Rejoin" row** | — | ❌ **gap** (FB-03 provides data) |

## Design

### 1. Display names in `TABLE_LIST`

`TABLE_LIST.seats[]` carries `user_id` but not a human-readable name. Add an optional
`display_name` to each occupied human seat (resolved from `accounts.display_name`):

```jsonc
{
  "kind": "TABLE_LIST",
  "tables": [
    {
      "table_id": 3,
      "phase": "lobby",                 // "lobby" | "in_hand" (existing)
      "seats": [
        { "seat": 0, "kind": "human", "occupied": true,  "user_id": "u_7",
          "display_name": "ConnorL", "state": "LIVE" },
        { "seat": 1, "kind": "human", "occupied": false },
        { "seat": 2, "kind": "bot",   "occupied": true,  "bot_id": "v0" },
        { "seat": 3, "kind": "human", "occupied": true,  "user_id": "u_9",
          "display_name": "Sam", "state": "HELD" }   // away — rejoinable by its owner
      ]
    }
  ]
}
```

- `display_name` only on occupied human seats; resolved server-side (the registry knows
  `user_id`; a cheap `account_id → display_name` lookup fills it).
- `state` (`LIVE`/`HELD`) lets the lobby show "Sam (away)" — and is exactly the signal a
  *different* user needs to know the seat isn't free (can't take a `HELD` seat; only its
  owner rejoins, per FB-03's conflict rules).
- **`KNOWN_KINDS`/codec round-trip** for the widened `TABLE_LIST` shape
  ([wire-codec-known-kinds]).

### 2. Live lobby freshness — push on occupancy change

Today the lobby is fetch-on-demand. For multi-human convergence, a second player joining
should update everyone's lobby. Minimal mechanism, reusing the existing fanout idea:

- Clients in the lobby (not yet seated at a table) are a **lobby subscription set** —
  analogous to spectators but table-list-scoped. On any occupancy transition
  (`ATTACH` accepted, seat → `HELD`/`UNBOUND`, `START_HAND`, `CREATE_TABLE`), the server
  pushes a fresh `TABLE_LIST` (or a `TABLE_LIST_DELTA` for the changed table) to that set.
- **Coarse is fine.** Re-pushing the whole `TABLE_LIST` on change is cheap at our scale
  (a handful of tables). A delta frame is an optimization; spec the full re-push for v1,
  flag the delta as an open question. ([defer-parallel-until-needed] generalized.)
- Implementation seam: the registry already has the occupancy-change call sites
  (attach/detach/start); they gain a "notify lobby subscribers" hook. This is the
  [event-callback-spectator-seam] pattern — wire the lobby push as a passive observer,
  not through seat adapters.

### 3. Seat-selection clarity (client)

In `<lobby-view>`, render each existing table's seats as a small **seat map** rather than
a count:

```text
  Table 3  · lobby · 2/4 seated
   ┌────┬────┬────┬────┐
   │ ●  │ ○  │ 🤖 │ ◐  │     ●=you-can-take  ○=open  🤖=bot  ◐=away(HELD)  ■=taken
   │Conn│open│ v0 │Sam │
   └────┴────┴────┴────┘
        click an ○ open human seat → ATTACH(table=3, seat=1)
```

- Clicking an **open human seat** (`occupied:false, kind:"human"`) emits the existing
  `ATTACH(table, seat)`. No new message.
- A `HELD` seat is shown as "away" and is **not** clickable by others (server would
  reject `seat_not_yours`); it *is* the rejoin affordance for its owner (§4).
- A bot seat is informational (shows which bot); not joinable as a human in v1 (changing
  a seated bot to a human mid-lobby is an open question).

### 4. Readiness + start authority + held-seat rejoin

- **Start affordance.** The lobby shows a `START_HAND` button to seats authorized to
  start (per the registry's existing rule), disabled with a reason chip when blocked
  ("waiting for 1 more human" / "waiting for seat 2"). The disabled-reason mirrors the
  server's `humans_not_ready` etc. codes so the UI and server agree
  ([test-wire-to-ui-seam]).
- **Held-seat rejoin row.** Using FB-03's `HELLO.seat_holds`, the lobby renders your own
  held seat as a prominent "▶ Rejoin Table 3 (seat 1)" row above the table list. Click →
  the FB-03 `ATTACH`-to-`HELD` flow. This is the concrete "connects to the rejoin bug"
  link the report calls out.

### 5. Files touched

| Change | File |
| --- | --- |
| `display_name` (+ `state`) on `TABLE_LIST.seats[]` | `wire/codec.py` + `registry.py` + `tests/wire/test_codec.py` |
| Lobby subscription set + push-on-change | `server/registry.py` / `orchestrator.py` (event-callback seam) |
| Seat-map rendering + open-seat join + start chip | `web/static/app.js` `<lobby-view>` |
| Held-seat rejoin row (consumes FB-03 `seat_holds`) | `<lobby-view>` |

## Alternatives considered

- **Client polls `TABLE_LIST` on a timer for freshness.** Trivial; no server push.
  Rejected as the *primary* mechanism: polling is laggy (convergence feels broken if it
  takes 5s to see a friend join) and wasteful for an idle lobby. A push on change is a
  small, well-scoped addition (the registry already has the change call sites). Polling
  is an acceptable *fallback* if the push proves fiddly.
- **Per-table chat / "ready" toggles per player.** Richer coordination (everyone marks
  ready, then anyone starts). Rejected for v1: the open-lobby model already has an
  explicit `START_HAND`; per-player ready toggles are scope creep over a P2 polish item.
  Revisit if "who starts" becomes a real friction point with 4 humans.
- **Server-driven matchmaking (auto-assign humans to tables).** Removes the coordination
  problem entirely. Rejected: it's a different product (queue-based) and the user asked
  for *manual* "manage which table they are joining," i.e. human choice, not automation.
- **Delta frames instead of full `TABLE_LIST` re-push.** More efficient. Deferred: at a
  handful of tables the full re-push is negligible; a `TABLE_LIST_DELTA` is premature
  optimization. Open question, not v1.

## Verification fixtures

1. **`TABLE_LIST` carries display names + seat state.** Seat user_id `u_7` (display
   "ConnorL") at table 3 seat 0, drop seat 3's socket (→ HELD). Assert the
   `TABLE_LIST.seats[]` shows `display_name:"ConnorL"`, seat 0 `state:"LIVE"`, seat 3
   `state:"HELD"`; open seats omit `display_name`.
2. **Lobby push on occupancy change.** Client A subscribed to the lobby; client B
   `ATTACH`es a seat at table 3. Assert client A receives an unsolicited `TABLE_LIST` (or
   delta) reflecting B's seat within one event-loop turn, no manual refresh.
3. **Push on hand start.** Lobby subscriber present; a table runs `START_HAND`. Assert the
   subscriber sees the table flip to `phase:"in_hand"`.
4. **Open human seat join.** Client clicks an `occupied:false, kind:"human"` seat in the
   seat map; assert it emits exactly `ATTACH(table, seat)` for that seat and transitions
   into the table on `ATTACHED`.
5. **Held seat is not joinable by others.** Client (user Y) clicks a `HELD` seat owned by
   user X; assert the UI treats it as non-clickable (and, if forced, the server returns
   `seat_not_yours` — no state change).
6. **Start chip reflects server authority.** With an unfilled human seat, assert the
   `START_HAND` button is disabled with a "waiting for…" reason; once all human seats are
   claimed, it enables; pressing it emits `START_HAND` and the server accepts (no
   `humans_not_ready`).
7. **Held-seat rejoin row renders from `seat_holds`.** Client re-auths with a `HELD`
   `seat_hold` (FB-03 data); assert the lobby shows a "▶ Rejoin Table T (seat N)" row and
   clicking it drives the FB-03 `ATTACH`-to-HELD path (delegates the rejoin mechanics to
   reconnect-rejoin.md fixtures; this pins the *lobby rendering* of it).
8. **Display name resolves to current account value.** Rename an account's
   `display_name`; assert a subsequent `TABLE_LIST` shows the new name for that seat (no
   stale cache).

Fixture 2 is load-bearing (live freshness is the core "multiple players manage which
table" ask); fixture 7 is the explicit FB-03↔FB-05 seam.

## Open questions

1. **Delta vs full re-push.** Ship `TABLE_LIST_DELTA` for the changed table only, or
   re-push the whole list? Full re-push for v1; delta if lobby traffic ever matters.
2. **Convert a seated bot to a human mid-lobby (and vice versa).** Should a late human be
   able to claim a seat currently filled by a bot before `START_HAND`? Useful, but it
   reopens the `CREATE_TABLE.seats[]` immutability assumption from
   [multi-human-seats.md]. Defer; decide with that spec's owner.
3. **Private / invite-only tables.** Out of scope here, but the seat map is where a
   "locked" indicator would live. File separately if friends-and-family wants it.
4. **Lobby subscription lifecycle.** Is a client "in the lobby" implicitly (authed, not
   seated) or via an explicit `WATCH_LOBBY` subscribe? Lean implicit (authed-and-unseated
   ⇒ subscribed) to avoid a new message, but pin it when implementing the push set.
