# Spec 11 — Session multiplexer

The session multiplexer (session-mux) is the in-process state machine that binds a WebSocket connection to a seat at a table. It owns the lifecycle of an attached client: graceful attach, graceful detach, ungraceful drop, reconnect within the seat-hold window, and forced replacement when that window expires.

Builds on [wire-protocol.md](wire-protocol.md) (the message surface session-mux drives) and [seat-port.md](seat-port.md) (the `SeatAdapter` interface session-mux implements toward the table manager). Consumed by [auth.md](auth.md) (token validation on attach), [persistence-api.md](persistence-api.md) (session token records), and [server-lifecycle.md](server-lifecycle.md) (drain-on-shutdown).

**Status:** draft, pre-S2 implementation. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md).

## Goals

- **A connection drop is not a seat drop.** A connected human's WebSocket can flap (Wi-Fi reset, laptop sleep, brief Tailscale renegotiation) without their seat being released. The seat is held for a configurable window; reconnects within it resume the game; reconnects past it find the seat replaced by auto-pass.
- **One adapter, one seat, one connection at a time.** The session-mux produces exactly one `HumanAdapter` per bound seat. Whether that adapter currently has a live socket behind it is opaque to the table manager. The table manager's `await adapter.decide(...)` either returns (live, or buffered-then-delivered after reconnect), times out (no live socket within the prompt deadline), or raises `SeatError` (window expired before reconnect).
- **Replay-after-reconnect is exact.** A client that reconnects within the seat-hold window sees every `EVENT` it missed, in order, before any new traffic — driven by a per-seat ring buffer that session-mux owns.
- **No silent loss of pending prompts.** If the table manager issued a `PROMPT` to a seat whose connection dropped, the prompt survives across the reconnect (or escalates to the deadline-driven default if no reconnect arrives in time).
- **All timing is server-authoritative.** The seat-hold deadline, the prompt deadline, and the heartbeat budget are server clocks. Clients see them but don't enforce them. A misbehaving client cannot extend its own grace window.
- **Spectators are first-class but separately tracked.** A spectator subscription is *not* a seat. It has its own collection (the spectator set), its own privacy projection (public-only events), and its own lifecycle (drop releases immediately, no seat-hold, no ring buffer, no replay). Spectator bookkeeping shares the session-mux module because it's the same concern (who is listening to this table) but uses a different data structure than player seat state.

## Non-goals

- **Not transport.** WebSocket frame handling, framing-error close codes, ping/pong — those are [wire-protocol.md](wire-protocol.md)'s job. Session-mux operates over an abstract "outbound message channel" and "inbound message stream" that the WebSocket layer provides.
- **Not authorization.** Whether a given `user_id` is allowed to attach to a given seat at a given table is [auth.md](auth.md)'s decision (and the table manager's, for seat ownership). Session-mux is told yes/no; it never checks credentials itself.
- **Not multi-table state.** Session-mux holds per-seat state, not table-level state. The table list, the orchestration of starting a new hand, the storage of completed hands — those are [server-lifecycle.md](server-lifecycle.md) and [persistence-api.md](persistence-api.md).
- **Not the table manager's lock.** The table manager already owns the asyncio orchestration of a hand ([seat-port.md § Lifecycle and concurrency model](seat-port.md)). Session-mux fits *behind* one of its `SeatAdapter` slots; it doesn't reach into the manager's coroutines.

## Architectural position

```text
   WebSocket layer (websockets library)        <-- frames, ping/pong, TLS
            │
            v
   wire-protocol codec                         <-- JSON ↔ Python objects
            │
            v
   ┌─────────────────────────────────────┐
   │   Session multiplexer (this spec)    │    <-- per table: seat sessions + spectators
   │                                      │       seats own: ring buffer, hold timer,
   │   ┌────────────────────────────┐     │                  pending prompt slot
   │   │ HumanAdapter (per seat)    │ <───┼─── implements SeatAdapter
   │   └────────────────────────────┘     │
   │   ┌────────────────────────────┐     │
   │   │ Spectator set (per table)  │     │       spectators are stateless
   │   └────────────────────────────┘     │       (no buffer / timer / prompt)
   └─────────────────────────────────────┘
            │
            v
   Table manager (one TableManager per table)  <-- runs the hand
            │
            v
   Rules engine                                <-- pure functions
```

Two facing surfaces:

- **Toward the wire:** session-mux consumes inbound wire messages (`ACTION`, `DETACH`, `RESUME`, etc.) and emits outbound wire messages (`EVENT`, `PROMPT`, `DETACH`, etc.). The translation to/from JSON is the codec's job; session-mux works in typed Python objects.
- **Toward the table manager:** session-mux exposes a `HumanAdapter` per bound seat — a `SeatAdapter` Protocol implementation. The table manager calls `seated`, `observe`, `decide`, `left` on this adapter; session-mux drives those calls into outbound wire messages and parks the coroutines waiting for inbound responses.

Both surfaces are asynchronous. Session-mux lives in the same asyncio event loop as the rest of the server ([seat-port.md § Lifecycle and concurrency model](seat-port.md) — "one asyncio event loop owns the whole server"). It uses `asyncio.Event` / `asyncio.Future` for the cross-surface waiting; it does not use threads or queues that escape the loop.

## Seat state machine

Each bound seat has exactly one session-mux state. States and transitions:

```text
             attach (auth ok, seat free)
   UNBOUND ──────────────────────────────► LIVE
      ▲                                      │
      │                                      │ socket drops
      │                                      ▼
      │       hold timer expires            HELD
      │ ◄──────────────────────────────      │
      │                                      │ resume (token ok, within window)
      │                                      ▼
      │       graceful detach              LIVE
      └──────────────────────────────  (or back to UNBOUND on detach)
```

States:

- **`UNBOUND`** — no seat held; no resources. The seat is open at the table (or held by another user, or replaced by auto-pass after a strike — those are the *table manager's* states, not session-mux's).
- **`LIVE`** — seat held, WebSocket attached, outbound channel open. `EVENT`s are sent in real time; `PROMPT`s are awaited and the response is whatever `ACTION` the client returns.
- **`HELD`** — seat held, WebSocket gone. The hold timer is running. Inbound `EVENT`s queue in the ring buffer; any outstanding `PROMPT` is parked; the table manager's `await adapter.decide(...)` is still pending. Default action fires on prompt deadline.

Transitions and their triggers:

| From | To | Trigger | Side effects |
| --- | --- | --- | --- |
| `UNBOUND` | `LIVE` | client `ATTACH` accepted | Allocate ring buffer; create `HumanAdapter`; table manager seats it. |
| `LIVE` | `LIVE` | (idempotent on already-attached) | None. |
| `LIVE` | `HELD` | WebSocket dropped (TCP close, ping timeout, framing error) | Start hold timer (`MAHJONG_SEAT_HOLD_SECONDS`); ring buffer keeps growing. |
| `LIVE` | `UNBOUND` | client `DETACH` `reason: "leaving"` (graceful) | Stop hold timer (none running); seat released to table manager; ring buffer freed. |
| `HELD` | `LIVE` | new WebSocket presents a valid `RESUME` token bound to this seat | Cancel hold timer; replay ring buffer; re-issue outstanding `PROMPT` if any. |
| `HELD` | `UNBOUND` | hold timer fires | `HumanAdapter` raises `SeatError(reason="seat_hold_expired")` if a prompt was pending; table manager substitutes `AutoPassAdapter` per [seat-port.md § Error model](seat-port.md); ring buffer freed. |
| `HELD` | `UNBOUND` | hand ends while connection still gone | `HumanAdapter.left()` is called by table manager; same teardown. |

States in this machine apply to a single `(user_id, table_id, seat)` triple. Two attachments under the same user but different `(table, seat)` are independent state machines. Two attempts to bind the *same* `(table, seat)` from different connections are mutually exclusive — see "Conflict resolution" below.

### Why `HELD` and not just "live socket required"

The naive design — drop the seat the moment the WebSocket closes — fails the "Wi-Fi flap during a hand" case that motivates the whole feature. The seat-hold pattern is borrowed from voice/video conferencing systems (Jitsi, Zoom) where every reconnect would otherwise be a re-join with lost context. The cost is the ring buffer memory and the small bookkeeping for the hold timer; for a 4-seat hand at <1KB per event and 256-event buffers, that's under 16 KB per table at worst.

## The HumanAdapter

The `SeatAdapter` implementation produced for each bound seat. From the table manager's side, this is just another adapter — same Protocol as `BotRunnerAdapter`, `CannedAdapter`, etc. ([seat-port.md § The interface](seat-port.md)).

```python
class HumanAdapter:                       # implements SeatAdapter

    identity: SeatIdentity                # {"kind": "human", "user_id": ..., "display": ...}

    async def seated(self, ctx: SeatContext) -> None:
        # Send ATTACHED to the client (snapshot + resume_buffer_size).
        # No client wait here; if the client is HELD we ATTACHED on next resume.
        ...

    async def observe(self, event: RecordEvent, view: SeatView) -> None:
        # Project event to the seat's wire shape.
        # If LIVE: send EVENT immediately.
        # If HELD: append to ring buffer.
        # Either way, return quickly (<50ms; this is not a synchronization point).
        ...

    async def decide(self, prompt: Prompt) -> Action:
        # Park a future. Send PROMPT if LIVE.
        # Schedule a timer for prompt.deadline → default_action.
        # On client ACTION (whenever it arrives, LIVE or after a reconnect),
        # validate against prompt.legal_actions and resolve the future.
        # On seat-hold-expired (still HELD when its timer fires), raise SeatError.
        # On prompt-deadline-expired, resolve with prompt.default_action.
        ...

    async def left(self, reason: LeaveReason) -> None:
        # If LIVE: send DETACH to client.
        # Always: free ring buffer, cancel timers, mark state UNBOUND.
        ...
```

The four invariants the implementation must hold:

1. **One outstanding prompt at a time.** A `decide()` call must not start its work if a previous prompt's future is not yet resolved. (The table manager guarantees this by its own coroutine ordering — see [seat-port.md](seat-port.md). Session-mux assumes it; in debug builds, asserts it.)
2. **Ring buffer order matches `observe` order.** The table manager calls `observe` strictly in record-event order. The buffer preserves that order; replays reproduce it.
3. **No event lost in the LIVE→HELD edge.** If `observe` is called the same async tick as the WebSocket drop, the event still ends up in the ring buffer. The transition happens atomically before any further `observe` returns.
4. **`decide` always resolves.** Either via client `ACTION`, prompt deadline (default action), or seat-hold expiry (`SeatError`). The coroutine never leaks.

## Ring buffer

Per-seat circular buffer of projected wire-`EVENT` payloads.

- **Capacity.** Default 256 events. Sized to comfortably hold one full hand (an MCR hand has roughly 100–150 wire-visible events to a participating seat). Configurable via `MAHJONG_RESUME_BUFFER_SIZE`.
- **Overflow policy.** Oldest event evicted. On `RESUME`, if the client's last-seen `event.seq` is older than the oldest event in the buffer (i.e., we evicted past the client's resume point), the server sends `ATTACHED` with a fresh `snapshot` and `resume_buffer_size = 0`. The client treats it as a fresh attach.
- **Reset.** On `LIVE → UNBOUND` and on hand end. Each hand starts with an empty buffer.
- **What's stored.** The projected wire shape, not the raw `RecordEvent`. Projection happens once at `observe` time; the buffer is replay-cheap.

The "buffer overflow → fresh snapshot" policy is the only place session-mux gives up on perfect event replay. The alternative — unbounded buffer — opens a DoS vector (a disconnected client whose seat is held indefinitely could accumulate memory). Bounded buffer + fall-back-to-snapshot is the bounded-memory variant.

## Pending prompt across reconnect

Holding the prompt across a drop is what makes "reconnect during your turn" work.

The mechanic:

1. Table manager calls `await adapter.decide(prompt)`. Adapter is `LIVE`. The wire layer sends `PROMPT` and the adapter awaits a future.
2. WebSocket drops. State transitions `LIVE → HELD`. The future is *not* resolved; the deadline timer keeps running on the server's clock.
3. One of three resolutions follows:
   - **3a — Client reconnects in time:** the state transitions `HELD → LIVE`. The adapter checks for an outstanding prompt and re-sends the same `PROMPT` (same `prompt_id`; outer `seq` is a fresh one for the new connection). Client responds; future resolves.
   - **3b — Prompt deadline fires while HELD:** the future resolves with `prompt.default_action`. The table manager applies it. When the seat-hold timer eventually fires, the state goes `HELD → UNBOUND` and the seat is replaced. The default action is recorded as if the client had sent it ("client" being "the session-mux on behalf of the absent client").
   - **3c — Seat-hold timer fires while a prompt is still outstanding and not yet defaulted:** the future raises `SeatError`. The table manager records the seat as failed; substitutes `AutoPassAdapter`.

Case 3b is subtle: the default action *can* fire even when a reconnect would have arrived seconds later. This is intentional — the prompt deadline is the contract with the rest of the table ("everyone else's clocks shouldn't wait for one player"). The seat-hold window is the contract with the human ("your seat won't get yanked the moment your Wi-Fi blips"). They're different windows for different reasons.

## Conflict resolution

What happens when a second connection tries to bind to a seat that already has one:

- **Same user, seat is `LIVE` on connection A.** Connection B sends `ATTACH` for the same `(table, seat)`. Server *takes over*: connection A is sent `DETACH { reason: "replaced_by_new_session" }`, its WebSocket closes (1000), and connection B becomes `LIVE`. This handles the "I left my laptop running and now I'm on my phone" case. The ring buffer is preserved across the takeover — there's nothing to replay because there's no gap, but the buffer continues filling for any *future* drop on connection B.
- **Same user, seat is `HELD`.** Connection B's `ATTACH` is treated as a `RESUME` shortcut. State transitions `HELD → LIVE` on connection B. Same replay semantics.
- **Different user, seat is `LIVE` or `HELD`.** Connection B's `ATTACH` is rejected with `ERROR { code: "seat_not_yours" }` (if it's held for a specific user) or `seat_occupied` (if someone else legitimately holds it). No state change.

Conflict resolution is per `(user_id, table_id, seat)`, not per connection. Reasonable because the wire-protocol's auth phase happens before attach — by the time session-mux is involved, the `user_id` is known.

## Spectator handling

Spectators are tracked alongside seat state but in a separate structure. Per table:

```python
class TableSessions:
    seats: dict[int, SeatSession]              # seat_index -> SeatSession (LIVE/HELD/UNBOUND state)
    spectators: dict[ConnectionId, Spectator]  # arbitrary cardinality, bounded by config
```

A `Spectator` carries:

- `user_id` — the authenticated user behind this subscription (anonymous spectating is not supported in v1; see [auth.md](auth.md)).
- `connection_id` — the live WebSocket handle.
- No ring buffer, no hold timer, no pending-prompt future. Spectators are stateless in session-mux beyond identity + socket.

### Lifecycle

| Event | Action |
| --- | --- |
| `SPECTATE` accepted | Allocate a `Spectator`, add to `spectators` map. Send `SPECTATING` with current public snapshot and `spectator_count`. |
| Outbound event ready | Iterate `spectators.values()`, project the record event with the public-view projection (`project(event, seat=None)`), send `EVENT` to each. |
| Spectator WebSocket drops | Remove entry immediately. No state to preserve. No notification to the table manager (the table manager has no spectator concept; spectators live entirely in session-mux + wire). |
| Spectator sends `STOP_SPECTATING` | Same as drop, plus send `DETACHED` ack before close. |
| `SPECTATE` when at `MAHJONG_MAX_SPECTATORS_PER_TABLE` (default 32) | Reject with `ERROR { code: "spectator_limit_reached" }`. |
| Table closes | All spectators receive `DETACH` with `reason: "table_closed"`; their entries are cleared. |
| Hand ends | All spectators receive `HAND_END`, then continue subscribed; the inter-hand reset is transparent. Spectators stay subscribed across hands within the same table, unlike players whose `HumanAdapter` is recreated per hand. |
| Server `SIGTERM` | All spectators receive `DETACH { reason: "server_shutdown" }`; their WebSockets close cleanly. |

### Why spectators stay subscribed across hands while players don't

A player's `HumanAdapter` is bound for the duration of one hand — that's the table manager's lifecycle ([seat-port.md § Lifecycle](seat-port.md)). Between hands the seat is briefly `UNBOUND`; the wire surfaces this with a `DETACH { reason: "hand_ended" }` and a fresh `ATTACHED` for the new hand.

A spectator has no such per-hand contract. They subscribed to a *table*, not a hand. Session-mux carries them across the inter-hand boundary; they see `HAND_END` then `EVENT`s of the new hand's deal. From the spectator's perspective the stream is continuous.

This asymmetry is the reason spectators live in session-mux (per-table) rather than in some "current hand observers" set inside the table manager (per-hand).

### Privacy: public projection

Every spectator `EVENT` is the result of applying the **public projection** of the underlying record event — the "no-seat" view that yields only public-information fields. This is `project(state, seat=None)` (and the matching `project_event(event, seat=None)` derived from it).

> **Prerequisite — state-schema amendment.** The existing [state-schema.md § Per-seat projection](state-schema.md) (`project(state: GameState, seat: int) -> SeatView`) does not yet admit `seat=None` in its signature, though the doc's intro already names the spectator view as one of the targets of projection. A small additive amendment to state-schema.md broadens the signature to `seat: int | None` and pins the public-view field-by-field rule (empty `concealed`, no own-draw `tile`, concealed-meld tile elided, etc.). This amendment lands as the *first sub-step* of impl step 7.1, before any wire-protocol or session-mux code. Tracked in [s2-s3-plan.md §4 Layer 7](../s2-s3-plan.md).

Concrete consequences of the public projection:

- A `DRAW` event sent to a spectator carries no `tile` field (the drawer's draw is private).
- A `DISCARD` event carries the discarded tile (public).
- Concealed `GANG` carries no tile-identity field (public information is "seat N declared a concealed gang", not which tile).
- Exposed `PENG` / `CHI` / `GANG` carry all three (or four) tile identities (public).
- `HAND_END` carries the full revealed final hand of the winning seat (public at hand end).

The exact field-by-field projection rule belongs to [state-schema.md § Per-seat projection](state-schema.md). This spec just commits to using the existing `project(event, seat=None)` path, no spectator-specific projection logic.

### Scaling note

A table with 32 spectators serialises every event 32 times. That's negligible at our scale (an MCR hand emits maybe 200 events; 6400 sends; under a second of work). If a busy table ever needed to scale further, the natural next step is server-side fan-out of pre-projected frames (project once, serialise once, send N times). v1 does the simple per-spectator loop.

## Server lifecycle interaction

[server-lifecycle.md](server-lifecycle.md) defines startup and shutdown; session-mux participates as follows:

- **Startup.** No persistent state to restore. Active sessions from before a restart are dropped; clients reconnect (with token from SQLite). This matches the decision in [s2-s3-plan.md §10.9](../s2-s3-plan.md) — in-memory connection state only.
- **Graceful shutdown (`SIGTERM`).** The server enters drain mode:
  1. New `ATTACH` requests are rejected with `ERROR { code: "shutting_down" }` (a new code added to [wire-protocol.md](wire-protocol.md)'s table during impl-step 7.3).
  2. Every `LIVE` session receives a `DETACH { reason: "server_shutdown" }` followed by a clean WebSocket close.
  3. Every `HELD` session has its hold timer cancelled; outstanding prompts default; the table manager runs its own drain ([seat-port.md § Lifecycle](seat-port.md)) which finishes the current turn and writes the record.
  4. Session-mux waits up to a configurable drain timeout (default 30s) for the table manager to finish, then exits.

Drain order matters: the table manager's own shutdown reads from session-mux to deliver the last events of the hand. Session-mux must outlive the table manager's drain. Implementation step 8.5 wires this; the spec just commits to the order.

## Error handling

Session-mux is a state machine, not a hot path. Errors are exhaustively enumerated:

| Error | When | Effect |
| --- | --- | --- |
| Client sends action outside `legal_actions` | Inbound `ACTION` not in current prompt's set | Wire `ERROR { code: "illegal_action" }`; strike counter +1 ([seat-port.md § Error model](seat-port.md)). Prompt stays outstanding. |
| Client sends `ACTION` with no prompt | Inbound `ACTION` while not awaiting | `ERROR { code: "no_outstanding_prompt" }`. State unchanged. |
| Client sends `ACTION` with stale `prompt_id` | Inbound `ACTION` for a prompt that's been resolved/cancelled | `ERROR { code: "stale_action" }`. State unchanged. |
| Client sends malformed inbound | Codec parse failure | Wire layer's job; session-mux never sees it. |
| Outbound send fails (socket-level) | WebSocket has died | Transition `LIVE → HELD` immediately. Subsequent outbound goes to the ring buffer or is dropped per buffer policy. |
| Table manager raises in `seated`/`observe` | Bug in table manager / engine | Logged; session-mux re-raises into the table manager's coroutine. Adapter state set to `UNBOUND`. Client sent `DETACH { reason: "internal_error" }`. |
| Hold timer fires with pending prompt | Reconnect didn't arrive in time | Pending prompt's future raises `SeatError("seat_hold_expired")`. Strike counter incremented; table manager's strike policy handles the seat-replacement decision. |

Session-mux itself does *not* implement the strike budget. That's [seat-port.md § Error model](seat-port.md)'s job, enforced by the table manager. Session-mux just reports the errors and lets the manager decide whether to keep this adapter or substitute auto-pass.

## Timers and clocks

Three independent timers per `(seat, prompt)`:

- **Seat-hold timer.** Per state transition into `HELD`. Duration: `MAHJONG_SEAT_HOLD_SECONDS` (default 60). Fires once. Cancelled on `RESUME` or hand end.
- **Prompt deadline timer.** Per `decide()` call. Duration: `prompt.deadline - now`. Fires once. Independent of seat-hold (a prompt can default while still `LIVE`, or while `HELD`).
- **Heartbeat send timer.** Per `LIVE` state. Duration: `MAHJONG_HEARTBEAT_INTERVAL_SECONDS` (default 30). Re-armed each time an outbound message is sent. Drives `HEARTBEAT` send.

All three use `asyncio.get_event_loop().call_later`. None of them use wall-clock comparisons inside their handler — they use the deadline at scheduling time. Wall-clock drift inside the loop is not a concern at this scale.

A scheduled timer that fires after its referent has been resolved (e.g. seat-hold timer fires after a graceful `DETACH` from the same seat — possible if the cancel and the fire race) is a no-op: every handler checks the current state on entry and short-circuits if the state has moved on. Idempotency over correctness.

## Interaction with multi-table

[s2-s3-plan.md §10.3](../s2-s3-plan.md) pins multi-table as "N independent `TableManager` instances; server holds `{table_id: TableManager}`". Session-mux is *table-scoped*: each `TableManager` has its own session-mux holding state for that table's four seats. Cross-table state doesn't exist; the only shared resource is the WebSocket connection itself (a connection can only be attached to one seat at one table at a time).

This means a single user attaching to table A, then table B, gets two independent `HumanAdapter`s in two independent session-mux instances. The wire connection is the same; the multiplexing logic that routes inbound `ACTION`s to the right adapter lives at the wire-protocol codec layer (the codec sees `(table_id, seat)` on every relevant inbound message and routes accordingly).

## Alternatives considered

- **Per-connection seat state instead of per-`(user, table, seat)` state.** Simpler in code: every reconnect is a fresh seat-bind. Rejected: it makes reconnect mid-hand effectively impossible (no continuity of identity from the table manager's perspective; the manager would see seat A leave and a new seat A join, and the strike counter would reset). The per-tuple model preserves identity across socket changes.
- **No ring buffer; on reconnect always re-send a fresh snapshot.** Simpler. Rejected: a fresh snapshot is a `SeatView`, which loses event-ordering and timing fidelity. Some UI affordances (last-discard highlight, recent-meld animation) need event order. Re-sending the buffer is cheap and preserves these.
- **Unbounded ring buffer.** Avoids the "overflow → fresh snapshot" code path. Rejected: DoS vector (a held seat could accumulate megabytes if events kept happening — though they can't, since a held seat is a stalled hand). The bound is cheap insurance.
- **Implement the hold timer in the table manager, not session-mux.** Would couple the timer to the hand lifecycle. Rejected: the hold timer must survive *the seat transitioning from `LIVE` to `HELD`*, which the table manager doesn't observe (the table manager only observes prompt-level outcomes). Keeping the timer in session-mux puts it at the layer that owns the connection state.
- **`DETACH` could be a single direction (server → client only).** A client could simply close the WebSocket to leave. Rejected: a server-side `DETACH` ack lets the server free resources before the close, and a client-initiated `DETACH` with a `reason` is more diagnostic than a bare close. Cost of supporting both directions is one extra message in the wire spec.
- **Replace the seat *immediately* on disconnect when there's no active prompt.** Argument: the seat isn't doing anything; no reason to hold it. Rejected: the user expects the seat to be theirs across the hand, even during their idle turns. The seat-hold window is the contract. Holding through idle is cheaper than the alternative ("I went to the bathroom and lost my seat").

## Verification fixtures

These are the acceptance criteria for impl step 7.3 (session-mux) and step 7.4 (HumanAdapter).

1. **State machine: every transition fires exactly once per trigger.** Parameterised test over the 7 transitions in the §"Seat state machine" table. Each test sets up the source state, fires the trigger, asserts the target state and the side effects.

2. **Buffered events replay in order on reconnect.** Setup: bind seat, send 10 `EVENT`s (5 live + drop + 5 buffered), reconnect. Assert: client receives the 5 buffered events in order with `resume_buffer_size = 5`, *then* any new traffic.

3. **Ring-buffer overflow forces a fresh snapshot.** Setup: `MAHJONG_RESUME_BUFFER_SIZE = 4`, bind seat, send 10 `EVENT`s while held, reconnect. Assert: `ATTACHED.resume_buffer_size = 0`, `snapshot` matches current `project(state, seat)`.

4. **Pending prompt survives reconnect.** Setup: bind seat, `decide()` enqueues prompt, send `PROMPT`, drop, reconnect within window. Assert: server re-sends same `prompt_id` after replay; client `ACTION` resolves the original `decide()` future with the chosen action.

5. **Pending prompt defaults at deadline even while HELD.** Setup: bind seat, `decide(prompt)` with deadline 1s, drop, wait 2s. Assert: `decide()` resolves to `prompt.default_action`; no `SeatError` raised yet; seat-hold timer (60s default) is still running.

6. **Seat-hold expiry raises `SeatError` with no pending prompt.** Setup: `MAHJONG_SEAT_HOLD_SECONDS = 1`, bind seat, drop, wait 2s. Assert: `HumanAdapter.decide()` (called by the table manager after expiry) raises `SeatError(reason="seat_hold_expired")`; subsequent `RESUME` is allowed but produces `seat_not_yours` (seat replaced).

7. **Seat-hold expiry resolves a pending `decide()` as `SeatError`.** Setup: `MAHJONG_SEAT_HOLD_SECONDS = 1`, bind seat, `decide(prompt)` with deadline 10s, drop, wait 2s. Assert: `decide()` raises `SeatError`. The prompt did not default — the seat-hold expiry pre-empted the prompt deadline.

8. **Same-user takeover from a second connection.** Setup: connection A bound LIVE, connection B sends `ATTACH` for same `(user, table, seat)`. Assert: A receives `DETACH { reason: "replaced_by_new_session" }`; A's WS closes; B is `LIVE`; ring buffer continuity (events sent to A *before* takeover are not re-sent to B; events *after* go to B).

9. **Different-user rejection.** Setup: connection A (user X) is LIVE on seat 0; connection B (user Y) `ATTACH`es to same `(table, seat 0)`. Assert: B receives `ERROR { code: "seat_not_yours" }`; A is unaffected.

10. **Hand end while HELD.** Setup: bind seat, drop mid-hand, table manager finishes hand. Assert: `HumanAdapter.left()` is called with `reason="hand_ended"`; state → `UNBOUND`; subsequent `RESUME` for that hand returns `seat_not_yours`.

11. **Graceful shutdown drains LIVE and HELD seats.** Setup: 2 LIVE seats, 1 HELD seat, send `SIGTERM`. Assert: LIVE seats receive `DETACH { reason: "server_shutdown" }`; HELD seat's outstanding prompt defaults (or `SeatError`s if no prompt — either is acceptable, fixture pins which based on actual impl); table manager records the hand to disk; process exits within drain timeout.

12. **No-prompt action raises `no_outstanding_prompt`.** Setup: bind seat, no prompt outstanding, client sends `ACTION`. Assert: `ERROR { code: "no_outstanding_prompt" }`; state unchanged.

13. **Stale prompt_id raises `stale_action`.** Setup: bind seat, two prompts in succession; client answers prompt 1 with prompt 2's `prompt_id` (or vice versa). Assert: `ERROR { code: "stale_action" }`; outstanding prompt remains outstanding.

14. **Illegal action increments strike, doesn't transition state.** Setup: bind seat, prompt issued, client sends action not in `legal_actions`. Assert: `ERROR { code: "illegal_action" }`; strike counter incremented at the table manager (verified by callback); state stays `LIVE`; client can retry.

15. **Timers are cancelled on idempotent re-entry.** Setup: bind seat, drop, reconnect, drop again, reconnect — within window each time. Assert: only one hold timer was running at any moment (no leaks); ring buffer is consistent (no duplicate events).

16. **Spectator subscribe → public-projected event stream.** Setup: hand in progress, spectator sends `SPECTATE`. Assert: `SPECTATING.snapshot` is a public view (every `concealed` empty); each subsequent `EVENT` is byte-equal to `project(record_event, seat=None)`; spectator never receives a `PROMPT`.

17. **Spectator drop is immediate.** Setup: spectator attached, WebSocket dropped. Assert: entry removed from `spectators` map within one event-loop tick; no hold timer started; no notification to table manager; hand continues unaffected.

18. **Multiple spectators receive identical streams.** Setup: two spectator connections to one table, hand in progress. Assert: each receives the same sequence of inner-event payloads (outer `seq` and `t_server_ms` may differ; inner content is byte-equal).

19. **`MAHJONG_MAX_SPECTATORS_PER_TABLE` enforced.** Setup: limit = 2, three spectators try to subscribe to the same table. Assert: first two get `SPECTATING`; third gets `ERROR { code: "spectator_limit_reached" }`. Drop one of the first two → a fourth `SPECTATE` succeeds.

20. **Spectator stays subscribed across hand boundary.** Setup: spectator attached during hand N, hand ends, hand N+1 starts. Assert: spectator receives `HAND_END` then `EVENT`s of the new hand's deal without any explicit re-subscribe.

21. **Spectator served correct projection for own-draw events.** Setup: scripted hand where seat 1 draws tile `B5`. Assert: spectator's `EVENT` for that draw carries no `tile` field (or carries `tile: null` per state-schema's projection rule); player-path event for seat 1 carries the tile.

Fixture 11 is the load-bearing one for the S3 graceful-shutdown gate; fixture 4 is load-bearing for the "reconnect mid-hand" headline feature of S2; fixture 21 is load-bearing for spectator privacy (no concealed-information leak).

## Open questions

None at v1. Decisions per [s2-s3-plan.md §10](../s2-s3-plan.md). Possible v2 considerations (per-table hold-window override, replay compression for huge backlogs) live in [research-ideas.md](../research-ideas.md) if surfaced.
