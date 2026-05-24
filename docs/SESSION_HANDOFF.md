# Session handoff — 2026-05-24 (end of 7.6.ii)

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 is 10 of 11 effective sub-steps complete.** This session landed **7.6.ii (orchestrator + Fixture F1)**: a real `TableSessions` + `manager.run_hand` is now behind the WS, and a byte-identical record fixture pins the full stack. F2/F3 (drop/reconnect) and F4 (spectator) remain.

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
| ~~7.5c.iv~~ | ~~Bilingual EN/ZH rendering~~ | **DEFERRED to a later edition** | User decided 2026-05-24 to skip; not blocking S2. |
| 7.6.i | HAND_END double-emit fix | `9ed9370` | Routed HAND_END as top-level frame; +6 tests |
| **7.6.ii** | **Orchestrator + Fixture F1 (byte-identical record)** | **THIS SESSION (`de15be7`)** | **`mahjong/web/server.py`; full stack + 2 new tests; 528 passing** |
| 7.6.iii | Fixtures F2 + F3 (drop/reconnect inside-hold + past-hold autopass) | pending | |
| 7.6.iv | Fixture F4 (spectator subscription, public-only projection) | pending | Closes Layer 7 and the S2 milestone |

**Verification at end of session:** ruff clean · ruff-format clean · mypy clean (58 source files; +1 over 7.6.i) · **528 tests pass repo-wide** (526 prior + 2 new F1 e2e tests; 2 Linux-only skipped). No regressions in S0 walking-skeleton byte-identical, 7.5c.iii prompt-bar, or 7.6.i HAND_END routing.

## Decisions reached this session

- **(32) Spectator fanout shape: option (a).** When 7.6.iv lands, the orchestrator will pass an `event_callback: Callable[[dict], Awaitable[None]] | None` to `mgr.run_hand`. The callback fires once per record event after the manager's adapter fanout and is wired to a new `TableSessions.fanout_event_to_spectators(event)` method (spectator-only — does NOT re-hit `SeatSession.observe`, which `HumanAdapter.observe` already triggers via the adapter path). Smallest manager surgery, cleanest separation. Not implemented yet — F1 has no spectator and doesn't need it; defer to 7.6.iv.
- **(33) F1 uses a contrived "all-default" hand, not a hand-tailored short script.** Seat 0's client echoes `default_action` on every PROMPT; seats 1-3 are `CannedAdapter(actions=[])` which already returns `default_action`. Result: identical engine path to the S0 walking-skeleton fixture (`seed=12345`), so the F1 record is byte-identical to the S0 record except at HEADER's `seats[0].identity` (`human` vs `canned`) and the FOOTER checksum. 221 events total, 31941 bytes. This is the simplest fixture that exercises the full stack; the byte-identical assertion is robust because it shares the engine path with an already-pinned fixture.
- **(34) F1 uses a raw `websockets` client, not Playwright.** The handoff spec wording suggested Playwright; we picked raw-WS because the browser UI's wire handling is already pinned by `tests/web/test_prompt.py` (7.5c.iii), and F1's contract is the orchestrator + record determinism — routing it through Chromium adds launch cost without strengthening the assertion. Playwright-driven e2e can be added later as a SEPARATE test if we want to gate UI wire handling against the full stack rather than a `FakeWireServer`.
- **(35) Clock seam stays as monkeypatch.** `mgr._now_ts` is monkeypatched in test (same pattern as `tests/table/test_s0_walking_skeleton.py`). `run_hand`'s signature stays unchanged. The two F1 tests both apply the patch; the wire-level PROMPT `deadline_ms` field uses `time.time()` (real clock), but PROMPTs don't appear in the record so they don't affect byte-identity.
- **(36) Single-hand orchestrator for v1.** `WebOrchestrator` runs ONE hand per instance — the hand kicks off on first successful ATTACH and the orchestrator stays up afterward. Multi-hand orchestration (between-hand state, hand_index increment, new dealer rotation, score carry-over) lands in Layer 8. This matches the project's walking-skeleton-before-depth scope rule.
- **(37) `OutboundSink.send` narrowed from `Mapping[str, Any]` to `dict[str, Any]`.** Reason: `mahjong.wire.server.Connection.send` already takes `dict[str, Any]`; the Protocol's wider `Mapping` declaration prevented it from satisfying the Protocol structurally (function-input parameter contravariance). Every actual caller (`SeatSession`, `Spectator`, `WebOrchestrator`) constructs a fresh dict per send, so the narrower input type costs nothing and lets `Connection` flow through `TableSessions.attach(conn, ...)` without casting. Worth knowing in case a future sink type wants to pass `MappingProxy`/`UserDict`.

## What this session built

### 7.6.ii — `WebOrchestrator` + F1

**[mahjong/web/server.py](../mahjong/web/server.py)** (new, ~240 lines):

- `WebOrchestrator(host, port, ruleset, seed, hand_id, record_path, server_info, canned_seat_actions, identity_factory, static_dir, table_id, decide_timeout_seconds)`.
- Pre-builds `initial_state(ruleset, seed)` at construction so `TableSessions.snapshot_provider` returns a correct snapshot at ATTACH time (before `run_hand` runs). Same seed → identical state inside `run_hand`, so no drift.
- Constructs three `CannedAdapter`s for seats 1-3 (empty actions ⇒ defaults). The orchestrator owns them.
- Lifecycle: `start()` boots the `WebSocketServer`, `close()` cancels the in-flight `run_hand` task (if any) and tears the server down, `port` exposes the bound port, `wait_hand_complete(timeout=...)` blocks until `run_hand` returns.
- Handler flow:
  1. Send HELLO.
  2. Await first inbound (5s timeout).
  3. ATTACH → `TableSessions.attach(conn, user_id=identity.user_id, seat=msg.seat)`. Only seat 0 accepted (others get `seat_not_yours`). On success, under a lock, kick `_run_hand` as a background task if not already running.
  4. SPECTATE → `TableSessions.spectate(...)`. (Wired but no F4 fixture yet — covered in 7.6.iv.)
  5. Inbound loop forwards every subsequent message to `TableSessions.handle_inbound(conn, msg)`.
  6. On socket close: `TableSessions.on_socket_dropped(conn)`.
- `_run_hand` constructs the 4-adapter list (`HumanAdapter` for seat 0, the three pre-built CannedAdapters for 1-3) and calls `mgr.run_hand(...)` with the orchestrator's seed/hand_id/record_path/server_info. Sets `_hand_done` event in `finally`.
- `IdentityFactory` is `Callable[[Connection], HumanIdentity]`. Default derives from `conn.connection_id`; tests inject a fixed identity for byte-identical assertions.

**[tests/web/test_e2e_s2.py](../tests/web/test_e2e_s2.py)** (new, 2 tests):

- `test_s2_e2e_record_is_byte_identical_to_fixture` — the S2 exit gate. Boots `WebOrchestrator(seed=12345, hand_id=01970e8a-..., server_info=s0-fixture)`, raw-WS client connects, ATTACHes seat 0 (identity = `{kind: human, user_id: u_test, display: Tester}`), echoes `default_action` on every PROMPT, breaks on HAND_END. After hand complete, asserts the temp record bytes == `tests/_fixtures/s2_e2e_record.jsonl` byte-for-byte.
- `test_s2_e2e_no_double_emit_hand_end` — wire-level invariant. Same setup but client drains for 500 ms after the first HAND_END and the test asserts exactly one HAND_END frame was received with a non-empty `terminal` payload. Pins the 7.6.i invariant at the wire level (the byte-identical record check only constrains record events).
- Both use `mgr._now_ts = lambda: f"2026-05-20T00:00:00.{i:03d}Z"` monkeypatch (same pattern as `tests/table/test_s0_walking_skeleton.py`).

**[tests/_fixtures/s2_e2e_record.jsonl](../tests/_fixtures/s2_e2e_record.jsonl)** (new, 31941 bytes, 221 lines):

- Diff vs `s0_walking_skeleton_seed_12345.jsonl`: 2 lines differ. Line 0 (HEADER): `seats[0].identity` is `{kind: human, user_id: u_test, display: Tester}` instead of `{kind: canned, script: pass}`. Line 220 (FOOTER): different `checksum` (the SHA-256 over the record bytes, which differ at HEADER).
- Replays correctly to the same `state_hash_final` as the FOOTER asserts (defense in depth: the engine path is byte-equivalent to S0).

**[mahjong/sessions/mux.py](../mahjong/sessions/mux.py)** — one-line type fix:

- `OutboundSink.send` parameter changed from `Mapping[str, Any]` to `dict[str, Any]`. See decision 37 above.

## Known limitations carried forward

- **Spectator fanout not yet wired.** Decision 32 above documents the design; implementation lands in 7.6.iv when F4 needs it. `WebOrchestrator._handle_spectate` accepts SPECTATE frames and registers the spectator with `TableSessions`, but record events from `run_hand` don't fan out to spectators yet — currently only seats receive events (via the manager's adapter-observe path). A spectator-only test would observe nothing.
- **Single hand per orchestrator instance** (see decision 36). Restarting between hands means a fresh orchestrator + fresh `TableSessions`. Acceptable for v1 (S2 is single-hand).
- **No authentication.** ATTACH carries no user_id at the wire layer; orchestrator derives one from `Connection.connection_id` (or a test-injected factory). Production auth (per `wire-protocol.md` AUTH_REQUEST/AUTH_RESPONSE) is Layer 8.
- **`mahjong/web/demo.py` still hand-rolled.** It's the toy reference handler for visual verification; `mahjong/web/server.py` is now the real one. Demo doesn't yet use `WebOrchestrator`; we could replace it but it's been useful as a minimum-dependency probe. Leave for now.
- **Phase-transition prompt clearing** (carried). Still TBD. The browser may need to clear a stale PROMPT bar when the server moves on without re-prompting seat 0 (e.g. CLAIM_WINDOW where seat 0 wasn't the discarder). Has not surfaced in F1 because seat 0 plays through every prompt. Will likely surface in F2 (mid-hand drop) or in a Playwright e2e variant.
- **Opponent meld formation can't reconstruct which specific concealed tiles left.** Unchanged from 7.5c.ii.
- **Concealed tile sorting after DRAW.** Unchanged from 7.5c.ii.
- **Bilingual EN/ZH rendering** is explicitly deferred (decision 28 from 7.6.i session).

## What remains for next session

### 7.6.iii — Fixtures F2 + F3 (drop/reconnect)

**F2 — drop & reconnect within hold window.**

- Spec: extend `tests/web/test_e2e_s2.py` with `test_s2_e2e_drop_and_reconnect_within_hold`.
- Setup: same `WebOrchestrator` as F1, but the client is split into two phases.
  - Phase A: connect, ATTACH seat 0, echo defaults for the first N PROMPTs, then disconnect abruptly (`await ws.close()` mid-handler — not `STOP_SPECTATING`/`DETACH`).
  - Phase B: reconnect with a new WS, send `ATTACH {table_id: 1, seat: 0}` again with the SAME user_id. Per spec § Conflict resolution (HELD + same user → resume), `TableSessions.attach` should replay the buffer and re-emit the pending PROMPT (if any). Continue echoing defaults to hand end.
- Assertion: record file is still byte-identical to the F1 fixture (same engine path, no auto_pass markers because the seat was held, not replaced). This is the load-bearing assertion — drop/reconnect is invisible to the record.
- Required: deterministic `user_id` across both phases. The default `_default_identity_factory` derives from `connection_id` which increments per connection — TEST must inject a fixed identity_factory (same pattern as F1).
- Watch: the orchestrator's `_handle_attach` currently kicks `run_hand` only on the FIRST attach (`if self._hand_task is None`). The reconnect path will go through `_handle_attach` again and skip the task spawn — correct, but verify.
- Watch: `SeatSession._resume` replays the buffer as wire EVENTs/HAND_ENDs. Phase B's client will see catch-up frames before the pending PROMPT. Echo-default logic still works because catch-up doesn't include PROMPTs (they're re-emitted separately via `_reprompt_if_pending`).

**F3 — drop, hold expires, AutoPassAdapter substitution.**

- Spec: `test_s2_e2e_drop_past_hold_replaces_with_autopass`.
- Setup: construct orchestrator with `MAHJONG_SEAT_HOLD_SECONDS` equivalent low (today, the constant lives in `TableSessions.__init__` defaulting to `DEFAULT_HOLD_SECONDS=60.0`; pass `hold_seconds=1.0` or thread through). After ATTACH, drop the client, wait 2s, do not reconnect.
- Assertion: record contains seat-0 events marked `replaced_by_auto_pass` (or `auto_pass`-flagged) for any prompt that fired after the hold expired. The hand still completes.
- Required: thread `hold_seconds` through `WebOrchestrator` constructor → `TableSessions(hold_seconds=...)`.
- Required: `on_hold_expired` callback on `TableSessions` must trigger the manager to swap the seat to `AutoPassAdapter`. Today the manager's strike-based escalation (`_maybe_swap_to_autopass`) only fires on per-decision failures (timeout/illegal/crash). The hold-expired path resolves the pending future with `SeatHoldExpired` which `HumanAdapter.decide` re-raises as `SeatError`, which `_decide_or_default` treats as `crashed=True` and counts as a strike. So after `strike_limit=3` hold-expiries-on-this-hand, autopass kicks in. For F3 with `strike_limit=1` we'd see autopass after the very next prompt. Confirm this matches spec § Error model before writing the test; may need to add a direct hold-expired → autopass path that doesn't wait for strike accumulation.

### 7.6.iv — Fixture F4 (spectator)

**F4 — spectator subscription, public-only projection.**

- Spec: `test_s2_e2e_spectator_sees_public_events_only`.
- Setup: F1 setup + a SECOND client that sends SPECTATE before/during the hand.
- Assertion 1: spectator receives EVENTs throughout the hand, projected with `project_event(event, seat=None)` (no concealed-hand leaks).
- Assertion 2: spectator never receives PROMPT frames.
- Assertion 3: spectator receives HAND_END at the end.
- **Implementation prerequisite (decision 32):** add an `event_callback: Callable[[dict], Awaitable[None]] | None = None` parameter to `mgr.run_hand`, threaded through `_fanout_observe` (or called directly after each `writer.write_event`). Add `TableSessions.fanout_event_to_spectators(event)` (spectator-only — iterates `self._spectators` and calls `spec.send_event`). `WebOrchestrator._run_hand` passes `event_callback=self._sessions.fanout_event_to_spectators`.
- After F4 passes: Layer 7 is complete, S2 milestone is reached, [docs/CHECKLIST.md](CHECKLIST.md) Step 7.6 gets a tick.

### After 7.6 — cleanup before tagging S2

- Refresh [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) with 7.6.{i,ii,iii,iv} and the design decisions above.
- Consider whether `mahjong/web/demo.py` should be retired in favor of `mahjong/web/server.py` or kept as a low-dep visual probe.
- Run `/extract-learnings` (consolidation flagged at 11+ sessions over 2 days).

## Outstanding questions / decisions for the user

- **F2 reconnect identity persistence.** When auth lands in Layer 8, user_id will come from session token. For F2 today (no auth), the test injects a fixed identity_factory. Production behavior with a real returning user is out of scope for 7.6.iii. Acknowledged?
- **F3 hold-expired → autopass path.** As written above, it'd work via the strike counter (3 strikes after 3 hold expiries). Check whether spec wants a more direct path (one hold expiry → immediate autopass). If so, the orchestrator needs to wire a real `on_hold_expired` callback that escalates without going through the strike loop.
- **Phase-transition prompt clearing** — likely surfaces in F2 if the resumed PROMPT differs from what the client expected. Plan: add a wire frame or rely on the new PROMPT's `prompt_id` differing from the stale one. Decision deferred to when it bites.
- **Chat wire-protocol amendment** — still required for `<chat-pane>` to do anything real. Not blocking S2 or 7.6.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) (will be refreshed with 7.6.ii on next consolidation).
- [ ] Verify `git log --oneline -5` shows the 7.6.ii commit at HEAD.
- [ ] Run `.venv/bin/python -m pytest` and confirm 528 passing, 2 Linux-only skipped.
- [ ] Decide between starting F2 (drop/reconnect inside hold) or F4 (spectator) first. They're independent; F4 requires the `event_callback` plumbing (decision 32) but unblocks closing the S2 milestone.
- [ ] If F2 first: scaffold `test_s2_e2e_drop_and_reconnect_within_hold`, watch it fail, then verify the resume path works without orchestrator changes (it should — `TableSessions.attach` already handles HELD + same user).
- [ ] If F4 first: add `event_callback` to `mgr.run_hand`, add `TableSessions.fanout_event_to_spectators`, wire from `WebOrchestrator._run_hand`, then write F4.
