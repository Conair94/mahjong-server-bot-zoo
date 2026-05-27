# Spec 20 — Mid-hand late join: replay or refuse

A player who connects to a 2H+2B (or more humans) table *after* the hand has started — i.e., the table is in `phase: IN_PROGRESS` and the would-be joiner's seat is still `UNBOUND` because nobody ever attached to it — currently completes the `ATTACH` successfully, receives an `ATTACHED` snapshot that is the **pre-hand** state ([mahjong/server/registry.py](../../mahjong/server/registry.py) `TableHandle._snapshot_provider` projects from `self._initial_state`, which was set at `TableHandle.__init__` time and never updated after `mgr.run_hand` started), and receives **no event replay** (the seat's ring buffer in [mahjong/sessions/mux.py](../../mahjong/sessions/mux.py) `SeatSession` was never populated because the session was UNBOUND — the manager's `fanout_event` only buffers for LIVE or HELD seats).

The result is a player staring at the deal-time snapshot while the engine has already advanced several turns. Their UI shows stale tiles; any ACTION they send against an outdated prompt will be rejected; the experience is broken.

This spec defines the contract for this corner case: either replay the hand to the new joiner, or refuse the attach with a clear error code.

Tier-2 spec. Surfaces in [session-mux.md](session-mux.md) (the seat state machine), [multi-human-seats.md](multi-human-seats.md) (open-lobby model implies *anyone* can take an open seat at any time), and [wire-protocol.md](wire-protocol.md) (new error code if we go the refuse route). Driven by analysis after the lobby UI shipped — the lobby explicitly invites users to join any open human seat, including on tables already running.

## Goals

- **Define the wire contract for mid-hand attach to a previously-UNBOUND human seat.** Pick one of the alternatives below and pin it as the v1 behaviour.
- **Preserve the open-lobby property where reasonable.** "Any authenticated user can take any open human seat" is load-bearing for multi-human play (see [multi-human-seats.md § Open-lobby model](multi-human-seats.md)). If we have to weaken it for in-progress hands, do so explicitly and document why.
- **No silent breakage.** The current behaviour (silent stale snapshot, no replay) is the bug to fix; whichever path we pick must give the joiner either a usable view or a clear rejection.

## Non-goals

- **Reconnect by an already-bound user.** That's a different case (HELD → LIVE, replay buffer is populated, fully handled by [session-mux.md § Fixture 7](session-mux.md)).
- **Spectator joins.** SPECTATE goes through a separate code path that already starts from the *current* state via the spectator projection; not affected by this bug.
- **Configurable per-table policy.** Whichever option we pick is the project-wide default.

## The decision: replay vs. refuse

### Alternative A — refuse with `hand_in_progress`

A clean line: human seats are joinable only in `WAITING_FOR_PLAYERS` phase. Once `START_HAND` is accepted and the table enters `IN_PROGRESS`, any further `ATTACH` to an UNBOUND human seat returns:

```json
{ "kind": "ERROR", "code": "hand_in_progress",
  "message": "table 17 is already running this hand; wait for the next hand to join seat 1" }
```

Lobby UI (8.7.e+) suppresses the Join button on `IN_PROGRESS` tables; only `WAITING_FOR_PLAYERS` tables show open-seat affordances. Spectating remains available.

**Pros:** trivial to implement (one phase check in `TableHandle.attach`); no replay machinery; client-side behaviour is unambiguous. Matches the user's intuition that "you can't walk into a game already in progress and sit down."

**Cons:** at a table running long matches (`max_hands > 1`), a new player can't join until the current hand finishes. For hobby scale this is mostly fine — hands take a few minutes — but it does mean the lobby has to clearly distinguish joinable from spectate-only tables.

### Alternative B — replay the record

When a previously-UNBOUND seat is attached mid-hand, the server reads its on-disk record file ([mahjong/records/](../../mahjong/records/)), projects each non-FOOTER event through `project_event(event, seat)`, and ships them down the new connection as a flush of EVENTs after ATTACHED (same shape as the existing same-user resume path in [session-mux.md § Resume](session-mux.md)). The seat transitions UNBOUND → LIVE; from that point forward it participates normally.

**Pros:** preserves open-lobby for in-progress hands; mirrors how same-user reconnect already works (just over a different event source — disk vs. ring buffer); a player who joins on turn 14 sees turns 1–13 replay quickly and gets caught up.

**Cons:** record-replay timing has to be done before the next event would be emitted (otherwise the joiner sees turn 14, then the replay of turns 1–13, then turn 15 — out of order). That implies holding a lock on the table while reading the record and flushing pre-attach. Disk reads on the hot path are a new concern (today only the writer touches the record). Subtle: the FOOTER and HAND_END events must be filtered out; record events are richer than wire events (they include `from_hand`, claim metadata, etc.) and were already canonicalised for *recording*, not for live emission.

### Recommendation: Alternative A for v1

Refuse is the safer cut. Alternative B is on the roadmap but its mechanics (record-replay timing, projection of record vs. wire event shapes, ordering with live events) deserve their own spec. Build the lobby UX around alternative A first; layer B on top later if late-joining is actually requested.

## The schema / interface (Alternative A)

### Server change

`TableHandle.attach` in [mahjong/server/registry.py](../../mahjong/server/registry.py) adds one check before delegating to `_sessions.attach`:

```python
async def attach(self, conn, *, identity, seat):
    if not self.is_human_seat(seat):
        await _err(conn, "seat_not_yours")
        return False
    # NEW: refuse joining a previously-UNBOUND seat once the hand is running.
    if self._hand_task is not None and self._sessions.seat(seat).state is SeatState.UNBOUND:
        await _err(conn, "hand_in_progress",
                   message=f"table {self._table_id} is mid-hand; wait for the next hand")
        return False
    # ... existing attach flow unchanged
```

The `_hand_task is not None` check is the same one `start_hand` uses for `hand_already_started`; here it gates ATTACH. The UNBOUND check distinguishes new joiners from reconnects (HELD seats with the same user_id are handled by the existing `_resume` path in session-mux, untouched).

### Wire-protocol amendments

[wire-protocol.md](wire-protocol.md) `Error codes` table gains:

| Code               | Direction | When                                          | Client guidance                     |
| ------------------ | --------- | --------------------------------------------- | ----------------------------------- |
| `hand_in_progress` | server    | `ATTACH` to a previously-unbound human seat at a table whose hand has already started | Wait for the next hand, or SPECTATE the current hand. |

### Lobby UI change

`mahjong/web/static/app.js` `<lobby-view>`'s per-seat row currently shows a `[ Join ]` button next to every open human seat. New rule: if `table.phase === "IN_PROGRESS"`, suppress the Join button for any open human seat on that table; show "(hand in progress)" instead. A `[ Spectate ]` button next to the table row sends SPECTATE.

Note: this is exactly the affordance the spec recommends in [multi-human-seats.md § Phase transitions](multi-human-seats.md) — `WAITING_FOR_PLAYERS` tables are *joinable*, `IN_PROGRESS` ones are *spectatable*. The current lobby (8.7.e+) doesn't make this distinction.

## Worked example

Alice + Bob create a 2H+2B table and start the hand. Bob's seat ends up HELD (his network blip) and the hand pauses on a claim window waiting for him. Charlie opens the lobby, sees the table, the lobby shows:

```
Table 17 · mcr-2006 · in progress · hand 1
  Seat 0: human · alice (occupied)
  Seat 1: human · bob (occupied — held)
  Seat 2: bot · canned-pass
  Seat 3: bot · canned-pass
  (hand in progress — [ Spectate ])
```

Charlie cannot join (no Join button visible). If they click Spectate, they see the public projection.

Once the hand ends (either Bob reconnects + the hand finishes, or his seat hold expires and AutoPassAdapter finishes the hand), the table phase returns to `WAITING_FOR_PLAYERS` for the next hand; Charlie's lobby auto-refresh (2s) sees the transition and the Join buttons re-appear. Note: in `max_hands=1` mode the table is closed instead, and disappears from the lobby entirely.

If Charlie tries to ATTACH directly via the wire (bypassing the UI guard) during `IN_PROGRESS`:

```
Charlie → ATTACH { table_id: 17, seat: 2 }
Server → ERROR { code: "seat_not_yours" }       # seat 2 is a bot, not human

Charlie → ATTACH { table_id: 17, seat: 1 }      # seat 1 is human, but Bob's HELD there
Server → ERROR { code: "seat_not_yours" }       # existing path: HELD + different user → not_yours

(After Bob's seat eventually expires to UNBOUND but the hand is still running:)
Charlie → ATTACH { table_id: 17, seat: 1 }
Server → ERROR { code: "hand_in_progress",
                 message: "table 17 is mid-hand; wait for the next hand" }
```

## Verification fixtures

1. **ATTACH to UNBOUND human seat on `WAITING_FOR_PLAYERS` table → ATTACHED.**  Regression: lobby joins must still work pre-hand.  This is fixture 8 from multi-human-seats.md; carry it forward unchanged.
2. **ATTACH to UNBOUND human seat on `IN_PROGRESS` table → `hand_in_progress`.**  The load-bearing assertion.  Drive: alice + bob attach, alice issues START_HAND, the hand runs; bob's seat hold then expires (no reconnect) → seat 1 transitions UNBOUND.  Charlie attempts ATTACH seat 1 → `hand_in_progress`.
3. **ATTACH to HELD seat by same user → ATTACHED (resume path unchanged).**  Bob drops mid-hand; before the hold expires, Bob reconnects with the same account → re-ATTACHED with replayed events.  This must NOT trigger `hand_in_progress`; the new check only fires when the seat is UNBOUND.
4. **SPECTATE on `IN_PROGRESS` table still works.**  Charlie sends SPECTATE; server returns SPECTATING with the public projection; subsequent EVENTs flow to Charlie's spectator subscription.  No change from existing behaviour.
5. **Lobby Join button suppressed on `IN_PROGRESS` tables.**  Web-side: a TABLE_LIST entry with `phase: "IN_PROGRESS"` renders no `[ Join ]` buttons in `<lobby-view>`; a `[ Spectate ]` button appears next to the table row.  (Cannot be browser-verified here; pin with a Lit render unit test against a fixture TABLE_LIST.)
6. **Between-hand attach (`max_hands > 1`).**  Setup: a `max_hands=2` table, hand 0 completes, `begin_next_hand` rotates dealer and table goes back to `WAITING_FOR_PLAYERS`.  Charlie attempts ATTACH at that moment → ATTACHED (the hand task may still be alive between hands; the check must gate on the *phase* not just `_hand_task is not None`).  See open question below.

## Open questions

- **`_hand_task` vs. `phase` gating.**  `_hand_task is not None` is true for the entire lifetime of `_run_hand_loop`, including the between-hand pause (`between_hand_pause_seconds`).  Multi-hand tables go `WAITING_FOR_PLAYERS → IN_PROGRESS → WAITING_FOR_PLAYERS → IN_PROGRESS → ...`.  The check should be `summary().phase == "IN_PROGRESS"`, not `_hand_task is not None`.  Working answer: gate on `phase`.  Pin with fixture 6.
- **What about new human seats added between hands (i.e., `max_hands > 1`, hand 1 finished, hand 2 hasn't started)?**  Working answer: WAITING_FOR_PLAYERS means joinable, regardless of whether the table is brand-new or between-hands. The next `START_HAND` will pick up the new seat composition.
- **Should we record a *reason* on the now-aborted ATTACH attempt for debugging?**  Working answer: server logs the rejection at info level with `(table_id, seat, would_be_user_id)`. Not surfaced to the client beyond the error code + message.
- **Alternative-B feasibility.**  Worth a follow-up spec when (a) someone actually requests late-joining mid-hand and (b) record-replay timing concerns can be resolved (probably with a per-table "replay lock" that pauses live event emission while the new joiner is being caught up). Not in v1.

## Cross-spec impact

- [multi-human-seats.md](multi-human-seats.md) — § Open questions ("What happens if a human drops between ATTACH and START_HAND?") needs a sibling note: "What happens if a *new* human tries to attach mid-hand?" → resolved here.
- [session-mux.md](session-mux.md) — no state-machine change; the new check is at the TableHandle level, *before* delegating to session-mux's attach.  Add a one-paragraph note in § Attach paths describing the new gating.
- [wire-protocol.md](wire-protocol.md) — new error code `hand_in_progress`.  Add to the codec's `KNOWN_KINDS`-equivalent error registry and to the round-trip fixture list.
- [server-lifecycle.md](server-lifecycle.md) — no change.  Lifecycle / drain semantics carry over: a draining table refuses both ATTACH and START_HAND already.
- [tests/wire/test_codec.py](../../tests/wire/test_codec.py) — add a round-trip fixture for an `ERROR` frame with `code: "hand_in_progress"` so the KNOWN_KINDS coverage check stays green.
