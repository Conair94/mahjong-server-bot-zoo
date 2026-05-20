# Spec 3 — Seat adapter port

The async interface every seat adapter implements. This is the **port** in the ports-and-adapters pattern committed in [server-plan.md](../server-plan.md): the rules engine and table manager have no concept of "human" vs. "bot" — they only see seats, and a seat is something that satisfies this interface.

Builds on [state-schema.md](state-schema.md) (the `SeatView` it consumes, the action grammar it emits) and [record-format.md](record-format.md) (events flow back into the record as the table manager observes adapter responses).

**Status:** draft, pre-S0.

## Goals

- **One interface, many adapters.** Human-TUI, bot-runner, canned-action (tests), spectator (read-only), self-play-driver (god-mode) are all the same shape from the table manager's perspective. New adapter types are additive — no engine or table manager changes.
- **Asyncio-native.** Adapters are async objects; the table manager `await`s them. No threads, no callbacks-with-state, no event loops in adapters. One asyncio event loop owns the whole server.
- **Timeout is a first-class outcome.** Every prompt carries a deadline; missing the deadline produces a defined action (`PASS` in claim windows, `tsumogiri` on own turn), is recorded, and never wedges the table.
- **Errors don't wedge the table either.** Adapter crashes, illegal actions, and protocol violations all resolve to "this seat is now a degraded adapter" (auto-pass / auto-discard) without taking down the hand.
- **Privacy enforced at the port.** Adapters receive `SeatView` (projected), never `GameState` (canonical). The single exception is the self-play driver, which holds *all four* seat adapters and is allowed god-mode by construction.

## Non-goals

- **Not a transport.** This spec is the in-process Python interface. How a human's WebSocket message becomes an `Action` is the human-TUI adapter's internal concern; how a subprocess's stdout becomes one is the bot-runner adapter's. The port itself doesn't know about networks.
- **Not a UI contract.** What gets rendered for a human is the TUI adapter's business. The port only delivers `SeatView`s and pulls back `Action`s.
- **No streaming actions.** One prompt → one action. Multi-stage decisions (declare riichi *then* discard, in Riichi mahjong) don't exist in MCR; if they ever do, they become multiple prompts, not a multi-stage action.

## The interface

```python
class SeatAdapter(Protocol):

    identity: SeatIdentity        # static; what goes into HEADER.seats[].identity

    async def seated(self, ctx: SeatContext) -> None:
        """Called once when the seat is bound to a table. Adapter does any
        setup (open subprocess, send initial state to client). Must return
        within ctx.seat_deadline_ms or the seat is replaced by a degraded
        adapter and the hand proceeds."""

    async def observe(self, event: RecordEvent, view: SeatView) -> None:
        """Called for every record event the seat is allowed to see, in order.
        The adapter may use this to update internal buffers (bot-runner builds
        history; TUI re-renders). Must return quickly (<50ms typical); not a
        place for decisions. Exceptions are logged and the adapter continues."""

    async def decide(self, prompt: Prompt) -> Action:
        """Called when the table manager needs an action from this seat.
        Must return an Action from prompt.legal_actions before prompt.deadline,
        or raise SeatTimeout / SeatError to signal failure. The table manager
        handles fallback action selection — adapters don't choose their own
        fallback."""

    async def left(self, reason: LeaveReason) -> None:
        """Called once when the seat is unbound (hand ended, table closed,
        seat replaced). Adapter releases resources (kill subprocess, close
        client connection). Must not raise; errors are logged."""
```

Five methods. Three (`seated`, `observe`, `left`) are lifecycle; one (`decide`) is the actual decision loop; `identity` is data.

**Why a Protocol, not a base class:** adapters don't share implementation. Forcing inheritance adds nothing and obscures the seam. Duck-typing via `Protocol` makes the interface checkable by `mypy --strict` (which CLAUDE.md requires on the table manager) without coupling adapters.

## Data shapes

### `SeatIdentity`

Tagged union; one variant per adapter kind. Embedded into [record-format.md](record-format.md)'s `HEADER.seats[].identity`.

```python
SeatIdentity = (
    | {"kind": "human",   "user_id": str, "display": str}
    | {"kind": "bot",     "bot_id":  str, "version": str, "runtime": "subprocess" | "in_process"}
    | {"kind": "canned",  "script":  str}                  # named canned sequence; for tests
    | {"kind": "spectator", "viewer_id": str}              # read-only; submits no actions
    | {"kind": "self_play_driver", "driver_id": str}       # holds all 4 seats; god-mode
)
```

`kind` is the only field the table manager branches on; everything else is opaque to it and passes through to the record.

### `SeatContext` (passed to `seated`)

```python
SeatContext = {
    "seat":           int,            # 0..3, your seat number
    "hand_id":        str,            # UUIDv7 from the record HEADER
    "ruleset":        RuleSetRef,     # same shape as in state-schema / record header
    "seat_deadline_ms": int,          # how long `seated` has to return
    "initial_view":   SeatView,       # post-deal projection for this seat
}
```

The adapter knows nothing of the other seats' identities at this point — that's deliberately separated. Adapters that need it (none in v1) would get it through `observe(HEADER, ...)` once the table emits header events.

### `RecordEvent` and `SeatView` (passed to `observe`)

`RecordEvent` is exactly the JSON object that gets written to the record file (see [record-format.md](record-format.md)). Pass-by-reference, treated as immutable. The adapter receives every event it's allowed to see, in `seq` order, *projected for this seat*:

- `HEADER`, `FOOTER`, `HAND_END`: everyone sees in full.
- `DEAL`: this seat sees their own `concealed`; others' `concealed` are replaced with `{"count": N}`.
- `DRAW`: only delivered to the drawing seat, with the explicit `tile`. Other seats receive a synthetic `{"event":"DRAW","seat":X,"tile":null,...}` so they know a draw happened but not what.
- `DISCARD`, `CLAIM_WINDOW`, `CLAIM_DECISION`, `CLAIM_RESOLUTION`: public; all seats see in full (the `CLAIM_WINDOW.opportunities` list is the only place a seat learns what *they* could claim — projected so they only see their own row).

`SeatView` is delivered alongside the event so the adapter doesn't have to re-derive state from history. Bot-runner ignores it (bots are stateless; the adapter builds the Botzone history itself); TUI uses it for rendering; canned ignores it; self-play uses the canonical state instead (see below).

### `Prompt` (passed to `decide`)

```python
Prompt = {
    "kind":          "DISCARD" | "CLAIM",   # what kind of decision is being requested
    "view":          SeatView,              # state at prompt time, projected for this seat
    "legal_actions": list[Action],          # exhaustive; nothing outside this list is valid
    "default_action": Action,               # what the table manager will submit on timeout/error
    "deadline":      AbsoluteDeadline,      # monotonic clock; not wall-clock
    "issued_at":     MonotonicTimestamp,    # for decision_ms calculation
    "context":       dict,                  # opaque metadata for logging; never affects validity
}
```

- `legal_actions` is the **complete** set; an adapter that returns anything outside it gets the action rejected as illegal (see error model below). This puts the legality computation in one place (the engine) and means every adapter is trivially correct on the "what can I do" question.
- `default_action` is `{"type":"PASS"}` for `CLAIM` prompts and `{"type":"PLAY","tile":<just-drawn-tile>}` (tsumogiri) for `DISCARD` prompts on own turn. The adapter does not pick its own default — that would mean every adapter has to know mahjong rules; we centralize.
- `deadline` is a *monotonic* deadline (`asyncio.get_event_loop().time()`-based), not a wall-clock. This matters because system time can jump (NTP sync); monotonic time can't. Wall-clock timestamps are only for record events and logs.
- `kind` is a hint, not a constraint — `legal_actions` is the authoritative list of what's allowed. Adapters that care can branch on `kind` (TUI shows different UI for own-turn vs. claim-window); adapters that don't care don't have to.

### `Action`

Identical to the action grammar in [state-schema.md](state-schema.md). The port doesn't redefine it.

### `LeaveReason`

```python
LeaveReason = (
    "HAND_ENDED"        # normal completion
    | "TABLE_CLOSED"    # table shut down
    | "REPLACED"        # this adapter was swapped out (degraded, reconnect-failed, etc.)
    | "ERROR"           # unrecoverable adapter error
)
```

Only the adapter's `left()` cares; the table manager's behavior is the same for all.

## Error model

Three failure modes, three handlings. The table never wedges; the hand always completes.

### Timeout

`decide` does not return by `prompt.deadline`. The table manager:

1. Cancels the pending coroutine (asyncio `task.cancel()`).
2. Submits `prompt.default_action` to the engine.
3. Writes a `CLAIM_DECISION` (or `DISCARD`) event with `timeout: true`.
4. Counts the timeout against this seat's strike budget (configurable per table; default: 3 timeouts in a hand → seat is replaced by an `AutoPassAdapter` for the rest of the hand).

The adapter is *not* signaled before cancellation. If it has cleanup to do on cancel, it does that in a `try/finally` inside `decide`.

### Illegal action

`decide` returns an `Action` not in `prompt.legal_actions`. The table manager:

1. Logs the violation with the offending action + the legal set.
2. Submits `prompt.default_action` (same as timeout path).
3. Writes a `CLAIM_DECISION` (or `DISCARD`) event with `illegal: true, attempted_action: <the bad action>`. The training corpus is interested in this signal.
4. Counts the violation against the same strike budget as timeouts. Repeated illegal actions get the seat swapped.

The action is **never** silently coerced or fixed up. The table manager does not try to guess what the adapter meant — that path is full of subtle bugs and corrupts the training signal.

### Crash / exception

`decide` raises any exception other than `SeatTimeout` / `SeatError`. The table manager:

1. Logs the traceback.
2. Treats it as a strike (same as timeout/illegal).
3. Calls the adapter's `left("ERROR")`.
4. Replaces with `AutoPassAdapter` for the remainder of the hand.

`SeatTimeout` and `SeatError` are sentinel exceptions an adapter *may* raise to explicitly signal those conditions (e.g., a bot-runner that knows its subprocess died doesn't need to wait for the deadline). They're treated the same as the corresponding silent failure mode but without the cancellation overhead.

### `AutoPassAdapter`

A degraded adapter used as the replacement for failing seats. Implements the same `SeatAdapter` protocol:

- `seated`: no-op.
- `observe`: no-op.
- `decide`: returns `prompt.default_action` immediately. Records carry a marker (`auto_pass: true` on the resulting event) so training data filters can exclude these decisions.
- `left`: no-op.

This is what makes "the hand always completes" structurally true: there is always a fallback, and the fallback is itself just an adapter.

## Adapter catalog

Brief sketch of each adapter type. Full implementations are post-spec work; this is the design contract each must satisfy.

### `HumanTuiAdapter`

- `seated`: pushes initial state to the connected TUI client over WebSocket.
- `observe`: pushes each event to the client; client re-renders.
- `decide`: sends a "your turn" message with `legal_actions`; awaits a response on the inbound WebSocket queue. On disconnect, waits up to `reconnect_window_ms` (configurable, default 60s, capped at `prompt.deadline`) for the same user to reconnect before timing out.
- `left`: closes the client session.

**Disconnect/reconnect semantics:** a disconnect mid-`decide` does *not* immediately fail the seat. The adapter holds the prompt, the client reconnects with the same `user_id` + session token, the prompt is re-pushed, and the deadline is whatever's left of the original. Only if the reconnect window expires *or* the prompt deadline hits does the seat fall through to timeout.

### `BotRunnerAdapter`

- `seated`: spawns the bot subprocess (under resource limits — see [bot-runner-protocol.md](bot-runner-protocol.md)), performs the startup handshake, holds the bot warm.
- `observe`: appends the event to an internal Botzone-format history buffer. Does *not* send anything to the subprocess — bots are stateless per [the AI plan](../ai-plan.md#platform-constraints-botzone); the full history is sent at decision time.
- `decide`: serializes the history buffer in Botzone request format, writes to the subprocess stdin, reads one response from stdout under a hard timeout (`min(prompt.deadline, bot_budget_ms)`), parses the response, returns the `Action`. Any framing/parse error → `SeatError`.
- `left`: sends SIGTERM, waits briefly, SIGKILL if still alive, reaps.

The full subprocess protocol lives in [bot-runner-protocol.md](bot-runner-protocol.md).

### `CannedAdapter`

- `seated`: loads a named canned-action script (e.g., `"always_pass"`, `"toy_hu_on_deal"`).
- `observe`: no-op (or asserts expected event sequence for stricter test fixtures).
- `decide`: returns the next scripted action; if the script is exhausted or the next action isn't in `legal_actions`, returns `default_action` (test should fail loudly if this happens).
- `left`: no-op.

This is the adapter used in unit tests of the table manager and the engine — no I/O, no subprocess, fully deterministic, fast.

### `SpectatorAdapter`

- `seated`: opens the spectator's read connection (WebSocket or stdout).
- `observe`: pushes the event to the spectator.
- `decide`: **never called.** A spectator is bound through a separate `add_spectator(viewer)` table manager call, not as a seat. The protocol matches `SeatAdapter` *structurally* (so spectators can share rendering code with TUI), but the table manager treats spectator slots separately from the four seat slots. If `decide` is ever called on a `SpectatorAdapter`, that's a bug → raise `SeatError`.
- `left`: closes the connection.

The S8 "permanent spectator table" exit criterion ("attempts to send an action from a spectator are rejected") rests on the table manager never wiring a `SpectatorAdapter` into the four-seat array. The protocol-level `decide` raise is a second-line defense.

### `SelfPlayDriverAdapter`

The exception to the "one adapter per seat" rule. A `SelfPlayDriver` is a single object that satisfies the `SeatAdapter` protocol *four times* — once per seat — and is allowed to see canonical `GameState` rather than `SeatView` because it owns all four seats by construction.

- `seated` (×4): registers each seat with the driver; first registration spins up the inner policy network / rule-based bot / whatever's training.
- `observe` (×4): driver receives the canonical event (not projected) and updates its internal training buffers.
- `decide` (×N seats prompted): driver picks an action for the prompted seat using its policy. Latency is bounded by inner inference time, which is bounded by the deadline like any other adapter.
- `left`: when all four seats have left, the driver flushes training data and shuts down.

This is what makes the AI plan's self-play harness "the same engine running with four bot adapters and no TUI adapter" — the driver *is* the bot, just instantiated as four trivially-thin seat adapter views.

**Privacy exception is explicit, not implicit.** The driver receives a flag on `seated` (`ctx.allow_god_view: true`) that opens an alternate `observe` channel carrying the canonical state. The default for every other adapter is `false`; the table manager won't pass `allow_god_view: true` to anything but a driver. The flag exists in code so the privacy boundary is auditable, not enforced by "we promise not to call the other thing."

## Lifecycle and concurrency model

One `asyncio.Task` per seat per hand owns that seat's adapter. The table manager's main loop:

1. Construct table; bind four `SeatAdapter`s.
2. `await asyncio.gather(adapter.seated(ctx_i) for i in 0..3)` with timeouts; replace any failures with `AutoPassAdapter`.
3. Loop until `phase == "TERMINAL"`:
    - Engine produces the next event(s); table manager pushes each via `await adapter.observe(...)` to all four seats (fanout).
    - If a decision is needed: `await adapter.decide(prompt)` for the prompted seat (or seats, in a `CLAIM_WINDOW` — multiple concurrent `decide` calls via `asyncio.gather`).
    - Apply the action; loop.
4. After `HAND_END` + `FOOTER`: `await asyncio.gather(adapter.left(reason) for adapter in adapters)`.

Concurrency points:

- `observe` fanout is concurrent (asyncio gather), but the table manager doesn't block on individual `observe` calls — each is a task with its own short deadline; if one hangs, the others proceed and the slow one gets canceled. This is to keep one misbehaving adapter from slowing the table.
- `decide` in a `CLAIM_WINDOW` is concurrent across the seats with opportunities. All proceed in parallel up to a single shared `CLAIM_WINDOW.deadline_ms`.
- Cross-seat ordering is enforced by the engine, not by the adapter call order. The table manager applies actions in the order the engine specifies (priority-resolved), not the order adapters returned.

## Worked example: a discard with two CHI claims

Trimmed pseudo-code of the table manager loop for one cycle:

```python
# Seat 1's own turn (DISCARD phase)
prompt = Prompt(
    kind="DISCARD",
    view=project(state, 1),
    legal_actions=engine.legal_actions(state, 1),
    default_action={"type":"PLAY","tile":just_drew},
    deadline=monotonic() + 30.0,
    issued_at=monotonic(),
    context={"hand_id": hand_id, "turn_index": state.turn_index},
)
action = await adapters[1].decide(prompt)
state = engine.apply_action(state, 1, action)   # raises IllegalAction if needed
record.append(diff_to_event(state_before, state, action))
for i in 0..3:
    await fanout_observe(adapters[i], record.last_event, project(state, i))

# Engine has transitioned to CLAIM_WINDOW; seats 2 and 3 have opportunities
window = state.pending_claims
prompts = {
    2: Prompt(kind="CLAIM", view=project(state, 2), legal_actions=[...], deadline=..., ...),
    3: Prompt(kind="CLAIM", view=project(state, 3), legal_actions=[...], deadline=..., ...),
}
results = await asyncio.gather(
    *(adapters[i].decide(prompts[i]) for i in (2, 3)),
    return_exceptions=True,
)
# results may include exceptions (timeouts, errors) — each becomes a default_action
for i, result in zip((2, 3), results):
    action = coerce_to_action_or_default(result, prompts[i])
    state = engine.apply_action(state, i, action)
    record.append(...)
    # engine internally tracks "all claim decisions in" and resolves at the right moment
```

`coerce_to_action_or_default` is the centralized failure handler: timeout → default, exception → default + log + strike, illegal → default + log + strike. Same logic, one place.

## Alternatives considered

**Async method `decide` vs. push/pull queues.**

- Considered: each adapter has `input_queue` (prompts) and `output_queue` (actions); table manager pushes/pulls.
- Chose `async def decide` because (a) it makes the prompt → action lifecycle a single typed call with a clear deadline, (b) cancellation works naturally (`task.cancel()` propagates to the awaited adapter), (c) testing a `CannedAdapter` is `assert (await adapter.decide(prompt)) == expected_action` — trivial. Queues would require the test to push, then await, then check, plus introduce a separate "is the queue empty" failure mode.

**Engine emits events vs. table manager diffs states.**

- Considered: the engine produces a list of `RecordEvent`s alongside the new state.
- Chose to diff in the table manager because (a) [state-schema.md](state-schema.md) committed to the engine being three pure functions with no event-stream output, (b) the diff is mechanical and lives once in the table manager, (c) keeping the engine producer-of-events would either bind it to the record schema (coupling) or require it to emit a parallel "internal event" type (duplication).

**Adapter chooses its own default vs. table manager chooses.**

- Considered: each adapter declares "if I time out, do X."
- Chose central default selection because (a) the default is rule-derived (tsumogiri / PASS), not policy-derived, so it belongs with the rules side, (b) every adapter knowing the rules would mean four reimplementations of "what's a safe action right now," (c) the centralization is what makes `AutoPassAdapter` a four-line class.

**One task per seat vs. one task per table.**

- Considered: a single coroutine per table that serially awaits each seat's decision.
- Chose one task per seat (during claim windows) because mahjong's claim-window semantics are inherently concurrent — multiple seats decide simultaneously, fastest valid claim with highest priority wins. Serializing them would either give earlier-seat advantage or require manual time-multiplexing. asyncio.gather is the natural fit.

**Synthetic `DRAW` for non-drawing seats vs. silent.**

- Considered: not delivering `DRAW` events to seats that aren't drawing.
- Chose synthetic delivery (`tile: null`) because (a) seat adapters need to know the turn is advancing (TUI updates "X's turn" indicator, bot-runner increments its history), (b) the existence of the draw is public information (you saw them take a tile from the wall, you just don't know which), so omitting it would distort the bot's available history relative to what a real Botzone bot sees.

**`SpectatorAdapter` as a separate protocol vs. a seat protocol.**

- Considered: a separate `SpectatorProtocol` with just `seated`/`observe`/`left`, no `decide`.
- Chose structural conformance to `SeatAdapter` because (a) the TUI rendering code is shared across human-seat and spectator views, so a shared protocol means shared call sites, (b) the table manager wires spectators into a separate list, not the 4-seat array, so the "decide should never be called" invariant is upheld at the wiring level — the structural conformance just opens code sharing.

## Verification fixtures this spec implies

1. **CannedAdapter end-to-end.** A four-`CannedAdapter` game plays a fixture script start-to-finish, producing the expected record. This is the S0 walking-skeleton exit artifact, restated in adapter terms.
2. **Timeout handling.** A `decide` that doesn't return → cancellation fires, `default_action` is applied, the event has `timeout: true`. Three timeouts → seat is swapped to `AutoPassAdapter`. Fixture: a deliberately-slow canned adapter.
3. **Illegal-action handling.** An adapter returns an action not in `legal_actions` → `default_action` is applied, event has `illegal: true, attempted_action: ...`. Fixture: a canned adapter scripted to attempt an illegal CHI.
4. **Crash handling.** An adapter raises `RuntimeError` from `decide` → `left("ERROR")` called, `AutoPassAdapter` substituted, hand continues. Fixture: a canned adapter scripted to raise.
5. **Privacy at the port.** For every fixture in the suite, every `decide` prompt's `view` contains zero tile tokens from other seats' concealed hands and zero tiles from the wall remaining-list. Automated assertion in the test harness (closes the loop with [state-schema.md](state-schema.md) fixture 3).
6. **`observe` fanout independence.** A canned adapter that hangs in `observe` does *not* delay other adapters' `observe`s or any `decide`. Fixture: one slow + three fast adapters; total cycle time matches the fast path within tolerance.
7. **Claim-window concurrency.** A fixture `CLAIM_WINDOW` with three opportunities resolves to the highest-priority claim regardless of which adapter returns first. Run the same fixture with different per-adapter latencies; outcome is invariant.
8. **AutoPassAdapter substitution preserves replay.** A hand where seat 2 is `AutoPassAdapter`ed after three strikes still produces a record that replays byte-identically.
9. **SelfPlayDriver god-mode is opt-in.** A `SelfPlayDriverAdapter` instantiated without `allow_god_view: true` receives `SeatView`s, not `GameState`s, on `observe`. Closes the privacy-flag audit.

## Open questions

- **Per-action time accounting across claim windows.** A bot's "budget" is per-decision in Botzone. But a `CLAIM_WINDOW` with three opportunities is one decision *or* three from the bot's perspective? Working answer: one per prompt, mirroring how Botzone scores it. Pin in [bot-runner-protocol.md](bot-runner-protocol.md).
- **Reconnect window vs. prompt deadline.** Human-TUI adapter currently caps `reconnect_window_ms` by `prompt.deadline`. If the prompt deadline is 30s and the reconnect window is 60s, the human gets 30s effective. Is that the right semantics, or should the per-table reconnect_window pause the deadline? Working answer: don't pause; pauses are exploitable by deliberate disconnects. Document.
- **Adapter hot-swap mid-hand.** The error model swaps in `AutoPassAdapter` automatically. Should there be a manual admin path (a host swapping out a slow human friend mid-hand)? Defer until requested; the structural support is there (the table manager already swaps via the strike path).
- **Backpressure on `observe`.** Currently `observe` is fire-and-forget with a short deadline. If an adapter's queue fills up (network-slow TUI), do we drop events, block the table, or buffer-and-coalesce? Working answer: drop and reconcile on reconnect (the record is the source of truth, the client can resync from it). Pin when S2 ships.
- **Spectator decide-rejection enforcement.** Currently a structural assert. Worth adding a type-system enforcement (a separate `SpectatorProtocol` with no `decide`)? Working answer: keep the structural shape for code sharing; the wiring-level invariant + the runtime assert are sufficient. Reconsider if a spectator type ever needs to share `decide`-handling code with seats (currently no such case).
