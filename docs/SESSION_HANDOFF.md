# Session handoff — 2026-05-22 (eve)

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 sub-steps 7.0, 7.1, 7.2 are complete and uncommitted on `main`.** S2 + S3 spec preparation landed in the prior session (morning); implementation began this session (evening).

Order so far:

1. **7.0 — `project(state, seat=None)` amendment.** State-schema spec broadened; `project_event(event, seat)` added. Six existing callsites accept the wider signature without behavior change.
2. **7.1 — Wire-protocol codec.** `mahjong.wire.codec` with `WireMessage` TypedDict union (25 shapes), `encode` / `decode`, and the `KNOWN_KINDS` enum. `WireError` hierarchy in `mahjong.wire.errors`.
3. **7.2 — WebSocket transport.** `mahjong.wire.server.WebSocketServer` wrapping `websockets` v16. Subprotocol `mahjong-v1` enforced via `process_request`; `/health` HTTP route on the same listener; 3-phase lifecycle (`start` → `stop_accepting` → `close`); per-connection `Connection` async iterator over decoded wire messages.

**Verification:** 474 tests passed (2 Linux-only skipped); ruff clean; ruff-format clean; mypy clean across 51 source files. Wire suite alone is 52 tests (41 codec + 11 real-loopback server integration).

## Where we are NOT yet

**Sub-step 7.3 — Session multiplexer — has not been started.** This was the natural next step but it's a substantial chunk (21 fixtures pinned in [session-mux.md](specs/session-mux.md), the seat state-machine, ring-buffer replay, pending-prompt survival, spectator set, conflict resolution) and the session ended before authorization to begin.

## What landed in this session

Files modified or added (uncommitted):

| Path | Change |
| --- | --- |
| `docs/specs/state-schema.md` | Amended § Per-seat projection: broadened signature to `int \| None`; added "Public (spectator) projection" + "Per-event projection" subsections; fixtures 3 / 4 / 4a / 4b. |
| `docs/specs/wire-protocol.md` | Added `shutting_down` and `rate_limit` rows to the Error-codes table. |
| `mahjong/engine/state.py` | `project()` widened to accept `seat: int \| None`; new `project_event(event, seat)`. |
| `mahjong/wire/__init__.py` | New package. |
| `mahjong/wire/errors.py` | `WireError` base + `WireFramingError`, `WireDecodeError`, `WireVersionError`. |
| `mahjong/wire/codec.py` | 25 TypedDicts; `WireMessage` union; `KNOWN_KINDS`; `encode`/`decode`. |
| `mahjong/wire/server.py` | `Connection` + `WebSocketServer`; subprotocol gate; `/health` route; lifecycle. |
| `tests/engine/test_state.py` | 15 new tests for the public projection + `project_event`. |
| `tests/wire/test_codec.py` | 41 codec tests (27 parameterized round-trips + framing + privacy). |
| `tests/wire/test_server.py` | 11 real-loopback integration tests. |
| `pyproject.toml` | Added `websockets>=12.0` runtime dependency (installed `websockets-16.0`). |
| `CHECKLIST.md` | Steps 7.0, 7.1, 7.2 ticked with fixture citations and Gate lines. |

## What remains for next session

The remaining Layer 7 sub-steps, in order:

- **7.3 — Session multiplexer.** Biggest sub-step. The `SessionMux` per table: `dict[seat, SeatSession]` + `dict[connection_id, Spectator]`. State machine for `UNBOUND ↔ LIVE ↔ HELD`; ring buffer (default 256 events); hold timer; pending-prompt future. 21 fixtures (1 state-machine, 2–7 buffer/prompt/timer, 8–9 conflict resolution, 10–11 hand-end/shutdown, 12–15 action/reconnect edge cases, 16–21 spectator). Spec is [session-mux.md](specs/session-mux.md).
- **7.4 — `HumanAdapter`.** The `SeatAdapter` impl bridging session-mux to the seat-port. Should slot into the existing table manager without changes (regression: the four-`CannedAdapter` walking-skeleton fixture from Step 4.2 must still pass). Spec is [session-mux.md § The HumanAdapter](specs/session-mux.md) + [seat-port.md § The interface](specs/seat-port.md).
- **7.5 — TUI client.** Textual app, headless `Pilot` tests, bilingual EN/ZH. Spec is [tui-client.md](specs/tui-client.md).
- **7.6 — End-to-end S2 fixture.** Scripted keystrokes → server → engine → byte-identical record. This is the S2 gate.

Then Layer 8 (sub-steps 8.1 through 8.6) is the full S3 surface — SQLite, auth, persistence, multi-table, lifecycle.

## Pinned decisions reaffirmed this session

- **Codec is dumb.** Projection is the *caller's* job (session-mux applies `project_event` before handing to the codec). The spec hinted at a defense-in-depth re-projection inside the codec; I deliberately left that out — single-responsibility, projection bug location is unambiguous. Add the assertion if/when we see it bite.
- **Subprotocol enforcement single point.** The websockets library's own subprotocol negotiation is permissive (accepts handshakes with no matching subprotocol). We enforce strictly via `process_request` returning HTTP 400 if `mahjong-v1` isn't in the offered list.
- **`stop_accepting()` is non-blocking; `close()` is blocking.** Splitting the two halves is what makes drain semantics correct — listener stops accepting immediately, existing connections finish naturally, then `close()` blocks at process-exit. Conflating them caused the test hang during development.

## Outstanding questions / decisions for the user

- **Proceed to 7.3?** Pending answer. 7.3 is genuinely large (21 fixtures + state machine); needs explicit go-ahead.
- **Commit the uncommitted work?** This session's changes are all on `main`, uncommitted. Suggested message: `Layer 7 sub-steps 7.0–7.2: public projection, wire codec, WebSocket transport`.
- **Memory consolidation recommended** at session start (5 sessions over 1 day since last consolidation). Run `/extract-learnings` in consolidation mode when convenient.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status.md](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) (memory).
- [ ] Decide whether to commit the uncommitted 7.0–7.2 work before starting 7.3 (recommended).
- [ ] Open [session-mux.md](specs/session-mux.md) and re-skim the 21 fixtures.
- [ ] Start at sub-step 7.3, tests-first per project TDD discipline.
- [ ] Run the verification ladder before ticking the Gate.
