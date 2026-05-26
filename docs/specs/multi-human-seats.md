# Spec 17 — Multi-human seats (Step 8.7)

The contract for a single mahjong table to host more than one authenticated human player. As of Layer 8 Step 8.6 every table hardcodes one human at seat 0 and three `CannedAdapter` bots at seats 1–3; this spec lifts that assumption so two-to-four friends can play one hand against each other (with any remaining seats filled by placeholder bots).

Builds on [wire-protocol.md](wire-protocol.md) (`CREATE_TABLE`, `ATTACH`, the new `START_HAND` admin message), [session-mux.md](session-mux.md) (the seat state machine and per-seat hold timers carry over unchanged; only the *trigger* for the hand-loop shifts), [server-lifecycle.md](server-lifecycle.md) (`TableRegistry`/`TableHandle` constructors gain a `seats` parameter), and [auth.md](auth.md) (any authenticated user can claim an open human seat; there is no per-seat ACL).

**Status:** draft, gates Step 8.7. Decisions resolved 2026-05-26 during planning session.

## Goals

- **Two-to-four humans at one table.** A table can be created with 1, 2, 3, or 4 human seats; remaining seats are filled with `CannedAdapter`-PASS placeholders. The single-human-seat case continues to work and continues to pass the existing Layer 8 regression suite.
- **Open-lobby model.** `CREATE_TABLE` declares only *roles* per seat (human vs. bot), never identities. Any authenticated user can `ATTACH` to any open human seat. Seat assignment is first-come on `ATTACH`, not pre-named on `CREATE_TABLE`.
- **Explicit hand-start gesture.** The hand loop does not auto-start on first attach. Some attached human (any of them — there is no privileged "creator" role inside the table) sends a `START_HAND` message; the server validates all human seats are LIVE, then kicks off `_run_hand_loop`. This gives a predictable, observable, "everyone is here" semantics.
- **Reuse existing seat-mux machinery.** The seat-hold, replay-buffer, prompt-resume, and `AutoPassAdapter`-substitution mechanisms from Layer 7 apply per-seat unchanged. We are *not* inventing new lifecycle states; we are letting them run on more than one seat at a time.
- **Backwards-compatible wire protocol.** Existing single-human clients (the current `mahjong/web/static/app.js`) keep working against tables they themselves created with `CREATE_TABLE` omitted entirely (the server falls back to "1 human seat 0 + 3 canned"). New clients opt into multi-human by sending the new `seats` and `START_HAND` flows.

## Non-goals

- **Pre-assigned (invite-style) seats.** The wire-protocol example previously showed a `user_id` per seat; this spec drops that field on the request path. Private/invited tables are a future feature (likely Layer 9+).
- **Real bot identities.** Bot seats remain placeholder `CannedAdapter`-PASS in v1. Selecting bots from a registry (`b_random`, `b_rule_v1`, …) is deferred to Layer 9 and the bot-runner adapter integration. The `seats[].kind = "bot"` slot is present in the schema so callers can opt in to "I want N humans + (4−N) bots," but the bot's *identity* is server-chosen.
- **Variable seat count.** Every table still has exactly four seats. "Three-player mahjong" or "two-player heads-up" are non-goals.
- **Lobby UI for table discovery.** The web client already gets `LIST_TABLES`; this spec only requires that `TABLE_LIST` correctly *expose* per-seat status. Building a fancy table-picker UI is part of the implementation step but is not protocol surface.
- **Cross-table account state.** A user can have at most one `LIVE` seat in the server at a time (re-attaching to a second table while LIVE on a first is rejected with `seat_not_yours` on the first table — see [session-mux.md § Conflict resolution](session-mux.md)). This was already the rule; this spec inherits it.
- **Server-side game-result UX after a multi-human hand.** `HAND_END` already carries the terminal payload; rendering it for N humans is purely client-side and out of scope.

## The schema / interface

### Wire-protocol amendments

Three messages change shape; one new message is added. The fixture list at the bottom of this spec enumerates the test cases that pin each.

#### `CREATE_TABLE` — request body

**Before (current spec):** seats array carried per-seat `user_id`, but the server ignored it entirely and hardcoded one human + three canned.

**After:**

```json
{
  "kind": "CREATE_TABLE",
  "ruleset": "mcr-2006",
  "seats": [
    { "kind": "human" },
    { "kind": "human" },
    { "kind": "bot" },
    { "kind": "bot" }
  ]
}
```

- `seats` (array of 4 objects, optional). If omitted, the server defaults to `[{kind:"human"}, {kind:"bot"}, {kind:"bot"}, {kind:"bot"}]` — preserving today's behavior.
- `seats[i].kind` (string, required when `seats` is present). One of `"human"` or `"bot"`. Position in the array is the seat index (0–3).
- All other fields on `seats[i]` are reserved and rejected with `ERROR { code: "framing" }` in v1. (No `user_id`, `bot_id`, `display`, etc.)

Validation rules at the server:

- Must contain exactly 4 entries when present.
- Must have at least one `"human"` seat (otherwise the table would never start — use the self-play harness for all-bot games).
- Unknown `kind` values → `framing`.

`TABLE_CREATED` is unchanged.

#### `TABLE_LIST` — response body

The `seats[]` field, which `TableSummary.to_wire()` previously left as `[]`, becomes populated:

```json
{
  "kind": "TABLE_LIST",
  "seq": 3,
  "tables": [
    {
      "table_id": 17,
      "ruleset": "mcr-2006",
      "seats": [
        { "seat": 0, "kind": "human", "occupied": true,  "user_id": "u_1" },
        { "seat": 1, "kind": "human", "occupied": false },
        { "seat": 2, "kind": "bot",   "occupied": true,  "bot_id":  "canned-pass" },
        { "seat": 3, "kind": "bot",   "occupied": true,  "bot_id":  "canned-pass" }
      ],
      "hand_index": 0,
      "phase": "WAITING_FOR_PLAYERS"
    }
  ]
}
```

- `seats[i].kind` — `"human"` or `"bot"` (matches `CREATE_TABLE.seats[i].kind`).
- `seats[i].occupied` — `true` if a seat-mux session exists in `LIVE` *or* `HELD` state for that seat. Bot seats are always `occupied: true` from the moment the table is created.
- `seats[i].user_id` — present iff `kind == "human"` and `occupied == true`. The `u_{account_id}` form.
- `seats[i].bot_id` — present iff `kind == "bot"`. Always `"canned-pass"` in v1.
- `phase` — `"WAITING_FOR_PLAYERS"` until `START_HAND` has been accepted; then `"IN_PROGRESS"`; then back to `"WAITING_FOR_PLAYERS"` between hands (if `max_hands > 1`).

The `attached` boolean from the original wire-protocol.md example is intentionally *not* exposed. `occupied` already captures the seat-claim fact; whether the socket is currently live vs. held during a brief reconnect is internal to the seat-mux state machine and irrelevant to lobby display. (If the client needs it later, it can be added; YAGNI for v1.)

#### `START_HAND` — new message

Client → server. Sent by *any* `LIVE` human at a table to request the hand loop start.

```json
{
  "kind": "START_HAND",
  "table_id": 17
}
```

Server validates:

1. The connection is attached as a human at `table_id` (otherwise `ERROR { code: "not_authorized" }`).
2. Every `kind: "human"` seat at the table is in `LIVE` state (not just `HELD`; we will not start with an unattached human). On failure: `ERROR { code: "humans_not_ready", message: "<n> human seat(s) still unoccupied" }`.
3. The table is in `WAITING_FOR_PLAYERS` phase (not already running a hand). On failure: `ERROR { code: "hand_already_started" }`.

On success: the server kicks off `_run_hand_loop` exactly as it does today on first-attach, and broadcasts `TABLE_LIST` (lazy: clients see the phase change next time they `LIST_TABLES`; we do not push). The originator does not need an acknowledgement frame — the first inbound `ATTACHED → snapshot` was already delivered, and the next visible signal is the first `EVENT` (e.g. `HEADER` then `DEAL`).

Note: in the single-human-seat case (today's behavior), `START_HAND` is *still* required. There is no auto-start exception. This keeps the state machine one-shaped instead of two-shaped. Existing single-human callers — the web client at `mahjong/web/static/app.js` — therefore need a small client-side amendment (described below).

#### `ATTACH` — semantics widening

The wire format of `ATTACH` does not change. What changes is which seats the server will accept:

- Today: only `seat == 0`; any other → `seat_not_yours`.
- After 8.7: any seat where `CREATE_TABLE.seats[i].kind == "human"` is claimable by any authenticated user. Bot seats remain rejected with `seat_not_yours`.

Two new error paths to specify (existing error codes; no new codes needed):

- Attach to a `kind == "bot"` seat → `seat_not_yours` (consistent with today's seat-1/2/3 rejection).
- Attach to an already-`LIVE` human seat held by a *different* user → `seat_occupied` (the existing same-user-takeover path from session-mux.md fixture 8 still applies for the *same* user reconnecting).

### Server-side data shapes

`TableHandle` gains one constructor parameter and one method:

```python
@dataclass(frozen=True)
class SeatComposition:
    """Per-seat declaration as parsed from CREATE_TABLE."""
    kind: Literal["human", "bot"]

class TableHandle:
    def __init__(
        self,
        *,
        # ... existing fields ...
        seats: tuple[SeatComposition, SeatComposition, SeatComposition, SeatComposition],
    ) -> None: ...

    def is_human_seat(self, seat: int) -> bool: ...

    async def start_hand(self, conn: Any) -> StartHandOutcome: ...
```

`StartHandOutcome` is the result type used by the orchestrator to translate into a wire `ERROR` or proceed:

```python
@dataclass(frozen=True)
class StartHandOutcome:
    ok: bool
    error_code: str | None    # one of: "not_authorized", "humans_not_ready", "hand_already_started"
    error_message: str | None
```

`TableRegistry.create_table_direct` gains a `seats: tuple[SeatComposition, ...] | None = None` kwarg; `None` defaults to the legacy single-human composition. The wire handler `_handle_create_table` parses `msg["seats"]` (when present) into the tuple and forwards it.

The module-level `HUMAN_SEAT = 0` constant in [mahjong/server/registry.py](mahjong/server/registry.py) is **removed**. Anywhere that today reads `seat == HUMAN_SEAT` is rewritten to call `self.is_human_seat(seat)` (or pass through the SeatComposition tuple at construction time).

### Hand-loop changes

The `_run_hand_loop` body changes in three places:

1. **Adapter list construction.** Currently builds `[human, canned_1, canned_2, canned_3]`. After 8.7 it walks the `seats` tuple, building one `HumanAdapter` per human seat (pulling the seat-mux session for that seat via `self._sessions.seat(i)`) and one `CannedAdapter` per bot seat.
2. **Identity threading.** `_run_hand_loop` no longer takes a single `human_identity` parameter. Instead, for each human seat, it consults the seat-mux session to obtain the bound `HumanIdentity` (the user who attached). For the persistence `Participant` row, the `account_id` is filled from that identity; for canned seats, `account_id` is `None` and `seat_kind` is `"canned"` (today's behavior).
3. **Hand-start trigger.** `TableHandle.attach` no longer kicks off the hand task. Instead, `TableHandle.start_hand` is the only entry point that creates `self._hand_task`. The `_start_hand_lock` continues to guarantee single-shot ignition.

### Web client (Lit, `mahjong/web/static/app.js`)

Three additive changes; nothing existing is removed:

1. **Lobby/table-picker UI** — after `AUTH_RESPONSE`, send `LIST_TABLES`; render the result with a seat-selector showing each table's open human seats. The "create new table" affordance lets the user choose the composition (e.g., a slider 1–4 humans). A "join existing table" affordance lets them pick from `TABLE_LIST` and choose an open human seat.
2. **Send `START_HAND` after the local `ATTACHED` arrives** *if* `seats[]` in `TABLE_LIST` shows all humans are now occupied. (Otherwise, render "waiting for N more players…" and re-`LIST_TABLES` on a 2-second timer until the count reaches the expected human count, then send `START_HAND`.) This means every connected human will issue `START_HAND`; the server idempotently accepts the first and returns `hand_already_started` to the rest — which the client treats as a no-op.
3. **`MAHJONG_LISTEN_ADDR` documentation** — the default stays `127.0.0.1:8400` in code (security default), but the README + the project's serve docs gain a one-liner showing how to bind to `0.0.0.0:8400` for LAN/Tailscale play. We are not changing the default. (Reasoning under "Alternatives considered" below.)

## Worked example: two friends + two bots

**Setup.** Alice and Bob both have accounts. Alice's web client connects first.

```
Alice → ws://server/socket
Server → HELLO
Alice  → AUTH_REQUEST { username: "alice", ... }
Server → AUTH_RESPONSE { account_id: 1, session_token: "...", display_name: "alice" }

Alice  → LIST_TABLES
Server → TABLE_LIST { tables: [] }

Alice  → CREATE_TABLE { seats: [{kind:"human"}, {kind:"human"}, {kind:"bot"}, {kind:"bot"}] }
Server → TABLE_CREATED { table_id: 1 }

Alice  → ATTACH { table_id: 1, seat: 0 }
Server → ATTACHED { seat: 0, snapshot: <SeatView>, resume_buffer_size: 0 }
```

Alice's UI now shows "Waiting for 1 more player." Her client polls `LIST_TABLES` every 2s.

Bob's web client connects.

```
Bob   → ws://server/socket
Server → HELLO
Bob   → AUTH_REQUEST { username: "bob", ... }
Server → AUTH_RESPONSE { account_id: 2, ... }
Bob   → LIST_TABLES
Server → TABLE_LIST { tables: [{ table_id: 1, seats: [
            { seat: 0, kind: "human", occupied: true, user_id: "u_1" },
            { seat: 1, kind: "human", occupied: false },
            { seat: 2, kind: "bot",   occupied: true, bot_id: "canned-pass" },
            { seat: 3, kind: "bot",   occupied: true, bot_id: "canned-pass" }
          ], phase: "WAITING_FOR_PLAYERS", ... }] }

Bob   → ATTACH { table_id: 1, seat: 1 }
Server → ATTACHED { seat: 1, snapshot: <SeatView>, resume_buffer_size: 0 }
```

Both clients' next `LIST_TABLES` poll shows both human seats occupied. Each client now sends `START_HAND`:

```
Alice → START_HAND { table_id: 1 }
Server → (kicks off _run_hand_loop; broadcasts EVENT HEADER, EVENT DEAL, etc.)

Bob   → START_HAND { table_id: 1 }
Server → ERROR { code: "hand_already_started" }       # to Bob; client ignores
```

The hand proceeds. Alice sees `PROMPT`s on seats 0's turns; Bob sees `PROMPT`s on seat 1's turns. Bots auto-pass. Persistence writes a `hand_index` row with `participants[0].account_id = 1`, `participants[1].account_id = 2`, `participants[2..3]` with `account_id = NULL` and `seat_kind = "canned"`.

If Bob's WebSocket drops mid-hand, the seat-hold timer (60s default) holds his seat in `HELD` state. If he reconnects with `RESUME` inside that window, the buffered EVENTs replay and his outstanding PROMPT (if any) re-fires. Past the window: his seat is replaced with `AutoPassAdapter` and the hand finishes without him. This is the existing session-mux behavior; no new code.

## Alternatives considered

- **Pre-assigned seats (invite model).** Rejected for v1 in favor of open-lobby. Pre-assigning requires the creator to know other users' `user_id`s ahead of time, which means either a separate "find friends" surface or the creator typing UUIDs. The lobby model is simpler and matches the actual flow of "people show up and join." A pre-assigned/private-table feature can layer on later by adding an optional `seats[i].user_id` field that, when present, restricts that seat to that user. This is a strict superset of the v1 schema.
- **Auto-start on full attach.** Rejected. "Hand starts the instant the last human attaches" is surprising in practice: someone clicks `ATTACH` to test their connection and the game starts before they've configured their browser, fetched a snack, etc. Explicit `START_HAND` is the predictable UX. The cost is one extra wire message; the benefit is no surprise starts.
- **A "creator" or "host" role that alone may `START_HAND`.** Rejected. We'd need to track who created the table and special-case their permissions, which adds state for no real benefit at hobby scale. "Any LIVE human may start" is the simplest rule; the server is idempotent on second-START so concurrent starts are harmless.
- **Selecting real bots in `CREATE_TABLE.seats[i]`.** Deferred to Layer 9. Pulling bot-registry integration into Layer 8 expands scope; the placeholder `CannedAdapter`-PASS is enough to verify the seat-composition machinery and ship multi-human play. The schema reserves the namespace (`kind: "bot"`) so the future addition is non-breaking — `seats[i].bot_id` becomes a request-side field then.
- **Changing `MAHJONG_LISTEN_ADDR` default to `0.0.0.0:8400`.** Considered (implementation-order's Step 8.7 stub mentioned it) and rejected. Binding to `0.0.0.0` by default surprises hobby users who didn't plan to expose their LAN. The current `127.0.0.1` default is conservative; opt-in via env var is correct. We *do* fix this in docs so the path is obvious to users who want LAN/Tailscale play.
- **Adding `attached` to `TABLE_LIST.seats[]`.** Considered (the original wire-protocol.md example had it). Dropped: `occupied` is enough for lobby display, and `attached` exposes the seat-hold internal state for no current consumer. Re-add if/when a UI needs it.
- **A new `phase: "WAITING_FOR_HAND_START"` value distinct from `WAITING_FOR_PLAYERS`.** Considered for precision; rejected. From the client's point of view "everyone is here but no hand yet" and "still waiting for people" both render the same way (a lobby), and collapsing them keeps the state machine smaller.
- **Unifying `TableHandle._run_hand_loop` with `WebOrchestrator._run_hand_loop` while we're in here.** Explicitly rejected per [project-multi-table-architecture memory](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_multi_table_architecture.md) Decision 59. The duplication is intentional; this spec touches `TableHandle` only.

## Verification fixtures this spec implies

Each fixture below is one failing test before implementation. They are the gate for Step 8.7 in [implementation-order.md](implementation-order.md).

### Schema parsing

1. **Default composition.** `CREATE_TABLE` with no `seats` field → table created with `seats == [human, bot, bot, bot]` (today's behavior preserved).
2. **All-human composition.** `CREATE_TABLE { seats: [{kind:"human"}]*4 }` → table created; four human seats; no canned adapters.
3. **2H + 2B composition.** `CREATE_TABLE { seats: [{kind:"human"},{kind:"human"},{kind:"bot"},{kind:"bot"}] }` → table created with that composition.
4. **All-bot rejection.** `CREATE_TABLE { seats: [{kind:"bot"}]*4 }` → `ERROR { code: "framing" }` (at least one human required).
5. **Wrong-length rejection.** `seats` of length 3 or 5 → `ERROR { code: "framing" }`.
6. **Unknown kind rejection.** `seats[0].kind == "alien"` → `ERROR { code: "framing" }`.
7. **Reserved-field rejection.** `seats[0]` contains `user_id` or `bot_id` → `ERROR { code: "framing" }` (v1 forbids them on the request).

### Attach widening

8. **Second human attaches to seat 1.** Alice attaches to seat 0; Bob attaches to seat 1; both `ATTACHED` succeed; their seat-mux sessions are independent.
9. **Bot seat rejects attach.** With a `2H + 2B` table, `ATTACH { seat: 2 }` → `ERROR { code: "seat_not_yours" }`.
10. **Occupied human seat rejects different user.** Alice on seat 0; Bob's `ATTACH { seat: 0 }` → `ERROR { code: "seat_occupied" }`.
11. **Same-user reconnect to held seat.** Alice on seat 0, drops, reconnects (same `user_id`); `ATTACH { seat: 0 }` succeeds via the existing same-user-takeover path. (Regression check for session-mux fixture 8 under multi-human composition.)

### `TABLE_LIST.seats[]` population

12. **Empty table snapshot.** Fresh `2H+2B` table, no attaches yet → `TABLE_LIST.seats == [ {seat:0,kind:"human",occupied:false}, {seat:1,kind:"human",occupied:false}, {seat:2,kind:"bot",occupied:true,bot_id:"canned-pass"}, {seat:3,kind:"bot",occupied:true,bot_id:"canned-pass"} ]`.
13. **Mid-fill snapshot.** With Alice attached to seat 0, the human-0 entry has `occupied:true, user_id:"u_1"`; human-1 still `occupied:false`.
14. **Phase transitions.** Before `START_HAND`: `phase == "WAITING_FOR_PLAYERS"`. After `START_HAND` succeeds: `phase == "IN_PROGRESS"`.

### `START_HAND` flow

15. **Happy path.** 2H+2B; both humans attached; either human's `START_HAND` kicks off `_run_hand_loop`; the originator receives the next `EVENT HEADER` as the wire confirmation.
16. **Premature start rejection.** 2H+2B; only Alice attached; her `START_HAND` → `ERROR { code: "humans_not_ready", message: "1 human seat(s) still unoccupied" }`.
17. **Non-human-attached rejection.** A spectator connection sends `START_HAND` → `ERROR { code: "not_authorized" }`.
18. **Double-start idempotency.** Alice's `START_HAND` succeeds; Bob's subsequent `START_HAND` for the same table → `ERROR { code: "hand_already_started" }`. The hand loop runs exactly once.

### End-to-end

19. **Two-human full hand (load-bearing — the Step 8.7 exit fixture).** Two authenticated clients; create 2H+2B table; both attach; one issues `START_HAND`; hand runs to a terminal (`EXHAUSTIVE_DRAW` is fine); persistence `hand_index` row has `participants[0..1].account_id` populated, `participants[2..3].account_id = NULL` with `seat_kind = "canned"`; the record file is replayable and per-seat projection privacy holds (Alice never sees Bob's concealed tiles in her event stream).
20. **Single-human regression.** `CREATE_TABLE` with `seats` omitted; one human attaches to seat 0; `START_HAND` succeeds; hand completes exactly as in the existing Layer 8 end-to-end fixture. The original web client (with the addition of a single `START_HAND` send) drives this path.
21. **Disconnect of one human mid-hand.** 2H+2B running; Bob drops; his seat enters HELD; Alice's prompts still arrive; if Bob reconnects inside the hold window the hand continues; if he doesn't, `AutoPassAdapter` takes seat 1 and the hand finishes. (Regression composite of session-mux fixtures 7 + 8 under multi-human.)

### Persistence

22. **`Participant` rows reflect composition.** For the fixture-19 hand, `find_hands_by_account(account_id=1)` and `find_hands_by_account(account_id=2)` both return the played hand; `account_id=3` (a nonexistent account) returns empty.

## Open questions

- **Should `START_HAND` carry a `ruleset_override` or `seed_override` field?** *Working answer: no.* The ruleset is fixed at `CREATE_TABLE`; the seed comes from the table's `seed + hand_index`. If users want a different ruleset they `CREATE_TABLE` again. Re-evaluate if Layer 10's home-rule overlays demand per-hand customization.
- **What happens if a human drops between `ATTACH` and `START_HAND`?** *Working answer:* the seat-mux puts them in HELD; their `occupied` in `TABLE_LIST.seats[]` stays `true` (they hold the seat); any other human's `START_HAND` is rejected with `humans_not_ready` because not every human is `LIVE`. They have until the seat-hold window expires; past that, the seat becomes `occupied: false` and is open for another claim. This is consistent with the seat-mux contract and needs no new code, but it should be exercised as a fixture in implementation.
- **Cross-table same-user constraint.** Today's session-mux enforces one LIVE seat per user. Does this need explicit messaging in `TABLE_LIST` (e.g., flag "your other table" with a marker)? *Working answer: not in v1.* The `seat_not_yours` error on the second attach is sufficient; cleaning up the first attachment is the user's problem.
- **Should `LIST_TABLES` push on phase/occupancy change instead of being poll-only?** *Working answer: not in v1.* 2-second polling at hobby scale is fine; pushing would require either a new server→client `TABLE_UPDATE` message or generalizing the spectator subscription to the lobby, both of which are additive future work.
- **Does the web client need to handle the `humans_not_ready` error path differently from `hand_already_started`?** *Working answer: yes — but both are treated as "do nothing locally."* The first means "we tried too early, re-poll"; the second means "someone else already started, no-op." Neither shows the user a banner; both just continue the polling loop.

## Cross-spec impact

- [wire-protocol.md](wire-protocol.md) — `CREATE_TABLE.seats[]` schema reduced to `{kind}`; `TABLE_LIST.seats[]` schema pinned (was `[]` in code); new `START_HAND` entry; new error codes `humans_not_ready` and `hand_already_started`.
- [session-mux.md](session-mux.md) — one-line addendum: the hand-loop trigger is `START_HAND` from any LIVE human seat at the table, not first-ATTACH-to-seat-0. The seat state machine itself is unchanged.
- [implementation-order.md](implementation-order.md) — Step 8.7 expands to reference these 22 fixtures in order. Step 8.8 is added for the deferred 8.5 lifecycle cleanup (separate workstream; see implementation-order.md).
- [persistence-api.md](persistence-api.md) — no schema change. `participants[i].account_id` already supports `NULL` for non-human seats; we're just exercising the multi-human path that fills more rows.
- [server-lifecycle.md](server-lifecycle.md) — no change. `TableRegistry.drain_all` semantics carry over: an in-progress multi-human hand drains the same way as a single-human one (let it finish, refuse new attaches).
