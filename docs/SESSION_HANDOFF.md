# Session handoff — 2026-05-24 (end of 7.6.i)

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 is 9 of 10 effective sub-steps complete.** This session decided to **skip 7.5c.iv (bilingual)** and route directly to **7.6 (S2 exit gate)**, broken into sub-steps. **7.6.i (HAND_END double-emit fix)** landed; it was a precondition for the orchestrator work that comes next.

| # | Step | Commit | Notes |
| --- | --- | --- | --- |
| 7.0 | Public projection amendment | `aa35890` (in 7.2 bundle) | `project(state, seat: int \| None)`, `project_event` |
| 7.1 | Wire codec | `aa35890` | 25 TypedDicts, `encode`/`decode`, `KNOWN_KINDS` |
| 7.2 | WebSocket transport | `aa35890` / `4cd666f` | `WebSocketServer`, subprotocol gate, `/health` |
| 7.3 | Session multiplexer | `e74d265` | `TableSessions`, `SeatSession`, `Spectator`, ring buffer, hold timer |
| 7.4 | HumanAdapter | `a49bcf6` | `SeatAdapter` impl wrapping a `SeatSession` |
| 7.5a | Web-client walking skeleton | `694b790` | Browser + Lit + WS round-trip; visually verified |
| 7.5b | Pane-toggle shell + light theme | `9fb7037` | `<table-page>` host, four pane elements, Alt-modifier hotkeys, theme system |
| 7.5c.i | Snapshot rendering | `f566340` | `SeatView` → ASCII table; tile styling; Unicode toggle |
| 7.5c.ii | applyEvent reducer | `e376cde` | Live SeatView mutation per EVENT; demo drives real engine |
| 7.5c.iii | PROMPT bar + ACTION round-trip + illegal-action banner | `7f871aa` | Spec fixtures 7, 8, 9 GREEN via Playwright async API |
| ~~7.5c.iv~~ | ~~Bilingual EN/ZH rendering~~ | **DEFERRED to a later edition** | User decided 2026-05-24 to skip and route to the S2 gate. Tracked separately; not blocking S2. |
| **7.6.i** | **HAND_END double-emit fix** | **THIS SESSION** | **Precondition for 7.6.ii orchestrator; +6 tests, 526 passing repo-wide** |
| 7.6.ii | Orchestrator (`mahjong/web/server.py`) + Fixture F1 (byte-identical record) | pending | Real `TableSessions` + `manager.run_hand` + `HumanAdapter`/`CannedAdapter` behind WS |
| 7.6.iii | Fixtures F2 + F3 (drop/reconnect inside-hold + past-hold autopass) | pending | |
| 7.6.iv | Fixture F4 (spectator subscription, public-only projection) | pending | Closes Layer 7 and the S2 milestone |

**Verification at end of session:** ruff clean · ruff-format clean · mypy clean (57 source files; unchanged) · **526 tests pass repo-wide** (520 prior + 6 new HAND_END routing tests; 2 Linux-only skipped). No behavioral change visible to the existing 7.5c.iii demo (`mahjong.web.demo` doesn't drive a real `TableSessions` yet, so the HAND_END path isn't exercised end-to-end until 7.6.ii).

## Decisions reached this session

- **(28) Skip 7.5c.iv (bilingual) for now; flag for a later edition.** The S2 exit gate is more load-bearing; bilingual is cosmetic polish. When picked back up: pull user-visible strings through `t(key, locale)` against `mahjong/web/static/locales/{en,zh,bilingual}.json`; add `Alt+L` toggle alongside `Alt+T` / `Alt+U`. Strings live in [mahjong/web/static/prompt.js](../mahjong/web/static/prompt.js) (action labels) and [mahjong/web/static/app.js](../mahjong/web/static/app.js) (pane headers, status text). Spec fixture 15 still pins acceptance.
- **(29) 7.6 splits across four sub-sessions, one fixture per commit** (i: HAND_END fix; ii: orchestrator + F1; iii: F2 + F3; iv: F4). Matches the rhythm of 7.5c.i/ii/iii. Confirmed 2026-05-24 with the user.
- **(30) Orchestrator lives at `mahjong/web/server.py`** (sibling to `web/demo.py`). `demo.py` stays as the toy reference handler; `server.py` is the production WS+static server that knows about `TableSessions`. Confirmed 2026-05-24.
- **(31) HAND_END frame is owned by `observe()`, not `left()`.** `SeatSession.observe()` intercepts record events with `event == "HAND_END"`, strips the wrapper fields (`event`/`seq`/`turn_index`/`phase`/`ts`), and emits a top-level HAND_END wire frame with the rest as `terminal`. `Spectator.send_event()` does the symmetric thing for spectators. `HumanAdapter.left("HAND_ENDED")` calls a new `SeatSession.unbind_after_hand_end()` that does the resolve + teardown WITHOUT sending — avoiding the double-emit. `SeatSession.hand_ended(terminal, next_hand_seq)` is retained for callers that have a pre-built terminal and want `next_hand_seq` (orchestrator post-hoc), but the manager-via-observe path is now the single sender.

## What this session built

### 7.6.i — HAND_END double-emit fix

**Problem (carried from 7.3):** `SeatSession.observe()` blindly EVENT-wrapped every record event, including HAND_END. Per [wire-protocol.md § HAND_END](specs/wire-protocol.md), HAND_END is its own top-level frame with a `terminal` payload, not `{"kind": "EVENT", "event": {...record-format HAND_END...}}`. Then `HumanAdapter.left("HAND_ENDED")` called `SeatSession.hand_ended(terminal={}, ...)` which sent a SECOND HAND_END frame, this one with an empty `terminal`. Net: two HAND_ENDs per hand, both wrong-shaped.

**Fix ([mahjong/sessions/mux.py](../mahjong/sessions/mux.py)):**

- Module-level `_terminal_from_record(record_event)` strips wrapper fields (`event`, `seq`, `turn_index`, `phase`, `ts`), yielding the `terminal` payload.
- `Spectator.send_event(record_event, hand_index)` checks `record_event["event"] == "HAND_END"` and dispatches to `send_hand_end(...)` instead of EVENT-wrapping.
- `SeatSession.observe(record_event)` does the same for the player path. LIVE → `_emit_hand_end(terminal)` (new helper that wraps `_send_hand_end` with `next_hand_seq=None`); HELD → buffered with a `"HAND_END"` discriminator (see below).
- **Buffer type changed** from `deque[dict]` to `deque[tuple[str, dict]]`. Each entry is `(wire_kind, payload)`: `("EVENT", projected_event)` or `("HAND_END", terminal)`. New `_append_buffer(entry)` helper. Replay path branches on `kind` and calls the right emitter. Required because a HELD seat receiving HAND_END must replay it as a HAND_END frame on reconnect, not an EVENT frame.
- New `SeatSession.unbind_after_hand_end()` — resolves any pending prompt with `SeatHoldExpired("hand_ended")` and tears the seat down to UNBOUND, WITHOUT sending HAND_END. Factored out shared `_resolve_pending_and_teardown()`.
- `SeatSession.hand_ended(terminal, next_hand_seq)` is preserved for the orchestrator path (callers that have a pre-built terminal and want to attach `next_hand_seq`) but is no longer the manager-driven sender. Docstring updated.

**Fix ([mahjong/adapters/human.py](../mahjong/adapters/human.py)):**

- `HumanAdapter.left("HAND_ENDED")` now calls `session.unbind_after_hand_end()` instead of `session.hand_ended(terminal={}, next_hand_seq=None)`. Comment explains the double-emit problem this avoids.

**New tests ([tests/sessions/test_hand_end_routing.py](../tests/sessions/test_hand_end_routing.py)):** 6 tests pinning the contract.

- `test_observe_hand_end_live_sends_top_level_hand_end_frame` — record event → single HAND_END frame with correctly-stripped `terminal`.
- `test_observe_hand_end_does_not_emit_event_frame` — defense-in-depth: no EVENT frame is emitted for HAND_END.
- `test_observe_hand_end_while_held_buffers_and_replays_as_hand_end` — HELD-state HAND_END replays as HAND_END on reconnect, not EVENT.
- `test_spectator_receives_hand_end_as_top_level_frame` — symmetric for spectators via `fanout_event`.
- `test_human_adapter_left_hand_ended_does_not_double_emit` — after `observe()` sends HAND_END, `left("HAND_ENDED")` adds nothing but still unbinds the session.
- `test_human_adapter_left_hand_ended_without_prior_observe_still_unbinds` — safety net: even if HAND_END never flowed through (drop before hand-end), teardown is mandatory so SeatSession is reusable for the next hand.

The existing `test_state_machine.py::test_hand_end_while_held_unbinds_seat` and `test_spectators.py` HAND_END test still pass — they use the `hand_ended(terminal, ...)` / `fanout_hand_end(...)` entry points which keep working.

## Known limitations carried forward

- **The 7.5c.iii demo handler still isn't a real table manager.** It drives `apply_action` + `diff_to_events` directly with an in-handler PROMPT/ACTION loop. The bridge to a real `TableManager` + `TableSessions` + `HumanAdapter` lands in 7.6.ii. **Now unblocked by 7.6.i.**
- **Phase-transition prompt clearing** (carried). Will surface again in 7.6.ii when the real `TableManager` is in the loop.
- **Opponent meld formation can't reconstruct which specific concealed tiles left.** Unchanged from 7.5c.ii.
- **Concealed tile sorting after DRAW.** Unchanged from 7.5c.ii.
- **Bilingual EN/ZH rendering** is now an explicitly-deferred polish item (see decision 28). Not blocking S2.

## What remains for next session

### 7.6.ii — orchestrator + Fixture F1 (the big one)

**New module:** `mahjong/web/server.py` — production WS+static server that owns `TableSessions`, accepts ATTACHes, constructs `HumanAdapter`s, and composes them with `CannedAdapter`s in a call to `manager.run_hand(...)`.

**Pieces to wire together** (most exist; just compose them):

- `mahjong/wire/server.py` `WebSocketServer` (7.2) — already supports static-dir serving (7.5a).
- `mahjong/sessions/mux.py` `TableSessions` (7.3) — owns per-table `SeatSession`s + spectators. Has `attach(sink, user_id)`, `spectate(...)`, `handle_inbound(...)`, `fanout_event(...)`. Now HAND_END-correct (7.6.i).
- `mahjong/adapters/human.py` `HumanAdapter` (7.4) — wraps a `SeatSession`. Now teardown-correct (7.6.i).
- `mahjong/adapters/canned.py` `CannedAdapter` — exists, takes `actions: list[Action]`.
- `mahjong/table/manager.py` `run_hand(adapters=[...], ...)` — drives one hand with four adapters, writes a record.

**Sketch of the orchestrator's responsibilities:**

1. Boot a `WebSocketServer` with `static_dir=mahjong/web/static` (same as `demo.py`).
2. Hold a single `TableSessions` instance (one table for v1 — multi-table is Layer 8).
3. On WS connect: peek the first frame. If `ATTACH`, route through `TableSessions.attach(sink, user_id)`; spawn a `HumanAdapter` for that seat. If `SPECTATE`, route through `TableSessions.spectate(sink, user_id)`.
4. When all 4 seats are filled (or after a configurable "start" trigger; for the F1 test, just start immediately with 1 human + 3 canned), construct the adapter list and call `manager.run_hand(...)` in a background task.
5. Inside `run_hand`, every record event flows through `adapter.observe(event, view)`. For HumanAdapter, that hits `SeatSession.observe(event)` which routes to wire EVENT or HAND_END. For CannedAdapter, observe is a no-op.
6. Spectators must also see events. The orchestrator needs to call `TableSessions.fanout_event(event)` on EVERY event the engine emits, *in addition* to the manager's adapter-observe fanout. **Watch for double-emit on the player side here** — `manager._fanout_observe` already drives `adapter.observe`, which already hits `SeatSession.observe`. So `fanout_event` would re-emit on players. Two options to consider:
   - **(a)** Pass the orchestrator's `TableSessions.fanout_event` as an event observer in addition to the adapters. Build a `_fanout_observe_and_fanout` helper that observes adapters AND fans to spectators-only. (Cleanest.)
   - **(b)** Restructure `manager.run_hand` to call `TableSessions.fanout_event(event)` directly and skip `adapter.observe` for HumanAdapter. Bigger refactor.
   - **(c)** Let the orchestrator iterate the record post-hoc and replay to spectators after the hand. Bad for live-spectating.
   - Recommend (a). Worth confirming with the user before writing.

**Fixture F1 (write-first):**

- Spec: `tests/web/test_e2e_s2.py` (new file).
- A Playwright `page` connects to a real `mahjong.web.server` instance running in the test process; sends ATTACH for seat 0 with a canned `user_id`/identity; then drives a hand via scripted keystrokes (use the same Playwright async fixture pattern from `tests/web/conftest.py`).
- Seats 1-3 are `CannedAdapter`s with action lists frozen as `tests/_fixtures/s2_e2e_seat{1,2,3}_actions.json`.
- Server records to a tempfile; assertion: tempfile bytes match `tests/_fixtures/s2_e2e_record.jsonl` byte-for-byte.
- **Seed everything.** `manager.run_hand(seed=...)` is the deterministic root. `_now_ts()` must be stubbed to a fixed timestamp so the record is reproducible — currently it reads `datetime.now(UTC)`, so this needs a clock-injection seam. Decide whether to monkeypatch or to add a `now_fn` parameter to `run_hand`.
- **Open question for the F1 author:** which exact hand to script? Either contrive a short one (toy fixture, ~10 events) or use one of the S0 walking-skeleton fixtures and add a human-driven seat 0. The shorter, the easier the byte-identical assertion is to update when the wire format evolves.

### 7.6.iii — Fixtures F2 + F3 (drop/reconnect)

- F2: drop seat 0's socket mid-hand, reconnect within hold window, hand completes without `auto_pass` markers in the record.
- F3: `MAHJONG_SEAT_HOLD_SECONDS=1`, drop, wait 2s, `AutoPassAdapter` substitution, record marks `replaced_by_auto_pass`.

### 7.6.iv — Fixture F4 (spectator)

- A fifth Playwright page subscribes via SPECTATE; assert it receives public-projected EVENTs (no own-concealed leaks) and never a PROMPT, even when seat 0 is on-turn. Closes S2.

## Outstanding questions / decisions for the user

- **Orchestrator/spectator fanout shape** (see "open question" inside 7.6.ii above). Pick (a), (b), or (c) before writing the orchestrator.
- **Hand-script for F1**: contrived short hand vs. an S0 fixture extended with a human seat. Lean short.
- **Clock seam for `_now_ts`**: monkeypatch in the test or add `now_fn=` to `run_hand`. Lean monkeypatch.
- **Phase-transition prompt clearing**: still TBD; will revisit once F1 is exercising the real loop.
- **Chat wire-protocol amendment**: still required before `<chat-pane>` does anything real. Not blocking S2.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) (will be refreshed with 7.6.i + the decisions above on next consolidation).
- [ ] Verify `git log --oneline -5` shows the 7.6.i commit at HEAD.
- [ ] Run `.venv/bin/python -m pytest` and confirm 526 passing, 2 Linux-only skipped.
- [ ] Pick orchestrator/spectator fanout shape (recommendation: option (a)) before writing `mahjong/web/server.py`.
- [ ] Start 7.6.ii tests-first: scaffold `tests/web/test_e2e_s2.py` with a minimal one-hand fixture, watch it fail, then build out `mahjong/web/server.py` until it passes.
