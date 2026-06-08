# Spec 32 — Per-account game records: history, stats & replay (FB-04)

Gives a player a "my games" surface: a list of the hands they've played, their running
stats, and the ability to **watch a past hand back**. Closes
[feedback-backlog.md](feedback-backlog.md) FB-04 ("game records should be saved by
account for replay and stat purposes").

Builds on [persistence-api.md](persistence-api.md) + [sqlite-schema.md](sqlite-schema.md)
(the `hand_index` / `hand_participants` index — already built), [record-format.md](record-format.md)
(the on-disk JSONL records + the `records/replay.py` reconstructor — already built),
and [profile-and-settings.md](profile-and-settings.md) Spec 28 (the profile page that
already shows stats + recent hands). Reuses the per-seat projection from
[state-schema.md](state-schema.md) and the wire `EVENT` shape from
[wire-protocol.md](wire-protocol.md).

**Status:** draft. **Spec only this session** — no implementation. FB-04 is `triaged`
moving to `in-progress` (spec written).

## The reframe: records are already saved by account; replay is the missing half

FB-04 reads as two asks — "save records by account" and "replay + stats." Grounding in
the code, **the first ask is already done** and **stats are already shipped**; the
genuinely new work is the replay *viewer*.

What already exists:

- **Account-keyed index.** `hand_index` + `hand_participants(account_id)` tables
  (migration `_0001_initial.py`) associate every seat in every hand to an `account_id`.
  The live serve path writes them: `registry.py` calls `reserve_hand` at HEADER and
  `finalize_hand` at FOOTER, filling `participants[seat].account_id` from the bound
  human identity. So records **are** saved by account.
- **Query API.** `persistence/hands.py` ships `find_hands_by_account` (keyset-paginated
  "my games"), `account_stats` (hands played/won/draws, totals, best fan), and
  `account_score_series` (cumulative-score sparkline data).
- **Stats surface.** Spec 28's profile page already calls all three and renders stats +
  a recent-hands list (`orchestrator.py _build_profile_payload` → `PROFILE.recent[]`).
- **Replay reconstruction.** `records/replay.py` already replays a record's JSONL back
  into the canonical `GameState` sequence (built for record-format verification fixture
  2 — it correctly handles claim windows, kong replacement draws, and HU terminals).

What's missing — the actual FB-04 work:

1. **A replay *viewer*** — there is no way for a player to *watch* a recorded hand in
   the client. `replay.py` reconstructs `GameState`s server-side for verification, but
   nothing projects those to a seat's wire `EVENT` stream and ships them to a browser
   that can step through them.
2. **A replay *fetch* API** — no wire request to pull a specific hand's event stream for
   playback, and no authorization rule for who may watch which hand.
3. **"My games" as a navigable list** — `PROFILE.recent[]` carries the rows but they're
   a static summary; FB-04 wants each row to be a clickable entry into the replay
   viewer, with pagination past the first 20.

So FB-04 is **a replay viewer + a thin fetch API + list navigation**, on top of a data
layer that's already complete and load-bearing in production.

## Goals

- **"My games": a paginated, navigable history.** Every finalized hand the account
  played, newest first, with outcome (won/lost/draw, score delta, fan), reusing
  `find_hands_by_account`'s keyset pagination so it scales past 20 rows.
- **Watch a past hand back.** Pick a hand → step through it event-by-event (or
  auto-play with a speed control) in the **same board renderer** the live client uses.
  No second rendering path.
- **Replay shows what that seat saw.** A player replaying their own hand sees it from
  *their* seat (their concealed tiles revealed, opponents' concealed tiles hidden as
  they were live) — the existing per-seat projection. At `HAND_END` the settlement
  reveal applies as normal (per [hand-end-settlement-reveal]).
- **Authorization is explicit.** You may replay a hand you **participated in**. Replay
  of arbitrary hands by non-participants is gated (admin-only, or public-by-config);
  pinned below, not left implicit.
- **Reuse the determinism guarantee.** Replay is reconstructed from the seed + recorded
  actions via the existing `replay.py`; the record's footer `checksum` already pins
  integrity. A tampered/truncated record is refused (the reader already enforces this).

## Non-goals

- **Not a new data model.** `hand_index` / `hand_participants` / the JSONL records are
  unchanged. No schema migration unless an open question forces one.
- **Not re-deriving stats.** `account_stats` / `account_score_series` are the stats
  contract; this spec consumes them, doesn't reinvent them.
- **Not cross-hand "match" replay.** v1 replays **one hand**. Stitching a whole match
  (`match_id` → N hands) into a continuous replay is a later nicety (open question).
- **Not live spectating.** Watching an *in-progress* hand is [session-mux.md] spectators
  + [late-join-replay.md]; FB-04 replays *finished* hands from disk.
- **Not bot-training data export.** The records are already the RL substrate
  ([record-format.md]); a training-set export surface is its own concern.

## Current state (grounded)

| Capability | Where | Status |
| --- | --- | --- |
| Account↔hand association persisted live | `registry.py` → `reserve_hand`/`finalize_hand` | ✅ built |
| "My games" query (keyset-paginated) | `hands.py find_hands_by_account` | ✅ built |
| Stats aggregate + score series | `hands.py account_stats` / `account_score_series` | ✅ built |
| Profile shows stats + recent 20 | `orchestrator.py _build_profile_payload` → `PROFILE` | ✅ shipped (Spec 28) |
| Record → GameState reconstruction | `records/replay.py` | ✅ built (verification) |
| Record integrity (checksum, refuse-on-tamper) | `records/reader.py` | ✅ built |
| **Project replay GameStates → seat EVENT stream** | — | ❌ **gap** |
| **Wire API to fetch a hand's replay** | — | ❌ **gap** |
| **Replay viewer (step/auto-play) in client** | — | ❌ **gap** |
| **"My games" paginated, clickable list** | — | ❌ **gap** (rows exist, not navigable) |

## Design

### 1. Replay event stream — project, don't reinvent

`records/replay.py` yields the canonical `GameState` sequence. A new thin function
turns that into the **same wire `EVENT` frames** the live client already renders — so
the replay viewer reuses `apply_event.js` / `render.js` verbatim ([test-wire-to-ui-seam]:
one renderer, exercised by both live and replay).

Two viable sources for the frames; pick the **recorded events**, not re-projection:

- The JSONL record already contains the per-event payloads the manager emitted. The
  cleanest replay stream is **the recorded events themselves, re-projected for the
  requesting seat** via `project_event(record_event, seat=S)` — identical to what
  `mux.py SeatSession.observe` does live. This guarantees a replay is byte-shaped like
  the live stream the player originally saw.
- `replay.py`'s `GameState` reconstruction stays the **verification** path (it proves
  the record is internally consistent); the viewer doesn't need full state replay, just
  the projected event sequence + the terminal.

New module `records/replay_stream.py` (or a function on `replay.py`):

```python
def projected_events_for_seat(
    record_events: list[dict], *, seat: int | None
) -> list[dict]:
    """Project each recorded event for `seat` (None = public view), yielding the
    EVENT/HAND_END payloads the client renderer consumes. Reuses
    engine.state.project_event — the same projection mux.py uses live."""
```

Authorization decides the `seat` argument: a participant replays from **their own
seat**; an authorized non-participant (admin / public) gets `seat=None` (public view).

### 2. Wire API — list + fetch

Two new request/response pairs (each needs a `KNOWN_KINDS` entry **and** a
`test_codec.py` round-trip, per [wire-codec-known-kinds]):

**`GET_HISTORY` → `HISTORY`** — paginated "my games" (extends what `PROFILE` shows once,
to a scrollable, paged list):

```jsonc
// client → server
{ "kind": "GET_HISTORY", "seq": 12, "before_hand_id": "h_…", "limit": 50 }
// server → client
{
  "kind": "HISTORY", "seq": 40,
  "hands": [
    { "hand_id": "h_…", "started_at_ms": 1733600000000, "ended_at_ms": 1733600300000,
      "terminal_kind": "HU", "won": true, "score_delta": 48, "fan_total": 8, "seat": 1 }
    // … same row shape as PROFILE.recent[], reused
  ],
  "next_before_hand_id": "h_…"   // null when no more pages
}
```

Backed directly by `find_hands_by_account(account_id, limit, before_hand_id)` — the
keyset pagination is already there.

**`GET_REPLAY` → `REPLAY`** — fetch one hand's event stream for playback:

```jsonc
// client → server
{ "kind": "GET_REPLAY", "seq": 13, "hand_id": "h_…" }
// server → client
{
  "kind": "REPLAY", "seq": 41,
  "hand_id": "h_…",
  "seat": 1,                      // the viewing seat (participant's own, or -1/public)
  "snapshot": { … },             // initial board for `seat` (deal), project(state0, seat)
  "events": [ {EVENT payload}, … , {HAND_END terminal} ],
  "meta": { "ruleset_id": "mcr-house-3fan", "winner_seat": 1, "fan_total": 8 }
}
```

Server handler (on a worker thread — the record read + replay is sync I/O, per
[sync-db-run-in-executor]):

1. `get_hand(hand_id)` → resolve participants; **authorize** (see §3). 404-equivalent
   `ERROR { code:"hand_not_found" }` / `ERROR { code:"not_authorized" }`.
2. Read the JSONL via `records/reader.py` (verifies checksum; refuses tampered files).
3. `projected_events_for_seat(record_events, seat=viewing_seat)` → the `events` array.
4. Ship one `REPLAY` frame. (One frame, not streamed — an MCR hand is ~100–200 small
   events, well under a comfortable single-message size. Streaming is an open question
   only if a pathological record blows the budget.)

### 3. Authorization

| Requester relationship to the hand | Allowed? | Viewing seat |
| --- | --- | --- |
| Participated (their `account_id` in `hand_participants`) | ✅ | their own seat (concealed revealed for that seat) |
| Did not participate, `role == 'admin'` | ✅ | public view (`seat = None`) |
| Did not participate, non-admin | ❌ (`not_authorized`) | — |
| Hand is `source == 'selfplay'` | ✅ for admins only (v1) | public view |

Rationale: a finished hand's settlement reveal is already public *to its participants*
([hand-end-settlement-reveal]), but a non-participant replaying your hand from your seat
would leak your *in-hand* concealed tiles turn-by-turn, which were private at the time.
Public (`seat=None`) projection avoids that leak. A `public_replays` config flag (default
off) can later open non-participant public-view replay table-wide; deferred (open Q).

### 4. Client — "my games" list + replay viewer

- **History list.** A `<history-view>` (reachable from the profile page) renders
  `HISTORY.hands[]` as rows (date · outcome · score Δ · fan), with "load more" driving
  `GET_HISTORY { before_hand_id }`. Each row is clickable → opens the replay viewer.
- **Replay viewer.** A `<replay-view>` that:
  - takes a `REPLAY` frame, seeds the board from `snapshot`,
  - reuses `apply_event.js` to fold `events[]` into view state and `render.js` /
    `<game-pane>` to draw — **the exact live renderer**, no replay-specific board code,
  - has transport controls: ◀ step / ▶ step, ⏸/▶ auto-play with a speed selector, a
    scrubber over the event index, and a "jump to HAND_END" — all pure client-side
    iteration over the already-fetched `events[]` (no server round-trips mid-replay).
  - is read-only: no `PROMPT`s, no `ACTION`s; the audio cues (Spec FB-06) may optionally
    fire on stepped events behind the existing mute toggle.
- **No new renderer.** The whole point: replay is "feed the recorded EVENT stream
  through the live reducer/renderer at the user's pace." If the live board can draw it,
  the replay can.

### 5. Files touched

| Change | File |
| --- | --- |
| `projected_events_for_seat` | `records/replay_stream.py` (new) or `records/replay.py` |
| `GET_HISTORY`/`HISTORY`, `GET_REPLAY`/`REPLAY` models + `KNOWN_KINDS` | `wire/codec.py` + `tests/wire/test_codec.py` |
| History + replay request handlers (worker-thread reads) | `server/orchestrator.py` |
| Authorization helper (participant / admin / public) | `server/orchestrator.py` or `persistence` |
| `<history-view>`, `<replay-view>` + profile entry point | `web/static/app.js` (+ split if it grows) |

## Alternatives considered

- **Reconstruct full `GameState`s server-side and ship those.** Use `replay.py`'s
  `GameState` sequence directly. Rejected: a `GameState` is heavier than a projected
  `EVENT`, it would need a *second* client path to render board-from-state (the live
  client renders board-from-event-stream), and it risks leaking concealed info if the
  projection isn't reapplied. Shipping projected events reuses the live renderer and the
  live privacy rule for free.
- **Stream the replay frame-by-frame from the server (like live play).** The client
  could `SPECTATE`-style subscribe and the server could pace the events. Rejected:
  pointless server statefulness for a finished hand. Ship the whole event array once and
  let the *client* pace playback — scrubbing backward is then free, and there's no
  server-side replay cursor to manage.
- **Re-project on the client from raw records.** Send the raw JSONL, project in JS.
  Rejected: it would duplicate `project_event`'s privacy logic in the client (a place to
  get concealed-info masking subtly wrong — exactly the [test-wire-to-ui-seam] /
  privacy hazard). Projection stays server-side, single source of truth.
- **Skip the list API; reuse `PROFILE.recent[]` only.** Cheaper. Rejected: `PROFILE`
  caps at 20 and isn't paginated; "my games" needs to scroll a real history. The
  keyset query already supports it, so the marginal cost of `GET_HISTORY` is tiny.

## Verification fixtures

1. **`projected_events_for_seat` matches the live stream.** Take a recorded hand;
   project for seat 1 via the new function and assert it equals the sequence
   `mux.py`'s `observe` would have emitted to seat 1 (same `project_event` path) —
   byte-equal inner payloads.
2. **Participant replay reveals own seat, hides opponents.** Replay a hand as a
   participant of seat 1: assert the `snapshot` + events reveal seat 1's concealed tiles
   and elide opponents' concealed draws (no `tile` on others' DRAW), matching the live
   per-seat projection.
3. **Non-participant non-admin is refused.** `GET_REPLAY` for a hand the requester
   didn't play, non-admin → `ERROR { code:"not_authorized" }`; no record read happens.
4. **Admin gets public-view replay.** Admin `GET_REPLAY` for a hand they didn't play →
   `REPLAY` with `seat = -1`/public projection (every `concealed` empty).
5. **Tampered record is refused.** Corrupt a byte in a record's JSONL; `GET_REPLAY` →
   `ERROR { code:"replay_unavailable" }` (the reader's checksum guard fires; no partial
   replay).
6. **`GET_HISTORY` paginates by keyset.** Seed 60 finalized hands for an account; first
   `GET_HISTORY {limit:50}` returns 50 + a `next_before_hand_id`; the follow-up returns
   the remaining 10 + `next_before_hand_id:null`. Order is `started_at_ms DESC`.
7. **Replay viewer renders through the live reducer.** Client (Playwright): feed a
   `REPLAY` frame; assert the `<game-pane>` board matches a known fixture after folding
   all `events[]`, and that stepping back one event un-applies the last (delegates the
   board correctness to the live renderer — this fixture pins the *replay dispatch*).
8. **Replay is read-only.** In the replay viewer, assert no `ACTION` is ever emitted and
   no `PROMPT` is rendered, even when the recorded events include the viewing seat's own
   turns.
9. **History row → replay round-trip.** Click a history row → client emits
   `GET_REPLAY {hand_id}` for that row's id; the returned `REPLAY` opens the viewer
   seeded to that hand.
10. **Live persistence still fills `account_id` (regression guard).** Play a scripted
    live hand with a human seat; assert the `hand_participants` row for that seat has the
    correct `account_id` (guards the existing wiring that FB-04's UI depends on).

Fixtures 1 and 2 are load-bearing for "replay shows the right thing without leaking";
fixture 5 is load-bearing for record integrity; fixture 7 is the headline "watch it
back" path.

## Open questions

1. **Match replay.** Replay a whole `match_id` (N hands) as one continuous session?
   `find_hands_by_match` already exists; the viewer would chain `REPLAY` fetches.
   Defer to v2 — v1 is one hand.
2. **Public replays config.** A `public_replays` flag to let any authed user watch any
   finished hand in public view (great for a community "hand of the day"). Default off;
   spec the flag when there's a reason to flip it.
3. **Selfplay/bot-zoo replays in the UI.** `source='selfplay'` hands are admin-only in
   v1. Surfacing bot-vs-bot replays for study (training-loop debugging) likely wants its
   own admin surface rather than the player "my games" list.
4. **Replay of a hand interrupted by a server restart / abort.** `terminal_kind='ABORTED'`
   or an in-progress (`ended_at_ms IS NULL`) row has a truncated record. The viewer
   should replay up to the truncation and show "hand aborted," not error. Pin the
   exact behavior when FB-01's hand-abort path and this spec meet.
