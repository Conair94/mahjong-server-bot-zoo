# Spec 38 — Table chat

Ephemeral, table-scoped text chat between the humans (and spectators, read-
only) at a table: a `CHAT` inbound frame relayed as a `CHAT_MESSAGE`
broadcast, rendered in the existing `Alt+C` chat pane (a stub since the
pane architecture landed; this fills it in).

Builds on:
- [wire-protocol.md](wire-protocol.md) — two new message kinds; both go in
  `codec.KNOWN_KINDS` **with round-trip tests** (the silent-drop rule).
- [session-mux.md](session-mux.md) § Inbound dispatch —
  `TableSessions.handle_inbound` is the single in-table router **both**
  server hosts (registry tables and the single-table `WebOrchestrator`)
  delegate to, so one implementation covers both (mirror-both rule
  satisfied structurally).
- Spec 34's seat-name snapshot annotation — `CHAT_MESSAGE` carries only the
  `seat`; the client labels it with the name it already has.
- FB-16's `isEditableTarget` window-keydown guard — typing in the chat input
  must never fire game shortcuts; the guard pierces shadow DOM via
  `composedPath()[0]` and covers any editable element, including this one.

## Goals

- Seated humans can send short text messages; everyone at the table (all
  LIVE seats + spectators) sees them with the sender's name, in order.
- Works mid-hand and between hands (phase-2 inbound loop covers both).
- Zero persistence, zero history replay: a reconnecting client starts with
  an empty pane. Chat is table talk, not a record.

## Non-goals

- **No lobby/global chat** (table-scoped only; the lobby phase-1 loop
  rejects unknown kinds, which is correct — there is no table yet).
- **No spectator sending** (read-only; revisit if spectating gets real use).
- **No buffering for HELD seats** (a dropped client misses chat sent while
  away — consistent with "table talk", and the ring buffer stays
  events-only).
- **No moderation/profanity tooling, no rate limiting** beyond a length cap
  (invite-only friends server; the connection-wide inbound cap already
  bounds abuse). Revisit at S7 ops hardening alongside DEF-05.
- **No bot chatter** (tempting; later).

## Wire schema

Client → server:

```json
{"kind": "CHAT", "text": "nice kong"}
```

Server → everyone at the table (per-connection `seq`, like every outbound):

```json
{
  "kind": "CHAT_MESSAGE",
  "seq": 41,
  "table_id": 3,
  "hand_index": 2,
  "seat": 1,
  "ts": "2026-06-11T22:10:00.000Z",
  "text": "nice kong"
}
```

Validation (server, before broadcast):

- `text` must be a `str`; stripped of leading/trailing whitespace it must be
  1–500 chars. Violations → `ERROR {code: "chat_invalid"}` to the sender
  only, no broadcast.
- Sender must own a seat on this table (`_seat_owning`): spectators and
  unrecognized sinks get `ERROR {code: "chat_not_seated"}`.
- Control characters (`\x00`–`\x1f` except none allowed — text is a single
  line) are stripped server-side; the client additionally renders text as
  text nodes (Lit interpolation), never HTML.

## Delivery

`TableSessions.handle_inbound` grows a `CHAT` branch →
`_broadcast_chat(sender_seat, text)`:

- every **LIVE** `SeatSession` gets the frame on its own outbound seq
  (HELD/UNBOUND skipped — no buffering, per non-goals);
- every spectator gets it via its own outbound counter;
- the sender receives their own message back (single source of render
  truth — the client appends only on `CHAT_MESSAGE`, never optimistically).

## Client

- `<chat-pane>` (Alt+C): scrollback list + input + Send. Submits on Enter
  (the FB-16 guard keeps that Enter out of the game keymap). Emits
  `chat-send {text}`; the app shell sends the `CHAT` frame.
- App shell dispatch: `CHAT_MESSAGE` → `table-page.addChatMessage(frame)`;
  the table-page keeps a `_chatLog` (capped at 200 entries) and binds it
  into the pane. Sender label: `seats[seat].name` from the game-pane's
  live view, falling back to `Seat N`; resolved at append time (names are
  hand-stable).
- A message arriving while the chat pane is closed increments an unread
  badge on the pane indicator; opening clears it. *(v1: indicator only —
  the `[C]` in the header turns accent-red; no count.)*

## Verification fixtures

1. Codec round-trip for `CHAT` and `CHAT_MESSAGE` (KNOWN_KINDS rule).
2. Mux: seated sender → every LIVE seat **and** spectator receives one
   `CHAT_MESSAGE` with correct `seat`/`text` and their own `seq`; sender
   included; HELD seat receives nothing (and nothing lands in its ring
   buffer).
3. Mux: empty / non-string / >500-char text → `chat_invalid` to sender,
   no broadcast. Spectator sender → `chat_not_seated`.
4. Playwright (real frame dispatch): Alt+C opens the pane; typing + Enter
   emits an inbound `CHAT` frame with the typed text; an injected
   `CHAT_MESSAGE` renders with the seat label; typing `h`/`Space` in the
   chat input fires **no** ACTION frame (FB-16 regression).

## Open questions

- Spectator send (and how to label spectators) — when spectating gets use.
- Persist last N lines across reconnect — only if players ask; would ride
  the snapshot, not the ring buffer.
