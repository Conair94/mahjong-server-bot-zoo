# Spec 31 — Reconnect / rejoin an in-progress game (FB-03)

Lets a player who dropped out of a live hand — refresh, Wi-Fi flap, laptop sleep,
phone takeover — get **back into the seat they were holding**, with the hand state
they missed, from the lobby. Closes [feedback-backlog.md](feedback-backlog.md) FB-03.

Builds on [session-mux.md](session-mux.md) (the seat-hold state machine that already
exists), [multi-human-seats.md](multi-human-seats.md) (the open-lobby join flow), and
[live-play-bugfixes.md](live-play-bugfixes.md) Spec 29 Bug A (session-token persistence
in `localStorage`). Overlaps [late-join-replay.md](late-join-replay.md) (spectator
catch-up) and [table-management.md](table-management.md) (FB-05).

**Status:** draft. **Spec only this session** — no implementation. FB-03 is `triaged`
moving to `in-progress` (spec written) per the backlog status vocabulary.

## The reframe: the server already does the hard part

The naïve reading of FB-03 ("there is no way to rejoin a game") implies a missing
reconnect subsystem. There isn't one missing — it's **built and tested**. Grounding
in the actual code:

- `mahjong/sessions/mux.py` `SeatSession` is a full `UNBOUND ↔ LIVE ↔ HELD` state
  machine. A socket drop moves the seat `LIVE → HELD` and arms a hold timer
  (`MAHJONG_SEAT_HOLD_SECONDS`, **default 60s**). A later `ATTACH` from the **same
  `user_id`** to a `HELD` seat hits `SeatSession._resume`, which **replays the ring
  buffer** (every `EVENT` missed, in order) or falls back to a fresh snapshot if the
  buffer overflowed, then re-sends any outstanding `PROMPT`. Conflict resolution is
  done: same-user-on-`LIVE` is a takeover (`replaced_by_new_session`), different-user
  is rejected (`seat_occupied` / `seat_not_yours`).
- Spec 29 Bug A already persists the session token to `localStorage`, so a **refresh
  re-authenticates** without a re-login, and the client re-`HELLO`s / `RESUME`s the
  WebSocket.
- The live table (`mahjong/server/registry.py`) exposes per-seat occupancy in
  `TABLE_LIST.tables[].seats[]` with `{kind, occupied, user_id}` — so the server
  already *knows*, and already *tells the client*, which seat a given `user_id` holds.

So the seat-hold, the replay, the takeover, the token persistence, and the occupancy
data are all present. **What's missing is the player-facing loop that connects them:**
after re-auth, nothing automatically walks the client back to the `HELD` seat, and the
lobby renders an occupied-by-you seat as just "occupied" — there's no "▶ Rejoin"
affordance. FB-03 is a **client-orchestration + small-discovery-API** feature, not a
new server subsystem. (This is the [verify-spec-premise] discipline paying off: the
spec's own "architecturally significant" label was anchored on the genre, not the
code.)

## Goals

- **A returning player lands back in their held seat in one action.** From the lobby
  (or automatically on refresh), the client detects "you hold seat *N* at table *T*"
  and re-`ATTACH`es, driving the existing `_resume` replay. The player sees the hand
  exactly where it is, including a still-outstanding prompt if it's their turn.
- **Discovery is server-authoritative.** The client does not guess which seat is
  "theirs" from stale local state; the server tells it, keyed on the authenticated
  `account_id`. (Per [prefer-authoritative-state-over-derivation].)
- **The seat-hold window is long enough for a human to come back.** 60s covers a Wi-Fi
  blip but not "I closed the tab to check something." This spec sets a separate,
  longer **rejoin window** for deliberate return and pins what the seat does while the
  human is away.
- **Rejoin works across the lobby boundary, not just same-socket.** Spec 29 fixed
  same-socket `RESUME` (reattach the *connection*). FB-03 is the case where the client
  is back at the **lobby** (fresh page load, picked a different table, etc.) and must
  re-enter the *seat*.
- **No new privacy surface.** Rejoin reuses the existing per-seat projection
  (`_resume` replays already-projected events). A returning player sees their own
  concealed hand; a different user is rejected before any state is sent.

## Non-goals

- **Not a new seat-state machine.** `session-mux.md`'s `UNBOUND/LIVE/HELD` is
  unchanged. This spec adds discovery + client flow + a window-length policy on top.
- **Not spectator catch-up.** Rejoining *as a player* is this spec; watching as a
  late-arriving spectator is [late-join-replay.md](late-join-replay.md).
- **Not cross-restart resume.** If the server process restarts, in-memory seat state is
  gone (per [session-mux.md § Server lifecycle] — "in-memory connection state only").
  Resuming a hand across a server restart would require rehydrating `TableHandle` from
  the record + DB; that's out of scope and tracked as an open question.
- **Not the multi-human lobby UX.** Coordinating *which* table several humans converge
  on is [table-management.md](table-management.md) (FB-05). FB-03 only handles *your
  own* return to a seat you already held.

## Current state (grounded)

| Capability | Where | Status |
| --- | --- | --- |
| Seat-hold on drop (`LIVE→HELD`, 60s timer) | `mux.py SeatSession.on_socket_dropped` | ✅ built + tested |
| Resume replays missed events / re-prompts | `mux.py SeatSession._resume` | ✅ built + tested |
| Same-user takeover, different-user reject | `mux.py SeatSession.attach` | ✅ built + tested |
| Session token in `localStorage`, refresh re-auths | client + Spec 29 Bug A | ✅ shipped |
| Per-seat occupancy incl. `user_id` on the wire | `registry.py` → `TABLE_LIST.seats[]` | ✅ built |
| **Auto re-ATTACH to held seat after re-auth** | — | ❌ **gap** |
| **Lobby "▶ Rejoin" affordance on your held seat** | client `<lobby-view>` | ❌ **gap** |
| **"Where do I hold a seat?" discovery for a `user_id`** | — | ⚠️ derivable from `TABLE_LIST`, but no direct signal |
| **Rejoin window distinct from 60s Wi-Fi-flap hold** | `config.py seat_hold_seconds` | ❌ **gap (policy)** |

## Design

### 1. Discovery — `AUTH_RESPONSE` carries your live seat-holds

The server already knows every `(table, seat)` a `user_id` holds (each `SeatSession`
stores `_user_id` while `LIVE` or `HELD`). On a successful auth handshake, surface it
directly rather than making the client scan `TABLE_LIST`:

> **Implementation note (grounded correction).** The spec originally proposed `HELLO`,
> but `HELLO` is emitted *before* auth (`orchestrator._send_hello`, line 270) so the
> account is unknown at that point. The post-auth frame is **`AUTH_RESPONSE { ok:true }`**
> (sent on both AUTH_REQUEST and RESUME success) — that's where `seat_holds` actually
> lives. Same idea (piggy-back on the existing post-auth frame), correct frame.

Add a `seat_holds` array to the **`AUTH_RESPONSE { ok:true }` (server→client)** frame
(sent after AUTH_REQUEST or RESUME succeeds). Each entry is a seat this authenticated
account currently holds:

```jsonc
{
  "kind": "AUTH_RESPONSE",
  "seq": 2,
  "ok": true,
  // ... existing fields (user_id, display_name, session_token, expires_at_ms) ...
  "seat_holds": [
    {
      "table_id": 3,
      "seat": 1,
      "state": "HELD",          // "LIVE" (socket still up elsewhere) | "HELD" (dropped, within window)
      "hand_index": 4,
      "rejoin_deadline_ms": 1733620000000  // absolute; null if state == "LIVE"
    }
  ]
}
```

- **Why on `HELLO` and not a new message.** Re-auth already happens on every reconnect
  and refresh; piggy-backing makes rejoin discovery free and atomic with auth. A
  dedicated `GET_SEAT_HOLDS` request would be a round-trip the client doesn't need.
- **`state: "LIVE"`** appears when the account holds a seat that still has a live socket
  (e.g. the laptop is still connected and the phone just authed). The client offers
  "take over here" (drives the existing `_takeover` path) rather than "rejoin."
- **Authoritative seat lookup** is a new read on `TableRegistry`: iterate tables, ask
  each `TableSessions` for seats whose `user_id == account_user_id`. O(tables × 4); at
  our scale (handful of tables) this is nothing.

### 2. Client flow — auto-rejoin on re-auth, manual from the lobby

```text
   page load / refresh / reconnect
            │
            ▼
   HELLO (re-auth) ──► seat_holds non-empty?
            │                   │
        empty                   ▼
            │            exactly one HELD hold ──► AUTO re-ATTACH(table,seat)
            ▼                   │                        │
       normal lobby             │ >1 hold, or a LIVE     ▼
                                ▼ (takeover) hold    _resume replay → in seat
                         lobby shows "▶ Rejoin
                         table T seat N" rows;
                         click ──► ATTACH(table,seat)
```

- **One `HELD` hold → auto-rejoin.** The overwhelmingly common case (you dropped from
  the one game you were in). The client re-`ATTACH`es without a click; the existing
  `ATTACHED` + buffered-`EVENT` replay puts the board back. A toast ("Reconnected to
  your game") covers the UX.
- **Ambiguity (>1 hold, or a `LIVE` takeover candidate) → explicit choice.** Render
  rejoin rows in `<lobby-view>`; the player picks. Avoids auto-yanking a seat the
  player may have intended to leave.
- **`ATTACH` is the existing message** (`mux.py TableSessions.attach`). No new inbound
  kind for rejoin — rejoin *is* an `ATTACH` to a `HELD` seat from the same user; the
  server already routes it to `_resume`. The only client-new code is the *decision* to
  send it and the lobby rows.
- **Rejection handling.** If the window expired between `HELLO` and the `ATTACH`, the
  server returns `ERROR { code: "seat_not_yours" }` (seat went `UNBOUND`, possibly
  re-seated). The client drops the stale rejoin row and falls back to the normal lobby.

### 3. Window policy — separate the Wi-Fi-flap hold from the rejoin window

The current single `MAHJONG_SEAT_HOLD_SECONDS = 60` serves two different contracts at
once (per [session-mux.md § Pending prompt across reconnect], the hold window is "your
seat won't get yanked the moment your Wi-Fi blips"). 60s is right for a blip and wrong
for "I'll be right back." This spec **keeps a single timer but raises the default**, and
documents the trade-off rather than adding a second timer (YAGNI — one timer, one
config, until a real need for two appears):

- Raise `MAHJONG_SEAT_HOLD_SECONDS` default **60 → 180s**. Long enough for a refresh +
  re-auth + deliberate rejoin; short enough that a walked-away human doesn't stall a
  multi-human table indefinitely.
- The **prompt deadline is unchanged** and independent: even within the hold window, if
  it's the absent player's turn, their prompt still defaults at `prompt.deadline` so the
  *rest of the table* never waits on one player (the existing `_on_prompt_deadline`
  path). Rejoining after a defaulted prompt is fine — the player rejoins mid-hand,
  having auto-passed the turn(s) they missed.
- **What the seat does while held:** nothing new. The seat is *held*, not *played by a
  bot* — the absent player's turns default to `default_action` (PASS / discard-drawn).
  We explicitly reject "substitute a bot while away" for v1 (see Alternatives).

### 4. Wire / codec changes

| Change | File | Note |
| --- | --- | --- |
| Add `seat_holds: list` to `AUTH_RESPONSE { ok:true }` | `wire/codec.py` `AuthResponseOk` | optional, omitted when empty; **`test_codec.py` round-trip** per [wire-codec-known-kinds] |
| New read `TableRegistry.seat_holds_for(user_id) -> list[SeatHold]` | `server/registry.py` | iterates tables; pure read |
| `HELLO` builder populates `seat_holds` | `server/orchestrator.py` auth path | after auth resolves `account_id → user_id` |
| Client: parse `seat_holds`, auto/-manual rejoin | `web/static/app.js`, `<lobby-view>` | reuses existing `ATTACH` emit |
| Raise hold default 60 → 180 | `server/config.py` | one-line default + doc |

No new inbound message kind. No change to `ATTACH` / `ATTACHED` / `_resume`.

## Alternatives considered

- **Substitute a bot for the absent human while held.** Tempting (the table keeps
  moving at full speed). Rejected for v1: it changes the *competitive* outcome (a bot
  may play the hand very differently than the returning human would have), and the
  re-entry semantics ("merge the bot's mid-hand decisions back to the human") are
  genuinely hard. The default-action path (auto-pass/discard-drawn) is the honest
  minimal behavior — the player simply misses the turns they were gone for. Revisit if
  multi-human tables make "dead seat slows everyone" a real complaint.
- **Second timer: short `seat_hold` + long `rejoin_window`.** More precise (yank the
  seat from the table's perspective at 60s, but still let the original human reclaim it
  until 180s). Rejected as premature: it doubles the timer bookkeeping in `SeatSession`
  for a distinction no one has asked for. One raised timer first; split only if needed
  ([defer-parallel-until-needed] generalized — don't add machinery before the need).
- **Client derives held seats by scanning `TABLE_LIST` for its own `user_id`.** Works
  with zero new wire fields. Rejected: it makes the client re-derive authoritative
  state, it races (the table list is a snapshot), and it leaks every table's occupancy
  to every client just to answer "where am I." The `HELLO.seat_holds` signal is scoped
  to the asker and atomic with auth.
- **Cross-restart resume (rehydrate `TableHandle` from the record + DB).** High value
  for a home server that gets restarted, but a much larger build (replay the JSONL into
  a live `GameState`, re-seat humans, reconcile the SQLite `hand_index` in-progress
  row). Deferred to an open question; not v1.

## Verification fixtures

These are the acceptance criteria. Server fixtures use the existing `TableSessions`
test harness; client fixtures use the Playwright async harness over the fake wire
([playwright-async-only]).

1. **`seat_holds` surfaces a HELD seat.** Bind seat 1 at table 3 (user X), drop the
   socket (→ `HELD`), re-auth as user X. Assert `HELLO.seat_holds` contains
   `{table_id:3, seat:1, state:"HELD", rejoin_deadline_ms:…}`.
2. **`seat_holds` surfaces a LIVE seat as a takeover candidate.** User X is `LIVE` on
   seat 1 over connection A; user X authes on connection B. Assert `HELLO.seat_holds`
   entry has `state:"LIVE"`, `rejoin_deadline_ms:null`.
3. **`seat_holds` empty for a user holding nothing.** Auth a user with no seats →
   `seat_holds == []`.
4. **Auto-rejoin: single HELD hold drives one ATTACH.** Client harness: re-auth with
   exactly one `HELD` hold. Assert the client emits exactly one `ATTACH(table,seat)`
   without user interaction, and the board renders from the replayed buffer.
5. **Manual rejoin: >1 hold renders rows, click ATTACHes.** Re-auth with two `HELD`
   holds. Assert no auto-`ATTACH`; lobby shows two "▶ Rejoin" rows; clicking one emits
   `ATTACH` for that `(table,seat)`.
6. **Rejoin replays missed events in order.** Bind, send 6 `EVENT`s (3 live, drop, 3
   buffered), rejoin via the FB-03 client path. Assert the 3 buffered events arrive in
   order before any new traffic (delegates to the existing `_resume` fixture; this one
   pins it through the *client rejoin* entry, the [test-wire-to-ui-seam] discipline).
7. **Rejoin re-issues an outstanding prompt.** Bind, `decide()` issues a prompt, drop
   while it's outstanding (within window), rejoin. Assert the same `prompt_id` is
   re-sent and the client can answer it.
8. **Expired window → graceful fallback.** Set hold to 1s, bind, drop, wait 2s,
   then attempt rejoin (`ATTACH`). Assert `ERROR { code:"seat_not_yours" }` and the
   client drops the rejoin row and shows the normal lobby (no crash, no wedge).
9. **Takeover from `seat_holds` LIVE entry.** Client B re-auths with a `LIVE` hold,
   user chooses "take over." Assert connection A receives `DETACH
   {reason:"replaced_by_new_session"}` and B becomes `LIVE` (delegates to the existing
   takeover fixture through the client path).
10. **Raised default is wired.** With no env override, `config.seat_hold_seconds ==
    180`; with `MAHJONG_SEAT_HOLD_SECONDS=45`, it's 45.

Fixtures 4 and 8 are load-bearing: 4 is the headline "I refreshed and I'm back in my
game" path; 8 proves the failure mode is graceful (FB-01 taught us silent wedges are
the cardinal sin here).

## Open questions

1. **Cross-restart resume.** Should a server restart rehydrate live hands from the
   record + `hand_index` in-progress rows so humans can rejoin? High value on a
   home server but a separate, larger spec. Recommend: out of scope for FB-03; file as
   its own follow-up if restarts during games become a real pain.
2. **Rejoin into a hand that ended while you were away.** If the hand finishes during
   the hold window, the seat goes `UNBOUND` (per `_resolve_pending_and_teardown`) and
   the player rejoins the *table* at the lobby/next-hand gate, not the dead hand. Is a
   "you missed the end — here's the summary" replay owed? Lean yes, but it overlaps
   FB-04 replay; defer the decision to whichever ships second.
3. **Multi-human "N players waiting to rejoin" display.** Only matters once tables
   routinely have >1 human. Punt to [table-management.md](table-management.md) (FB-05),
   which owns the multi-human lobby surface.
