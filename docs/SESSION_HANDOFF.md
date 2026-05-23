# Session handoff — 2026-05-23

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 is 4 of 7 sub-steps complete. All committed on `main`.** Layer 7 has 7 sub-steps total (7.0–7.6); 7.5 is next.

| # | Step | Commit | Notes |
| --- | --- | --- | --- |
| 7.0 | Public projection amendment | `aa35890` (in 7.2 bundle) | `project(state, seat: int \| None)`, `project_event` |
| 7.1 | Wire codec | `aa35890` | 25 TypedDicts, `encode`/`decode`, `KNOWN_KINDS` |
| 7.2 | WebSocket transport | `aa35890` / `4cd666f` | `WebSocketServer`, subprotocol gate, `/health` |
| **7.3** | **Session multiplexer** | **`e74d265`** | **`TableSessions`, `SeatSession`, `Spectator`, ring buffer, hold timer** |
| **7.4** | **HumanAdapter** | **`a49bcf6`** | **`SeatAdapter` impl wrapping a `SeatSession`** |
| 7.5 | TUI client | pending | Textual app, 5 screens, bilingual EN/ZH |
| 7.6 | End-to-end S2 fixture | pending | Scripted keystrokes → server → engine → record |

**Verification at end of session:** ruff clean · ruff-format clean · mypy clean (55 source files) · **509 tests pass repo-wide** (2 Linux-only skipped on macOS). S0 walking-skeleton regression (Step 4.2 gate) still byte-identical.

## What landed this session (7.3 + 7.4)

### Step 7.3 — Session multiplexer (`e74d265`)

Files added:

| Path | Purpose |
| --- | --- |
| `mahjong/sessions/__init__.py` | Package exports |
| `mahjong/sessions/timers.py` | `IdempotentTimer` wrapping `asyncio.call_later` |
| `mahjong/sessions/mux.py` | `TableSessions`, `SeatSession` (UNBOUND↔LIVE↔HELD), `Spectator`, `OutboundSink` Protocol, `SeatPrompt`, `SeatHoldExpired`, `AttachOutcome`, `SpectateOutcome` |
| `tests/sessions/__init__.py` | (empty) |
| `tests/sessions/conftest.py` | `FakeSink`, `make_table_sessions`, `make_seat_session`, `make_prompt` |
| `tests/sessions/test_state_machine.py` | Fixtures 1, 8, 9, 10, 14, 15 |
| `tests/sessions/test_ring_buffer.py` | Fixtures 2, 3 |
| `tests/sessions/test_pending_prompt.py` | Fixtures 4, 5, 6, 7, 12, 13 |
| `tests/sessions/test_shutdown.py` | Fixture 11 |
| `tests/sessions/test_spectators.py` | Fixtures 16, 17, 18, 19, 20, 21 |

28 test methods covering all 21 spec fixtures from [session-mux.md § Verification fixtures](specs/session-mux.md).

### Step 7.4 — HumanAdapter (`a49bcf6`)

Files added:

| Path | Purpose |
| --- | --- |
| `mahjong/adapters/human.py` | `HumanAdapter` — `SeatAdapter` Protocol impl against a `SeatSession` |
| `tests/adapters/test_human_adapter.py` | 7 tests pairing adapter + real `SeatSession` + `FakeSink` |

The adapter owns just the translation: stable `prompt_id` from `(seat, turn_index, phase)`, monotonic-to-Unix-epoch-ms deadline conversion, `SeatHoldExpired` → `SeatError` exception remap. `seated()` is a no-op (ATTACHED is sent at bind time, not hand-start). `observe()` ignores the `view` arg (session re-projects from the canonical event).

`left(reason)` mapping:

- `HAND_ENDED` → `session.hand_ended(...)`
- `TABLE_CLOSED` → `session.shutdown(reason="table_closed")`
- `REPLACED` → `session.shutdown(reason="replaced_by_autopass")`
- `ERROR` → `session.shutdown(reason="internal_error")`

## Pinned decisions reaffirmed or added this session

1. **Codec is dumb (7.1).** Projection is the caller's job. Session-mux applies `project_event` before handing events to the codec. Single-responsibility; bug location is unambiguous.
2. **`stop_accepting()` non-blocking; `close()` blocking (7.2).** Splits the two halves of `Server.close()` so drain semantics work — listener stops immediately, existing connections finish naturally, `close()` blocks at process exit. Conflating them caused a test hang during dev.
3. **Mux owns outbound `seq`, not the sink (7.3).** Each `_Outbound(sink, next_seq)` pair holds its own counter; on takeover/resume, a new `_Outbound` is allocated so the new connection starts at seq=1. The buffer stores already-*projected* event payloads (no wire envelope, no seq); replay wraps them with fresh seqs at send time. This matches the spec's "outer seq is a fresh one for the new connection" rule.
4. **`SeatHoldExpired` future-resolution path is exception-only (7.3).** Three resolution paths race in `SeatSession.decide()`: inbound ACTION → `set_result`, prompt deadline → `set_result(default_action)`, seat-hold timer → `set_exception(SeatHoldExpired)`. Whichever wins; the others short-circuit on `future.done()`. Fixture 5 vs fixture 7 distinguishes prompt-deadline-first (default action) from seat-hold-first (SeatError).
5. **Shutdown of HELD seat with pending prompt defaults rather than errors (7.3).** Fixture 11 commentary says "either is acceptable"; we chose default to match prompt-deadline outcome — table manager applies `default_action` on drain, no special SeatError handling needed.
6. **HumanAdapter `seated()` is a no-op (7.4).** The `ATTACHED` frame is sent by `TableSessions.attach()` at *bind time*, not when the table manager calls `adapter.seated()` at *hand-start time*. The seat-port lifecycle hook does nothing for HumanAdapter beyond stashing `ctx` for prompt-id derivation. If multi-hand orchestration ever needs to re-emit ATTACHED between hands, the orchestrator (7.6+) will drive that, not the adapter.
7. **HumanAdapter ignores the `view` arg to `observe` (7.4).** The seat-port hands both `event` and `view` to adapters; the session-mux re-projects from the canonical event via `project_event(event, seat)`. Forwarding both would be two sources of truth for "what does this seat see?" — keep the engine's projection rule as the only one.

## Known limitation deferred

**HAND_END double-emit risk.** The engine emits `HAND_END` as a record event during the run-loop, and `SeatSession.observe()` would wrap it in an `EVENT` wire frame — but per `wire-protocol.md`, HAND_END is its own top-level frame, not EVENT-wrapped. Not blocking 7.3/7.4 (unit tests fan events explicitly via `TableSessions.fanout_hand_end`); will surface in **7.6** end-to-end. Likely fix: filter HAND_END out of `SeatSession.observe()`'s EVENT path, since the HAND_END frame is delivered via the separate `fanout_hand_end` codepath.

## What remains for next session

The remaining Layer 7 sub-steps, in order:

- **7.5 — TUI client.** Textual `App` wiring the wire-protocol `Connection` to an interactive renderer. Per [CHECKLIST.md § 7.5](../CHECKLIST.md):
  - Tests: `Pilot`-driven scripted-keystroke fixture per screen (login, lobby, player_table, spectator_table, hand_end); spectator-privacy defense-in-depth (rendering refuses to draw concealed tiles even if wire sends them); bilingual EN/ZH label rendering per tile and action; crash-resistance (broken render → placeholder, WebSocket stays open).
  - Files: `mahjong/tui/app.py` (owns `ConnectionManager`), `mahjong/tui/screens/{login,lobby,player_table,spectator_table,hand_end}.py`, `mahjong/tui/render/` (tile rendering, meld layout, discard-pile widget), `mahjong/cli/tui.py` (`python -m mahjong tui` entry).
  - Largest sub-step in Layer 7 by surface area. **Consider splitting authorization** — one screen at a time vs. all at once — before starting.
- **7.6 — End-to-end S2 fixture.** The S2 milestone gate. A real server hosting a hand with a TUI client driving one seat and the others as `CannedAdapter`s; record byte-identical to a checked-in fixture. Closes Layer 7 and the S2 milestone.

Then Layer 8 (sub-steps 8.1–8.6) is the full S3 surface — SQLite, auth, persistence, multi-table, server-lifecycle.

## Outstanding questions / decisions for the user

- **Authorize 7.5 in one go, or screen-by-screen?** Pending. 7.5 is the largest 7.x sub-step.
- **Memory consolidation still recommended** (6 sessions over 1 day at start of this session). Run `/extract-learnings` in consolidation mode when convenient.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status.md](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) (memory).
- [ ] Verify `git log --oneline -5` shows `a49bcf6` (Step 7.4) at HEAD.
- [ ] Run `.venv/bin/python -m pytest` and confirm 509 passing, 2 Linux-only skipped.
- [ ] Decide 7.5 scope (all screens or one at a time) before starting.
- [ ] Open [tui-client.md](specs/tui-client.md) and re-skim the screen-by-screen contract.
- [ ] Start at 7.5 tests-first per project TDD discipline. Note: TUI cosmetics are explicitly carved out of strict TDD in CLAUDE.md — only the protocol/render-privacy/crash-resistance contracts need tests-first.
- [ ] Address the HAND_END double-emit limitation (above) before 7.6 e2e.
