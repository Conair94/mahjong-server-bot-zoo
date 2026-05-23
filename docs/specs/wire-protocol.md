# Spec 10 — Wire protocol

The WebSocket message contract between server and every connected client (TUI in v1; future web/mobile clients later). This is **the** seam between in-process Python and the outside world: everything the server tells a client and everything a client tells the server flows through this format.

Builds on [seat-port.md](seat-port.md) (this protocol is how a human's connection becomes a `SeatAdapter` call), [state-schema.md](state-schema.md) (`SeatView` is the privacy-projected payload), [record-format.md](record-format.md) (server-pushed events mirror record event shapes), and [determinism.md](determinism.md) (canonical-JSON serialization rules).

**Status:** draft, pre-S2 implementation. Resolved decisions baked in per [s2-s3-plan.md §10](../s2-s3-plan.md).

## Goals

- **One transport, every client.** TUI, web, mobile, future analysis tools — all speak this protocol. The server has no client-type-specific code path.
- **Privacy enforced at the wire.** A connected client only ever receives `SeatView`-projected data for seats it is authorized to see. The wire format makes the canonical `GameState` unrepresentable for client traffic; god-view payloads exist only inside the server process.
- **Reconnect-safe.** Network drops are the common case for a Tailscale-served hobby server (laptop sleeps, Wi-Fi flaps). The protocol carries a resume token so a client can drop and re-establish without losing its seat — within the seat-hold window pinned in [session-mux.md](session-mux.md).
- **Sequence-numbered, monotonic.** Every server-to-client message has a per-connection `seq`. Clients can detect drops and reconcile state. Replays of the same hand from records produce a byte-identical sequence.
- **Authoritative server, dumb client.** The server is the only thing that decides what's legal, what's visible, and what's persistent. The client renders state and submits actions. The protocol never asks a client to compute game logic.
- **Spectators are first-class from v1.** Friends watching a game in progress is expected to be heavy early-stage usage. Spectating is a separate flow from playing — its own message pair (`SPECTATE` / `SPECTATING`), its own privacy projection (public-only events, no concealed tiles, no prompts), and its own absence of seat-hold mechanics (a spectator's drop releases nothing). Multiple spectators per table; no exclusivity.
- **Extensible additively.** New message types and new fields are added without breaking older clients. Unknown `kind` is a hard error (don't silently drop a message you don't understand); unknown optional fields are tolerated (forward-compatible).

## Non-goals

- **Not a transport protocol.** Framing details below ride on WebSocket text frames (RFC 6455). TLS, HTTP upgrade, origin checking, etc. are the WebSocket library's job ([`websockets`](https://websockets.readthedocs.io/)); this spec does not redefine them.
- **Not a UI spec.** What gets rendered for a human is the TUI's business ([tui-client.md](tui-client.md)). This protocol delivers data; the TUI decides how to draw it.
- **Not a bot protocol.** Bots speak [bot-runner-protocol.md](bot-runner-protocol.md) over stdin/stdout to the bot-runner adapter. They do not connect to the server over WebSocket. ("Bot accounts" in [auth.md](auth.md) are server-side credentials used by the *bot-runner* — the bot subprocess itself never speaks this protocol.)
- **Not a stream protocol.** Each message is a complete JSON object in one WebSocket text frame. No fragmenting a logical message across frames, no binary frames, no compression in v1.
- **No multiplexing.** One WebSocket connection holds at most one *playing* attachment. Multi-table clients open one connection per table. Spectating is single-subscription-per-connection too: one spectated table per WebSocket. (Self-play drivers talk to the engine in-process and do not use this protocol.)

## Transport

- **WebSocket.** Server listens on `MAHJONG_LISTEN_ADDR` (default `127.0.0.1:8400`); client connects to `ws://host:port/socket`.
- **Subprotocol identifier:** the client requests subprotocol `mahjong-v1` in the WebSocket handshake (`Sec-WebSocket-Protocol`). Server accepts only this value in v1; refuses connections that request anything else. Future protocol versions add `mahjong-v2`, etc., side-by-side.
- **Text frames only.** Server and client send WebSocket text frames; binary frames are a framing violation (server closes with code 1003 — "unsupported data"). Encoding is UTF-8.
- **One JSON object per frame.** Each text frame is exactly one JSON document, no leading or trailing whitespace, no embedded newlines except inside string values. No NDJSON, no multi-message frames.
- **Ping/pong.** Standard WebSocket ping/pong handled by the library. Application-level heartbeat is a separate `HEARTBEAT` message (see below) — they exist because the WebSocket ping doesn't carry sequence-number context for our drop detection.

The choice of WebSocket and the rejection of plain TCP / HTTP polling are pinned in [s2-s3-plan.md §10](../s2-s3-plan.md).

## Message framing

Every message is a JSON object with at minimum a `kind` field. Server-to-client messages additionally carry `seq`. Client-to-server messages may carry `ref` to correlate a response to a prior request.

```json
{
  "kind": "HELLO",
  "seq": 1,
  "protocol_version": 1,
  "server_id": "mahjong-server-0.1.0"
}
```

### Required fields on every message

- `kind` (string, required): the message type. UPPER_SNAKE_CASE. The full enumeration is in §"Message catalog".

### Server-to-client additional fields

- `seq` (integer, required): per-connection monotonic counter. Starts at 1 on the first message after the WebSocket handshake completes. Increments by 1 for every server-to-client frame. Wraps never (integers are 64-bit; we'll never hit `2^63`).
- `t_server_ms` (integer, optional): server wall-clock timestamp in milliseconds since the Unix epoch. Diagnostic only; clients must not rely on it for game logic (use record events for timing facts).

### Client-to-server additional fields

- `ref` (integer, optional): the `seq` of a server message this client message responds to. Used by `ACTION` (refs the `PROMPT` it answers) and `AUTH_RESPONSE` echoes. Servers MUST tolerate missing `ref`; clients SHOULD include it when responding to a specific prompt.
- `client_seq` (integer, optional): client-side monotonic counter, for diagnostics only. The server does not enforce strict monotonicity.

### Canonical JSON

Serialization follows [determinism.md § Canonical hash](determinism.md): keys sorted lexicographically, no insignificant whitespace, no trailing newlines, integers without leading zeros, strings UTF-8. **This applies to records and bot-protocol; for wire-protocol it is *recommended but not required*** — the WebSocket frame's payload may contain pretty-printed JSON if a future client benefits, because the wire is not hashed for replay (records are; this isn't). Clients should still send canonical JSON to keep server-side parse predictable.

## Connection lifecycle

```text
  client              server
    |                    |
    |   WS handshake     |
    | ─────────────────► |
    | ◄───── HELLO ───── |   (server seq=1)
    | ──── HELLO ──────► |
    | ── AUTH_REQUEST ─► |   (or RESUME)
    | ◄── AUTH_RESPONSE  |   (server seq=2)
    | ─── LIST_TABLES ─► |   (or ATTACH directly)
    | ◄── TABLE_LIST ─── |   (server seq=3)
    | ─── ATTACH ──────► |
    | ◄── ATTACHED ───── |   (server seq=4)
    | ◄── EVENT ──────── |   (server seq=5..N, observe stream)
    | ◄── PROMPT ─────── |   (server seq=N+1)
    | ─── ACTION ──────► |   (ref=N+1)
    | ◄── EVENT ──────── |   (resulting record events)
    |                    |
    |  ... many turns ...|
    |                    |
    | ◄── HAND_END ───── |
    | ─── DETACH ──────► |
    | ◄── DETACHED ───── |
    |                    |
    |   WS close (1000)  |
    | ◄─────────────────►|
```

The five phases:

1. **Handshake** — WebSocket upgrade succeeds; server immediately sends `HELLO`; client replies with `HELLO`.
2. **Authentication** — client sends `AUTH_REQUEST` (login) or `RESUME` (re-establish with session token).
3. **Discovery** — optional `LIST_TABLES` / `TABLE_LIST` exchange. Skippable if the client already knows which table it wants.
4. **Attached** — client `ATTACH`es to a table+seat; server confirms with `ATTACHED`. From here the seat-port flow takes over: `EVENT`s stream the projected record; `PROMPT`s ask for actions; `ACTION`s deliver them.
5. **Detach + close** — client `DETACH`es (graceful) or drops (ungraceful); server closes the WebSocket with the appropriate code.

The exact state machine — including the seat-hold window for ungraceful disconnects — is pinned in [session-mux.md](session-mux.md). This spec defines the *messages* exchanged; session-mux defines *when* each is valid.

## Message catalog

Messages grouped by direction. Every payload below is normative: the example *is* the contract.

### Control (bidirectional)

#### `HELLO`

Sent by server immediately after WebSocket handshake; sent by client in response.

**Server → client:**

```json
{
  "kind": "HELLO",
  "seq": 1,
  "protocol_version": 1,
  "server_id": "mahjong-server-0.1.0",
  "min_client_version": 1,
  "features": ["resume", "list_tables", "spectate"]
}
```

- `protocol_version` (int, required): server's wire-protocol version. Clients reject mismatch.
- `server_id` (string, required): server build identifier. Diagnostic.
- `min_client_version` (int, optional): minimum client `protocol_version` the server accepts.
- `features` (string[], optional): named optional features the server supports. Clients may use this to enable UI affordances; the protocol is functional without it.

**Client → server:**

```json
{
  "kind": "HELLO",
  "protocol_version": 1,
  "client_id": "mahjong-tui-0.1.0"
}
```

The server enforces `protocol_version == 1` in v1. Mismatch → server sends `ERROR { code: "protocol_version" }` and closes the WebSocket with code 1002 ("protocol error").

#### `HEARTBEAT`

Application-level keep-alive. Either side may send it; the receiver echoes it back with the same `nonce`.

```json
{ "kind": "HEARTBEAT", "nonce": "a1b2c3d4" }
```

The reply:

```json
{ "kind": "HEARTBEAT", "nonce": "a1b2c3d4", "echo": true }
```

Servers send a `HEARTBEAT` every 30 seconds of outbound silence (no `EVENT`, `PROMPT`, etc. sent). A client that sees no server frames for 60 seconds should consider the connection dead and reconnect with `RESUME`. The TCP-level WebSocket ping does the same job in most stacks; we keep the app-level heartbeat to make drop detection observable in tests.

#### `ERROR`

Either side may send an error frame. After sending one, the side that sent it MAY also close the WebSocket; the side that received it MUST be prepared for an imminent close.

```json
{
  "kind": "ERROR",
  "seq": 7,
  "code": "illegal_action",
  "message": "PLAY W3 is not in legal_actions for prompt seq=6",
  "ref": 6,
  "details": { "legal_actions": ["PASS", "PLAY B5", "..."] }
}
```

- `code` (string, required): a stable enum-like identifier. See §"Error codes" below.
- `message` (string, required): human-readable diagnostic. Not stable; do not parse.
- `ref` (int, optional): the `seq` of the server message that caused the error (or, for server-sent errors about a client message, the `client_seq`).
- `details` (object, optional): code-specific structured context.

`ERROR` is not a `seq`-bearing-special-case for server→client — it carries `seq` like any other server frame.

### Authentication

#### `AUTH_REQUEST`

Client → server. Sent once, immediately after the `HELLO` exchange, on a fresh connection. Login by credentials.

```json
{
  "kind": "AUTH_REQUEST",
  "username": "alice",
  "password": "correct-horse-battery-staple"
}
```

Server validates per [auth.md](auth.md) (argon2 hash compare, no early termination on missing user, etc.). On success → `AUTH_RESPONSE`. On failure → `AUTH_RESPONSE { ok: false }` *only* — the server does not distinguish "no such user" from "wrong password" on the wire.

Connections in S2 (server-plan.md "hard-coded users") may skip `AUTH_REQUEST` entirely; the server populates the connection's identity from a config-file mapping by client IP. The wire format supports auth from day one because re-introducing it for S3 is a config flag, not a protocol change.

#### `AUTH_RESPONSE`

Server → client.

```json
{
  "kind": "AUTH_RESPONSE",
  "seq": 2,
  "ok": true,
  "user_id": "u_alice",
  "display_name": "Alice",
  "session_token": "s_8f1c...",
  "expires_at_ms": 1748908800000
}
```

- `ok` (bool, required): success flag.
- `user_id` (string, present iff `ok`): server's stable identifier for this user. Embedded in records as the `human` `SeatIdentity.user_id`.
- `display_name` (string, present iff `ok`): the name to render in UI.
- `session_token` (string, present iff `ok`): the opaque token to present on `RESUME`. Format is server-internal; clients treat as opaque.
- `expires_at_ms` (int, present iff `ok`): token expiry as Unix epoch milliseconds. Tokens auto-renew on successful `RESUME`; see [auth.md](auth.md).

Failure shape:

```json
{ "kind": "AUTH_RESPONSE", "seq": 2, "ok": false }
```

Followed by server `CLOSE` with code 4001 ("auth failed"). Clients implementing retry must respect a backoff defined in [auth.md](auth.md).

#### `RESUME`

Client → server. Alternative to `AUTH_REQUEST`: re-establish a prior session.

```json
{
  "kind": "RESUME",
  "session_token": "s_8f1c..."
}
```

On success the server replies with `AUTH_RESPONSE` populated as if from a fresh login (a possibly-rotated `session_token` and a fresh `expires_at_ms`). On failure (expired, unknown, revoked) the server replies with `AUTH_RESPONSE { ok: false }` and closes with code 4002 ("session expired"); the client must re-`AUTH_REQUEST`.

If the client was attached to a seat when its previous connection dropped, the server automatically re-binds the seat on successful `RESUME` (provided the seat-hold window from [session-mux.md](session-mux.md) has not elapsed). The server then re-streams any `EVENT`s the client missed (replaying from a server-side per-seat buffer) and re-issues any pending `PROMPT`. This is the core reconnect path.

### Discovery

#### `LIST_TABLES`

Client → server. No fields.

```json
{ "kind": "LIST_TABLES" }
```

#### `TABLE_LIST`

Server → client.

```json
{
  "kind": "TABLE_LIST",
  "seq": 3,
  "tables": [
    {
      "table_id": 17,
      "ruleset": "mcr-2006",
      "seats": [
        { "seat": 0, "kind": "human", "user_id": "u_alice",  "occupied": true,  "attached": true  },
        { "seat": 1, "kind": "human", "user_id": "u_bob",    "occupied": true,  "attached": false },
        { "seat": 2, "kind": "bot",   "bot_id":  "b_rule_v1","occupied": true,  "attached": true  },
        { "seat": 3, "kind": "open",  "occupied": false,     "attached": false }
      ],
      "hand_index": 0,
      "phase": "WAITING_FOR_PLAYERS"
    }
  ]
}
```

The list contains every table the requesting user can see. Visibility rules:

- v1 / S2: all tables are public; the list always contains all of them.
- v1 / S3: still public; private tables are a future feature.

### Attachment

#### `ATTACH`

Client → server. Bind this connection to a specific seat at a specific table.

```json
{
  "kind": "ATTACH",
  "table_id": 17,
  "seat": 3
}
```

- `seat` (int, required): which seat (0–3) to bind to. Server validates that the seat is open *or* is held for this user.

Server replies with `ATTACHED` on success, `ERROR` on failure (codes: `table_unknown`, `seat_occupied`, `seat_not_yours`, `table_full`, `not_authorized`).

#### `ATTACHED`

Server → client.

```json
{
  "kind": "ATTACHED",
  "seq": 4,
  "table_id": 17,
  "seat": 3,
  "hand_index": 0,
  "snapshot": { /* SeatView, see state-schema.md */ },
  "resume_buffer_size": 0
}
```

- `snapshot` (SeatView, required): the seat's current projected view. From here on the client maintains state by applying `EVENT`s on top of this snapshot.
- `resume_buffer_size` (int, required): the count of `EVENT`s the server is about to replay because they happened before this attach (always `0` on a fresh attach; nonzero after `RESUME`).

#### `DETACH`

Either direction. Graceful release of the seat binding without closing the WebSocket.

Client → server:

```json
{ "kind": "DETACH", "reason": "leaving" }
```

Server → client:

```json
{
  "kind": "DETACH",
  "seq": 42,
  "reason": "replaced_by_autopass",
  "table_id": 17,
  "seat": 3
}
```

Valid `reason` values: `leaving` (client-initiated), `kicked` (admin), `replaced_by_autopass` (seat-port strike budget exhausted), `table_closed`, `hand_ended` (informational; the server closes hand-end state by detaching). The seat-port mapping is pinned in [seat-port.md § Error model](seat-port.md).

After a `DETACH` the WebSocket remains open; the client may `LIST_TABLES`, `ATTACH` to another seat, or `CLOSE`.

#### `DETACHED`

Server → client. Acknowledgement of a client-initiated `DETACH`. Carries no fields beyond `kind`/`seq`. (The server's *unsolicited* detach goes out as `DETACH`, not `DETACHED`.)

### Spectating

A spectator connection observes a table without occupying a seat. From the wire's perspective:

- **Separate message pair** (`SPECTATE` / `SPECTATING`) — not an `ATTACH` flag. Spectating has no seat parameter, no seat-hold timer, no `PROMPT`, no `ACTION`. Overloading `ATTACH` would force conditionally-meaningful fields; separation keeps each message's contract obvious.
- **No exclusivity.** Many spectators may attach to the same table simultaneously. Each gets their own copy of the projected event stream.
- **Public-only events.** Spectator `EVENT`s carry only fields the "all hands face-down" projection allows — no `seat.concealed`, no own-draw payloads, no concealed-meld details. The wire codec is the enforcement point (same defense-in-depth assertion as the player-EVENT path).
- **No seat-hold on disconnect.** A spectator's WebSocket drop releases the subscription immediately; there's no ring buffer kept on their behalf, no replay on reconnect. Reconnecting spectators re-issue `SPECTATE` and start fresh from a `SPECTATING.snapshot`.
- **Spectators can become players (and vice versa)** by detaching/spectating-stop on the current role and issuing the new role's message. The protocol does not transition roles silently.

#### `SPECTATE`

Client → server. Subscribe to a table's public event stream.

```json
{
  "kind": "SPECTATE",
  "table_id": 17
}
```

Server replies with `SPECTATING` on success, `ERROR` on failure (codes: `table_unknown`, `not_authorized`, `spectator_limit_reached`).

#### `SPECTATING`

Server → client.

```json
{
  "kind": "SPECTATING",
  "seq": 4,
  "table_id": 17,
  "hand_index": 0,
  "snapshot": { /* PublicView, see state-schema.md */ },
  "spectator_count": 3
}
```

- `snapshot` (PublicView, required): the table's current public-only projection. This is the "all-hands-face-down" `SeatView`-shaped object with every seat's `concealed` empty and every seat's `flowers` / `melds` / `discards` populated as for any external observer. The exact shape is pinned in [state-schema.md § Per-seat projection](state-schema.md); spectators use a `seat = None` (or equivalent) projection.
- `spectator_count` (int, optional): how many spectators (including this one) are currently watching. Informational; clients may render a "N watching" indicator.

After `SPECTATING`, the server streams `EVENT`s with the same `kind: "EVENT"` envelope as the player path, but with the public-only projection applied. `PROMPT` / `ACTION` / `HAND_END.next_hand_seq` semantics also apply (a spectator sees `HAND_END` between hands but never an outstanding prompt).

#### `STOP_SPECTATING`

Client → server. Graceful unsubscribe.

```json
{ "kind": "STOP_SPECTATING" }
```

Server replies with `DETACHED` (reusing the existing acknowledgement message — the `DETACH`/`DETACHED` pair generalises to any role release; this is intentional). The WebSocket remains open; the client may `LIST_TABLES`, `ATTACH`, `SPECTATE` again, or `CLOSE`.

The server may also unilaterally send `DETACH { reason: "table_closed" | "server_shutdown" | "internal_error" }` to a spectator. Spectators never receive `replaced_by_autopass` (that's a player-only reason).

### Gameplay

These are the messages that flow on an attached connection during a hand. They mirror the seat-port port methods one-to-one: server `EVENT` ↔ `SeatAdapter.observe`; server `PROMPT` ↔ `SeatAdapter.decide`; client `ACTION` ↔ that method's return value.

#### `EVENT`

Server → client. One `EVENT` per `RecordEvent` the seat is allowed to see, in record order. The payload is the *event itself*, projected to the seat — i.e. it carries only fields the projection rule allows. Concealed-tile-bearing fields appear only on events the seat owns (its own draws and concealed actions).

```json
{
  "kind": "EVENT",
  "seq": 5,
  "table_id": 17,
  "hand_index": 0,
  "event": {
    "kind": "DISCARD",
    "seq": 12,
    "seat": 1,
    "tile": "T6",
    "from_hand": true
  }
}
```

The inner `event` is **byte-identical to the corresponding [record-format.md](record-format.md) event line, minus its own `seq`** (which is the record's `seq` and lives inside the inner object). The outer `seq` is the connection-level counter. This double-`seq` is intentional: it lets the client correlate to records (via `event.seq`) and to drop-detection (via outer `seq`).

Privacy: an `EVENT` sent to seat S NEVER contains a field that the [state-schema.md § Per-seat projection](state-schema.md) rule would project away in `project(state, S)`. The wire is the last place to enforce this; if a buggy server tries to send unprojected data, the inner event is rewritten by the wire-protocol codec to its projected form, *and* a server-side assertion fires (logged loudly). The codec acts as a defense-in-depth seam.

#### `PROMPT`

Server → client. Request a decision from the seat. Mirrors [seat-port.md § `Prompt`](seat-port.md) and [state-schema.md § `legal_actions`](state-schema.md).

```json
{
  "kind": "PROMPT",
  "seq": 23,
  "table_id": 17,
  "hand_index": 0,
  "seat": 3,
  "phase": "DISCARD",
  "legal_actions": [
    { "kind": "PLAY", "tile": "W3", "from_hand": true },
    { "kind": "PLAY", "tile": "B5", "from_hand": true },
    { "kind": "GANG_CONCEALED", "tile": "F1" },
    { "kind": "HU", "self_drawn": true, "win_tile": "T9" }
  ],
  "default_action": { "kind": "PLAY", "tile": "W3", "from_hand": true },
  "deadline_ms": 1748908830000,
  "prompt_id": "p_17_0_23"
}
```

- `legal_actions` (Action[], required): the full enumeration of legal actions from `legal_actions(state, seat)`. Clients render and let the user pick; clients must never compute legality themselves.
- `default_action` (Action, required): the action the server will apply if no `ACTION` arrives by the deadline. Pinned by seat-port: `PASS` in claim windows, tsumogiri on own turn.
- `deadline_ms` (int, required): absolute deadline as Unix epoch ms. Clients should display countdown; the server enforces the deadline.
- `prompt_id` (string, required): server-stable identifier for this prompt. The client echoes it on `ACTION` (in addition to setting `ref` to the message `seq`). The redundant identifier survives reconnects where outer `seq` is reset.

A single connection sees at most one outstanding `PROMPT` at a time. If a second `PROMPT` arrives before the first is answered, the first is implicitly cancelled (timeout-equivalent) — clients should not assume they can reorder responses.

#### `ACTION`

Client → server. The decided action.

```json
{
  "kind": "ACTION",
  "ref": 23,
  "prompt_id": "p_17_0_23",
  "action": { "kind": "PLAY", "tile": "B5", "from_hand": true }
}
```

Server validates the action against the prompt's `legal_actions`:

- Action is in the set → server applies it via `apply_action`, streams resulting `EVENT`s, possibly issues the next `PROMPT`.
- Action is *not* in the set → server replies `ERROR { code: "illegal_action", ref: 23, details: { legal_actions: [...] } }`. The seat-port strike counter increments per [seat-port.md § Error model](seat-port.md). The server does *not* close the connection on a single illegal action; it tells the client and waits. Repeat offenders are replaced by `AutoPassAdapter` and detached with `reason: "replaced_by_autopass"`.
- `prompt_id` doesn't match the outstanding prompt → `ERROR { code: "stale_action" }`. The action is discarded; the outstanding prompt remains.
- No prompt outstanding → `ERROR { code: "unsolicited_action" }`.

#### `HAND_END`

Server → client. Sent once when the hand terminates (HU, exhaustive draw, table closed). The client can render the result screen.

```json
{
  "kind": "HAND_END",
  "seq": 87,
  "table_id": 17,
  "hand_index": 0,
  "terminal": {
    "kind": "HU",
    "winner": 2,
    "loser": 1,
    "fan_list": [{"name": "Pung of Terminals", "fan": 1}, "..."],
    "fan_total": 12
  },
  "next_hand_seq": null
}
```

- `terminal` (Terminal, required): the engine's terminal payload per [state-schema.md § Terminal block](state-schema.md).
- `next_hand_seq` (int|null, required): if a next hand will start in this table session, the connection-level `seq` of the upcoming `ATTACHED` for hand `hand_index + 1`. Null if the table is closing.

`HAND_END` is sent *before* the server detaches the seat for hand cleanup. Clients show a result screen; the server pauses briefly (see [session-mux.md](session-mux.md)) and then issues `DETACH` for the old hand and `ATTACHED` for the new hand.

### Server-administrative

These are scoped to S2+S3 and limited in v1.

#### `CREATE_TABLE`

Client → server. Requires admin role (`accounts.role = 'admin'`) in S3; unrestricted in S2.

```json
{
  "kind": "CREATE_TABLE",
  "ruleset": "mcr-2006",
  "seats": [
    { "kind": "human", "user_id": "u_alice" },
    { "kind": "human", "user_id": "u_bob" },
    { "kind": "human", "user_id": "u_carol" },
    { "kind": "bot",   "bot_id":  "b_rule_v1" }
  ]
}
```

#### `TABLE_CREATED`

Server → client.

```json
{ "kind": "TABLE_CREATED", "seq": 9, "table_id": 17 }
```

#### `CLOSE_TABLE`

Admin-only. Requests the table close at the next safe checkpoint (end of current hand by default, or immediately with `force: true`). Response is a `DETACH { reason: "table_closed" }` to every attached connection, then a `TABLE_LIST` refresh on next `LIST_TABLES`.

## Privacy on the wire

The wire codec is the last enforcement point of [state-schema.md § Per-seat projection](state-schema.md). Specifically:

- `EVENT` is rewritten by the codec to its `project(event, seat)` shape before send. The rewrite is pure-functional; in tests we assert that for any seat `S` and any record event `E`, the codec output equals `record_event_to_seatview_event(E, S)`.
- `PROMPT.legal_actions` and `PROMPT.default_action` are taken directly from `legal_actions(state, seat)`, which is itself projected. No additional rewrite needed.
- `ATTACHED.snapshot` is a `SeatView`, never a `GameState`. The codec type system rejects sending a `GameState` over the wire (mypy + runtime assert).
- `SPECTATING.snapshot` is a `PublicView` (public-only projection — empty `concealed` on every seat). The codec type system rejects sending any `seat.concealed` content on a spectator connection (mypy + runtime assert). This is a strictly tighter projection than the player path: a spectator never sees what any single seat sees, only what every external observer sees.
- A connection's role (player on seat S, spectator, or unattached) is tracked at the wire-protocol codec layer. Outbound `EVENT` and `PROMPT` are routed through the role-appropriate projection. A buggy server attempting to send a player-path event to a spectator connection trips the assertion *before* the frame is serialised.

## Error codes

Stable enum-like strings on `ERROR.code`. Adding a new code is additive; renaming or removing one is a protocol break.

| Code | Sent by | Meaning | Recovery |
| --- | --- | --- | --- |
| `protocol_version` | server | Client's `HELLO.protocol_version` not supported. | Client upgrades. WS closes 1002. |
| `framing` | either | Frame was not a JSON object, or was missing `kind`. | Sender bug; WS closes 1003. |
| `unknown_kind` | either | `kind` value not in this spec. | Client/server bug. WS closes 1003. |
| `auth_failed` | server | Login or resume rejected. | Client re-authenticates. |
| `auth_required` | server | Action requires authentication; none on this connection. | Client sends `AUTH_REQUEST`. |
| `not_authorized` | server | Authenticated but not allowed (e.g. spectator tries to `ACTION`, non-admin tries `CREATE_TABLE`). | Client gives up on that action. |
| `spectator_limit_reached` | server | `SPECTATE` rejected because table is at `MAHJONG_MAX_SPECTATORS_PER_TABLE` (default 32). | Client can retry later or pick another table. |
| `table_unknown` | server | `table_id` doesn't exist. | Client refreshes `LIST_TABLES`. |
| `seat_occupied` | server | `ATTACH` to a seat already held. | Client picks another seat or waits. |
| `seat_not_yours` | server | Seat is held for a different user (e.g. mid-reconnect window). | Client waits or picks another seat. |
| `table_full` | server | No seats open. | Client picks another table. |
| `no_outstanding_prompt` | server | Client sent `ACTION` without a prompt. | Client bug. |
| `stale_action` | server | `prompt_id` doesn't match. | Client drops the stale UI state. |
| `illegal_action` | server | Action not in `legal_actions`. | Client UI bug; strike counted. |
| `internal_error` | server | Unexpected server condition. | Server-side concern; client should reconnect. |
| `shutting_down` | server | Server is draining for shutdown; new attaches/spectates refused. | Client retries after a delay. Sent inside an `ERROR` frame in response to `ATTACH`/`SPECTATE`/`CREATE_TABLE` during the drain window. See [server-lifecycle.md § Graceful shutdown](server-lifecycle.md). |
| `rate_limit` | server | Client exceeded the per-second frame budget. | Client backs off; WS closes 1008. |

WebSocket close codes used by the server:

- `1000` — normal closure (client `CLOSE`, server `CLOSE_TABLE`-driven detach + close, etc.).
- `1002` — protocol error (sent for `protocol_version`, `framing`, `unknown_kind`).
- `1003` — unsupported data (binary frame received).
- `1008` — policy violation (rate-limit, abuse).
- `1011` — internal error.
- `4001` — auth failed (private-use range; documented here).
- `4002` — session expired (private-use range).
- `4003` — seat-hold window expired (private-use range).

## Versioning

Single integer `protocol_version` on `HELLO`. v1 ships as `1`.

Adding a new optional field to an existing message → no version bump. Receivers MUST tolerate unknown optional fields.

Adding a new `kind` → no version bump. Receivers MUST send `ERROR { code: "unknown_kind" }` rather than silently ignoring.

Renaming a field, removing a field, changing field semantics → version bump. New `protocol_version = 2`; the WebSocket subprotocol identifier becomes `mahjong-v2`; v1 clients are rejected at handshake.

Rejection of mismatched versions is hard, not soft. We don't run "translation shims" between protocol versions in v1 — at this scale, deploying server and client in lockstep is cheaper than maintaining a compat layer.

## Reconnect semantics (summary)

Full state machine is in [session-mux.md](session-mux.md). The wire-protocol portion:

- Server holds per-seat `EVENT` history in a ring buffer (size pinned in session-mux.md, default 256 events — enough for a full hand).
- On `RESUME` for an attached seat, the server replays missed `EVENT`s in order (`resume_buffer_size` in `ATTACHED` indicates how many to expect) followed by re-issuing the outstanding `PROMPT` (if any).
- If more events were missed than the buffer holds, the server sends `ATTACHED` with `snapshot` reflecting current state, `resume_buffer_size = 0`, and the client treats it as a fresh attach (no replay). This is the cost of a sufficiently long disconnect; the seat is still bound.
- If the seat-hold window has elapsed, `RESUME` succeeds but `ATTACH` is required separately — the seat was released to auto-pass and the client must pick where to go next.

## Rate limiting and abuse

v1 enforces:

- Max 10 frames per second from a client. Excess → `ERROR { code: "rate_limit" }` + WS close 1008.
- Max 16 KB per inbound frame. Excess → WS close 1009 ("message too big").
- Max one outstanding `PROMPT` per attached seat (enforced server-side, not client-side; described above).

These limits are deliberately loose for a friends-and-family server. Tightening is a config knob, not a protocol change.

## Alternatives considered

- **gRPC instead of WebSocket.** Stronger typing via protobuf, bidirectional streams. Rejected: requires HTTP/2, codegen step, harder to debug by hand, less natural fit for Textual's asyncio stack. WebSocket+JSON is what we'd debug with a browser console anyway.
- **JSON-RPC 2.0.** Reuses an existing schema for request/response correlation. Rejected: most of our traffic is server-initiated (events, prompts, heartbeats), which JSON-RPC handles awkwardly. Our `ref` field gives correlation where we need it without the wrapper overhead.
- **MessagePack / CBOR binary encoding.** Smaller wire size. Rejected at v1: human-readable JSON is debuggable, and the message volume is tens per hand, not thousands. Revisit if/when an analysis overlay starts pushing large grids per turn.
- **Server pushes the full `SeatView` per event.** Simpler client (no state machine; just rerender on each message). Rejected: bandwidth and latency aren't free, *and* it makes the protocol diverge from the record format (which is event-stream). Keeping `EVENT` shape == record-event shape means client code reuses the same projection logic the replay does.
- **Subprotocol `mahjong-csm-v1`.** Calling out the ruleset in the subprotocol identifier was tempting; rejected because the ruleset is per-table, not per-connection. A v2 protocol that supports both MCR and a Japanese variant doesn't need a new subprotocol name.

## Verification fixtures

These are the test cases each layer-7 implementation step must produce. They are the acceptance criteria for the wire-protocol codec (step 7.1) and the WebSocket server (step 7.2).

1. **Round-trip per message type.** For every `kind` in §"Message catalog", a checked-in JSON example serializes via the codec to byte-identical JSON and parses back to an equal Python object. (One fixture file per `kind`; 18 cases at v1.)

2. **Privacy projection on `EVENT`.** Given a `(record_event, seat) → expected_wire_event` table covering each `RecordEvent` kind with at least one seat-owner and one non-owner case, the codec produces the projected wire event. Foreign-concealed-tile leakage is asserted absent for every non-owner case.

3. **Unknown `kind` is a hard error.** Client and server both produce `ERROR { code: "unknown_kind" }` on a frame with `kind: "ZZZ_NONESUCH"`.

4. **Unknown optional field is tolerated.** Parsing a `HELLO` with an unrecognized `extra_field` succeeds; the field is dropped.

5. **Binary frame closes the WebSocket with 1003.** Integration test: client sends a binary frame; server's WS handler closes with code 1003.

6. **Frame > 16 KB closes with 1009.** Integration test.

7. **`AUTH_RESPONSE` failure shape is identical for missing-user and wrong-password.** Two AUTH_REQUEST cases — one for a username not in the DB, one for a username with a wrong password — produce byte-identical failure frames (modulo any timing-side-channel mitigations in [auth.md](auth.md)).

8. **Per-connection `seq` is strictly monotonic.** A scripted hand fixture (the layer-7 step 7.6 end-to-end fixture, projected to one seat) has `seq` values `1, 2, 3, ..., N` with no gaps and no repeats.

9. **Resume after disconnect replays missed events.** Integration test: client attaches, server sends 5 `EVENT`s, client drops, reconnects with `RESUME`, server's `ATTACHED.resume_buffer_size = 5` and the 5 events are re-sent in order followed by any outstanding `PROMPT`.

10. **Resume past seat-hold window yields a clean re-attach prompt.** Integration test: with `MAHJONG_SEAT_HOLD_SECONDS=1`, drop, wait 2s, `RESUME` — seat is released; server returns success on `RESUME` but the `ATTACH` step must be redone.

11. **Illegal action increments strike count, doesn't close.** Send an action not in the prompt's `legal_actions`; assert `ERROR { code: "illegal_action" }`, the WebSocket stays open, and on the next prompt a fresh `ACTION` is accepted.

12. **Stale `prompt_id` is rejected without losing the outstanding prompt.** Issue prompt P1, send `ACTION` with `prompt_id` of P0; server replies `ERROR { code: "stale_action" }`, P1 remains outstanding, sending a valid action for P1 succeeds.

13. **Spectator subscribes and receives only public events.** Spectator attaches mid-hand to a fixture table; assert: `SPECTATING.snapshot` has empty `concealed` on every seat; subsequent `EVENT`s carry no `seat.concealed` content for any seat (including own-draw events that, in the player path, would expose the drawer's hand); no `PROMPT` is ever sent to the spectator.

14. **Multiple spectators per table receive identical event streams.** Two spectator connections to the same table see byte-identical `EVENT` payloads (modulo per-connection `seq` and `t_server_ms`).

15. **Spectator drop does not stall the hand.** A spectator's WebSocket drops mid-hand; assert: no seat-hold timer started, no ring buffer allocated, the table manager's coroutines are unaffected, and the hand continues.

16. **Wire is byte-identical to a recorded fixture for a scripted hand.** This is the S2 exit fixture from server-plan.md §S2: a Canned sequence of keystrokes drives the TUI, the resulting WebSocket frames (server-side, recorded) match a checked-in JSONL log frame-for-frame.

Fixture 16 is the load-bearing one — it pins the whole protocol against drift in one assertion.

## Open questions

None at v1. The pinned decisions from [s2-s3-plan.md §10](../s2-s3-plan.md) cover transport, versioning, and reconnect semantics. Future v2 considerations (binary encoding, server-pushed `SeatView`s vs. event stream) live in [research-ideas.md](../research-ideas.md) if they come up.
