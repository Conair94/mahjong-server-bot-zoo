# Session handoff — 2026-05-24 (end of Layer 7 / S2)

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 is complete. S2 milestone is reached.** This session landed **7.6.iii (F2 + F3)** and **7.6.iv (F4 + spectator fanout plumbing)** as one combined commit. All four end-to-end fixtures pass; the orchestrator hosts a real hand, supports drop/reconnect, escalates to autopass on persistent disconnect, and broadcasts publicly-projected events to spectators.

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
| ~~7.5c.iv~~ | ~~Bilingual EN/ZH rendering~~ | **DEFERRED** | Decision 28; not blocking S2 |
| 7.6.i | HAND_END double-emit fix | `9ed9370` | Routed HAND_END as top-level frame; +6 tests |
| 7.6.ii | Orchestrator + F1 (byte-identical record) | `de15be7` | `mahjong/web/server.py`; +2 tests |
| **7.6.iii + 7.6.iv** | **F2 + F3 + F4 + spectator fanout plumbing** | **THIS SESSION (`2337042`)** | **+3 tests; 531 passing repo-wide; Layer 7 / S2 complete** |

**Verification at end of session:** ruff clean · ruff-format clean · mypy clean (58 source files; unchanged) · **531 tests pass repo-wide** (528 prior + 3 new e2e tests; 2 Linux-only skipped). All four S2 fixtures (F1 byte-identical, F2 drop/reconnect, F3 drop→autopass, F4 spectator) GREEN. No regressions in S0 walking-skeleton, 7.5c.iii prompt-bar, 7.6.i HAND_END routing, or 7.6.ii orchestrator.

## Decisions reached this session

- **(38) F3 framing corrected mid-session: spec-aligned, not 'no-markers'.** The 7.6.ii handoff posed F3 as choosing between (a) defaults-without-marker and (b) proper autopass marker. Discovered when writing F3 that (a) was never achievable — `seat-port.md` § Failure modes (lines 13, 144-149, 181) is explicit: every prompt that hits its deadline gets `timeout: true` written on the resulting event, counts a strike, and after `strike_limit` strikes the seat is swapped to `AutoPassAdapter` whose events carry `auto_pass: true`. The existing strike-escalation path already produces the correct shape — no new `on_hold_expired` plumbing was needed. F3 now asserts the documented escalation pattern. **Lesson for future planning:** when the framing of a choice gets ambiguous, read the spec for the prior art before committing — I bounced through a failing test before going back to confirm spec intent.
- **(39) Order F4 → F2 → F3 was the right call.** F4 required the only manager-touching plumbing change (`event_callback`), so building it first established the manager-shape decision before F2/F3 confirmed the existing infra worked. F2 and F3 needed no manager changes at all — just orchestrator constructor knobs (`hold_seconds`, `strike_limit`) wired through to `TableSessions` / `mgr.run_hand`.
- **(40) `event_callback` is the spectator seam.** Added as an optional kwarg to `mgr.run_hand`; fires once per record event from inside `_fanout_observe` after the adapter gather completes. Bounded by the same `observe_timeout_seconds` and silently absorbs errors — same independence guarantee as adapters. The orchestrator wires it to a new `TableSessions.fanout_event_to_spectators(record_event)` (spectator-only — does NOT iterate seats, since the manager's adapter loop already drives `HumanAdapter.observe` → `SeatSession.observe`). Two callers for spectator fanout now exist: `fanout_event` (seats + spectators, for old code paths and tests) and `fanout_event_to_spectators` (the orchestrator's path). Both correct in their contexts.
- **(41) Race-avoidance in F3 via short `decide_timeout_seconds=0.1`.** The race between `SeatSession._on_prompt_deadline` and the manager's `asyncio.wait_for` timeout (both armed off the same prompt deadline) does fire — manager's wait_for wins, producing `timeout: true` on the event. This is correct per spec § 144-149 ("does not return by `prompt.deadline`" → timeout). The race isn't a bug; it's the spec's design. F3 sets `decide_timeout_seconds=0.1` to make the test run in ~3s (≈30 seat-0 prompts).

## What this session built

### 7.6.iv plumbing — `event_callback` + spectator-only fanout

**[mahjong/table/manager.py](../mahjong/table/manager.py):**

- New type alias `EventCallback = Callable[[dict[str, Any]], Awaitable[None]]`.
- `run_hand` accepts `event_callback: EventCallback | None = None`. Threaded through `_step_discard`, `_step_claim_window`, `_apply_all_pass`.
- `_fanout_observe` now accepts `event_callback`; after the adapter `asyncio.gather` completes, calls `event_callback(event)` once with the same per-observe timeout and silent error handling. Preserves the manager's independence guarantee (one slow consumer doesn't block others).

**[mahjong/sessions/mux.py](../mahjong/sessions/mux.py):**

- `TableSessions.fanout_event_to_spectators(record_event)` — new method. Iterates `self._spectators` only; calls `Spectator.send_event(record_event, hand_index)` which handles both EVENT (project_event(seat=None)) and HAND_END (wire HAND_END frame) shapes.
- `TableSessions.fanout_event` refactored to delegate spectator-fanout to the new method. Same external behavior; preserved for callers that drive both seat and spectator fanout themselves (legacy / direct tests).

**[mahjong/web/server.py](../mahjong/web/server.py):**

- `WebOrchestrator.__init__` accepts `hold_seconds` (default `DEFAULT_HOLD_SECONDS` = 60s) and `strike_limit` (default 3). Both wired through to `TableSessions` / `mgr.run_hand` respectively.
- `_run_hand` passes `event_callback=self._sessions.fanout_event_to_spectators` to `mgr.run_hand`. This is the orchestrator → spectator path.

### F2 — drop and reconnect within hold

**[tests/web/test_e2e_s2.py](../tests/web/test_e2e_s2.py)** `test_s2_e2e_drop_and_reconnect_within_hold_is_byte_identical`:

- Player opens WS, ATTACHes, receives the first PROMPT, drops the WS WITHOUT sending ACTION. Server's `_handler` exits its `async for msg in conn` loop and calls `on_socket_dropped` → seat goes HELD with prompt outstanding.
- Player reconnects (fresh WS), sends ATTACH with same user_id (via `_fixed_identity` factory injecting `u_test`). Server's `TableSessions.attach` hits the HELD + same-user resume path, replays buffer (empty in this scenario), re-emits the same PROMPT via `_reprompt_if_pending`.
- Player echoes `default_action` on every PROMPT to hand end.
- Assertion: record bytes byte-identical to F1's fixture. Drop/reconnect was invisible to the record because no engine events fire during the wire-level transition.
- `hold_seconds=5.0` (generous; reconnect happens in ~100 ms after a small sleep).

### F3 — drop without reconnect; strike → autopass

**[tests/web/test_e2e_s2.py](../tests/web/test_e2e_s2.py)** `test_s2_e2e_drop_without_reconnect_strikes_then_autopasses`:

- Player connects, ATTACHes, closes WS without ever sending ACTION.
- Hand proceeds. Each seat-0 prompt: manager's `_decide_or_default` `asyncio.wait_for` times out at `decide_timeout_seconds=0.1`. Per spec § 144-149: `default_action` is submitted to the engine; event is written with `timeout: true`; strike counter incremented.
- After 3 timeouts, `_maybe_swap_to_autopass` substitutes `AutoPassAdapter`. Subsequent seat-0 events carry `auto_pass: true` (and no longer `timeout: true` — AutoPassAdapter returns synchronously).
- Hand reaches HAND_END without deadlock.
- Assertions: at least one timeout marker AND at least one auto_pass marker on seat-0 actions; no markers after the autopass swap carry both; seats 1-3 unaffected; HAND_END reached.
- `hold_seconds=30.0` (well above hand duration so the hold timer is moot — the spec uses prompt-deadline-based escalation, not hold-timer).

### F4 — spectator subscription, public projection

**[tests/web/test_e2e_s2.py](../tests/web/test_e2e_s2.py)** `test_s2_e2e_spectator_sees_public_events_only`:

- Two clients run concurrently. Spectator connects first, sends SPECTATE, captures all received frames, signals a `spectator_ready` event after receiving SPECTATING. Player waits on `spectator_ready` before sending ATTACH — ensures spectator is subscribed when the hand kicks.
- Assertions:
  1. Spectator's first non-HELLO frame is SPECTATING.
  2. Spectator never receives PROMPT.
  3. Spectator receives exactly one HAND_END (top-level frame with non-empty `terminal`), and the `terminal` payload matches the record's HAND_END event stripped of wrapper fields.
  4. Spectator's EVENT count equals the record's non-meta event count, and each EVENT payload is byte-equal to `project_event(record_event_stripped_of_seq, seat=None)`. The seq strip is because `RecordWriter` stamps `seq` on events when persisting; the in-memory event passed to `event_callback` doesn't carry it. The wire EVENT frame has its own outer `seq` and the inner payload is the raw projection.

## Known limitations carried forward

- **Single hand per orchestrator instance.** `WebOrchestrator` runs ONE hand per instance. Multi-hand orchestration (between-hand state, hand_index increment, dealer rotation, score carry-over) is Layer 8.
- **No authentication.** ATTACH carries no user_id at the wire layer; orchestrator derives one from `Connection.connection_id` (or a test-injected factory). Production auth (per `wire-protocol.md` AUTH_REQUEST/AUTH_RESPONSE) is Layer 8.
- **`mahjong/web/demo.py` still hand-rolled.** It's the toy reference handler for visual verification; `mahjong/web/server.py` is now the real one. Could be retired in Layer 8.
- **Phase-transition prompt clearing** — not surfaced in any S2 fixture because seat-0 either plays through every prompt or is dropped. Likely surfaces when CLAIM_WINDOW races introduce stale prompts on the wire after the engine has moved on. Defer until a UI report.
- **Opponent meld formation can't reconstruct which specific concealed tiles left** (carried from 7.5c.ii).
- **Concealed tile sorting after DRAW** (carried from 7.5c.ii).
- **Bilingual EN/ZH rendering** (decision 28, carried).

## What remains

**Layer 7 is closed.** Tick CHECKLIST.md Step 7.6 if it hasn't been already.

**Next layer: Layer 8 (ops hardening + multi-hand / multi-table).** Per [docs/server-plan.md](server-plan.md). Major themes:

- **Multi-hand orchestration.** Between-hand state, dealer rotation, score persistence, hand_index increment. The current `WebOrchestrator`'s single-hand assumption is the load-bearing thing to refactor — probably split into a `Table` (long-lived) and `Hand` (per-run_hand) abstraction.
- **Multi-table.** One `WebSocketServer` hosts N tables. Wire-protocol's `ATTACH {table_id}` and `LIST_TABLES` / `CREATE_TABLE` come into play.
- **Auth.** AUTH_REQUEST/AUTH_RESPONSE + session token + argon2 (per existing memory). User identity becomes server-tracked rather than connection-id-derived.
- **Linux deployment.** Sandbox tests currently skip on Darwin. Hosting target is RPi 5 / mini PC + Tailscale (see hosting-target memory). Need a deploy story + systemd unit (or equivalent) + health monitoring + log rotation.
- **Score persistence.** Per-hand scores → match scores → ELO/skill update math for bot zoo evaluation. SQLite (per design-doc style memory).
- **Cleanup before tagging:** retire `mahjong/web/demo.py` if `WebOrchestrator` covers all visual-verification use cases; consolidate memory (12+ sessions over 2-3 days now, `/extract-learnings` recommended).

## Outstanding questions / decisions for the user

- **Layer 8 entry decision.** Server plan lists multi-table, auth, deploy as Layer 8 themes. Which to attack first? Lean: multi-hand orchestration since it's the immediate restructure of what we just built (and unblocks the demo from being "one hand and you're done").
- **`mahjong/web/demo.py` fate.** Retire or keep as a low-dep visual probe? It's been useful but `WebOrchestrator` now subsumes it.
- **Chat wire-protocol amendment** — still required for `<chat-pane>` to do anything real. Not blocking Layer 8 entry but blocks the chat-pane stub from becoming functional.
- **Phase-transition prompt clearing** — not surfaced in S2; will surface in real human play. Plan: rely on new PROMPT's `prompt_id` differing from the stale one + client-side stale-prompt-id detection. Defer until reported.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) (will be refreshed with 7.6.iii/iv on next consolidation).
- [ ] Verify `git log --oneline -5` shows the 7.6.iii/iv commit (`2337042`) at HEAD.
- [ ] Run `.venv/bin/python -m pytest` and confirm 531 passing, 2 Linux-only skipped.
- [ ] Run `/extract-learnings` to consolidate memory (recommended — 12+ sessions over 2-3 days).
- [ ] If continuing into Layer 8: read [docs/server-plan.md](server-plan.md), pick a Layer 8 theme, build a CHECKLIST stub or update `docs/CHECKLIST.md`.
- [ ] If pausing: tag the commit (e.g., `git tag s2-exit`) so it's easy to find again.
