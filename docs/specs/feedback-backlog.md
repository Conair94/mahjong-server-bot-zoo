# Feedback backlog — player-reported bugs & feature requests

The single internal source of truth for **which player-submitted reports have been
triaged, specced, implemented, or declined**. Reports arrive via the in-game feedback
modal (Spec 23, [feedback-reporting.md](feedback-reporting.md)) and land as plain-text
files in `data_dir/reports/*.txt`. This doc is the *triage layer* over those files; the
live, server-side equivalent is the feedback-tracking feature
([feedback-tracking.md](feedback-tracking.md), Spec 30) which reads/writes the same
status values from the admin console.

**Status vocabulary** (shared with Spec 30's status store):

| Status | Meaning |
| --- | --- |
| `open` | Received, not yet triaged. |
| `triaged` | Understood + categorised + prioritised; not started. |
| `in-progress` | Spec written and/or implementation underway (branch open). |
| `implemented` | Landed on `main` with a verification artifact. |
| `verified` | Implemented **and** browser-/play-verified on the deployed build. |
| `wontfix` | Declined (with a reason) or superseded. |
| `duplicate` | Same as another report; points at the canonical ID. |

> **Source of truth for status:** this table. Spec 30's `status.json` sidecar mirrors it
> for the live console; when they disagree, this doc wins and the sidecar is corrected.
> Update the relevant row in the **same PR** that changes a report's state.

---

## Current backlog

Each item has a stable `FB-NN` id. "Report(s)" are the on-disk filenames under
`data_dir/reports/`.

| ID | Type | Title | Priority | Status | Target spec |
| --- | --- | --- | --- | --- | --- |
| FB-01 | bug | Concealed-gang hang / concealed tiles not displayed | **P0** (game-breaking) | implemented | (this doc — robustness guard) |
| FB-02 | bug+feat | End-of-game summary too brief — needs ready-up / acknowledge gate | **P0** (user "urgent") | implemented | (this doc) |
| FB-03 | feat | Reconnect / rejoin an in-progress game | P1 | implemented | [reconnect-rejoin.md](reconnect-rejoin.md) (Spec 31) |
| FB-04 | feat | Per-account game records (replay + stats) | P1 | implemented | [account-records-replay.md](account-records-replay.md) (Spec 32) |
| FB-05 | feat | Table management / multi-human join UX | P2 | implemented | [table-management.md](table-management.md) (Spec 33) |
| FB-06 | feat | Audio cues + clearer claim-opportunity notifications | P2 | implemented | (this doc) |
| FB-07 | meta | Feedback-tracking system (this backlog + admin console) | P0 (enabler) | implemented | [feedback-tracking.md](feedback-tracking.md) |
| FB-08 | bug | Profile page unreachable / re-login on refresh | — | implemented | [live-play-bugfixes.md](live-play-bugfixes.md) (Spec 29 Bug A/E) |
| FB-09 | bug | Scoring awards concealed fans (Fully Concealed / Concealed Pungs / Concealed Kong) to hands with claimed melds; Self-Drawn fan missing | **P0** (wrong winners/payments) | implemented | (this doc — meld→pack offer fix) |
| FB-10 | bug | Fan→point conversion "wrong" (−20/loser) — live tables ran `mcr-2006` (8-fan floor), not the house ruleset | P1 | implemented | (this doc — default ruleset → `mcr-house-3fan`) |
| FB-11 | feat | Mobile-friendly client + a way to quit a game | P2 | triaged | — (quit-game half delivered by FB-14; mobile layout remains) |
| FB-12 | feat | Minimalist UI mode (discards + melds + own tiles only) | P2 | triaged | — |
| FB-13 | bug | Mid-hand table freeze — hand task dead-stops at a pending prompt, no timeout, nothing logged | **P0** (game-breaking) | in-progress | (this doc — stall watchdog) |
| FB-14 | bug | No way back to the main menu from a game — fatal when combined with a hung table (FB-13) + auto-rejoin (FB-03) | **P0** (game-breaking with FB-13) | implemented | (this doc — leave-table escape hatch) |
| FB-15 | bug | Missed a mahjong (HU) on a discard of 9B / 7B — HU not offered | P1 | implemented | (this doc — was the `mcr-2006` 8-fan floor; see FB-10) |
| FB-16 | bug | Typing in a text field (bug-report box / chat) fires game shortcuts — Space passes, H toggles HU, Enter discards | P1 | implemented | (this doc — keydown editable-target guard) |
| FB-17 | bug | Reconnect/refresh serves the deal-time snapshot — board desync, phantom un-discardable tiles, "new hand = same hand" | **P0** (game-breaking) | implemented | (this doc — live-state snapshot provider) |
| FB-18 | bug | Drawn-tile discard targeting: Enter falls back to sorted-last tile; digit keys fight the reordered display | P1 | implemented | (this doc) |
| FB-19 | bug | Next hand can start without every player's READY; no ready gate at match end | P2 | implemented | (this doc — HELD gating + gate logging; match-end gate → DEF-24) |

Priority key: **P0** ship next · **P1** important, larger · **P2** polish.

---

## Deferred ledger (DEF-NN)

Work consciously parked — punted features, browser-verify-owed UI, and
**instrument-and-defer** root causes. Per the project working agreement
([CLAUDE.md § Deferring work](../../CLAUDE.md)), this table is what makes a deferral
*discoverable*; the linked spec can hold the detail. Every row names **what / why /
revive-trigger**. An instrument-and-defer row also pins the **exact log string to grep**
— when that string appears in a real run, the parked investigation resumes with the
stack trace it was waiting for.

| ID | What's parked | Why deferred | Revive trigger | Grep / ref |
| --- | --- | --- | --- | --- |
| DEF-01 | FB-01 concealed-gang hang **root trigger** (the precise exception). Fix converted the silent hang into a logged teardown in *both* hand loops; the original cause is offline-clean and unreproducible. | Replay logic steps cleanly; no deterministic repro. Instrument-and-defer is the honest fix. | The log string appears in a real run → read the traceback, fix the trigger directly. | `hand_loop_crashed` ([web/server.py:360](../../mahjong/web/server.py#L360), [server/registry.py:815](../../mahjong/server/registry.py#L815)) |
| DEF-02 | FB-04 leftovers: paginated "my games" view (`GET_HISTORY` wired+tested, no "load more" UI), match-replay, public-replays config. | Profile recent-20 covers the common case. | A player asks for older games / match replay. | [account-records-replay.md](account-records-replay.md) |
| DEF-03 | FB-05 leftovers: lobby server-push-on-change, start-authority reason chip, bot↔human mid-lobby seat conversion. | 2 s lobby poll keeps the list fresh; multi-human is rare today. | Lag complaint, or a 2nd+ human table becomes common. | [table-management.md](table-management.md) |
| DEF-04 | **Browser-verify owed** on the deployed build: FB-06 audio (**re-fixed 2026-06-11** — declaration cues + AudioContext unlock; was silent before, so this verify is now load-bearing: confirm the alert on your own claim window and the chi/peng/gang/hu declaration tones actually sound), FB-07 console, FB-08 (Spec 29 token/profile), Spec 22 §22.x UI, Spec 25 admin tunnel/feedback/training panes, cardinal pinwheel, **PR #16 chi-picker + opponent-concealed-kong render** (2026-06-10 session ran pre-PR-16 code — reports `20260610_002312`, `20260611_004346` are stale-build, not regressions), **Spec 34 minimal play view + in-game player names** (default view flipped to minimal; large-print legibility, claim banner, `Alt+M` toggle — Playwright-green, visual pass owed), **Spec 37 hand-stats strip + `Alt+S` detail pane** (Playwright-green incl. real-orchestrator e2e; visual pass owed — check the strip during your own turn and a claim window). | Unit/Playwright-green; real-device pass not yet run. | Next live deploy / play session — flip each to `verified`. | (this doc + Spec 22/25/34) |
| DEF-05 | Auth: real auth-targeted rate limiter (e.g. 10 failures/IP/hr); RESUME token rotation. | Friends-and-family + connection-wide cap suffices pre-S7. | S7 ops hardening, or a public-abuse signal. | [auth.md:23](auth.md), [auth.md:294](auth.md) |
| DEF-06 | Late-join **replay-from-record** (catch a mid-hand joiner up). | Refusal gate (Spec 20) is enough; needs per-table replay-lock design. | Someone actually requests mid-hand late-join. | [late-join-replay.md:133](late-join-replay.md) |
| DEF-07 | Decide-timeout heartbeat extension (`PROMPT_HEARTBEAT`: keep an engaged human's clock alive). | Needs wire + client + timer-reset work; fixed timeout OK for now. | Players report being timed out while actively deciding. | [human-decide-timeout.md:94](human-decide-timeout.md) |
| DEF-08 | Scoring-config false-mahjong penalty. | Declared in config schema but unreachable in-engine today. | The engine can produce an illegal-declared-win state. | [scoring-config.md](scoring-config.md) |
| DEF-09 | Admin-console: control-plane login + network bind; `systemd` supervisor switch. | v1 ships the script + token-gated status. | Production Linux deploy. | [admin-console.md:35](admin-console.md), [admin-console.md:383](admin-console.md) |
| DEF-10 | Feedback-tracking: status filter + auto-archive of `implemented`/`wontfix` rows. | Nice-to-have; backlog is short. | Pane gets noisy enough to need it. | [feedback-tracking.md:137](feedback-tracking.md) |
| DEF-12 | **FB-13 root cause** (the exact await where the live hand task wedged). The watchdog converts any future stall into a logged, position-stamped abort with the pending coroutine chain; the underlying trigger is still unidentified (see FB-13 section: every decide/observe await is provably bounded, yet two tables dead-stopped). Also determine the live `MAHJONG_DECIDE_TIMEOUT_*` env (observed behavior implies ≫ defaults). | No deterministic repro; all bounded-await candidates ruled out offline. Instrument-and-defer per the FB-01 template. | `hand_step_stalled [DEF-12]` or an unexpected `hand_loop_cancelled` appears in a run → the `stuck_at=` chain names the wedged await; fix it directly. | `hand_step_stalled` ([table/manager.py](../../mahjong/table/manager.py) `_guarded_step`), `hand_loop_cancelled` (both hand loops) |
| DEF-15 | **Minimal-view combined pond ordering on mid-hand reconnect**: `view.discard_pond` is exact when a hand is watched from the start, but a reconnect snapshot has no global discard order, so the seed approximates by round-robin-from-dealer interleave (self-heals as play continues). | The from-start path (the normal case) is exact; reconnect is rare and the pond self-corrects. Exact ordering needs per-discard sequence in the snapshot. | A player notices a mis-ordered pond right after reconnecting, or per-discard timestamps get added to the projection. | `_seedPond` in [web/static/apply_event.js](../../mahjong/web/static/apply_event.js); [minimal-play-view.md](minimal-play-view.md) |
| DEF-17 | **Rules fidelity: kong is legal on an empty wall** (surfaced closing DEF-16). Real MCR forbids declaring a kong when no replacement tile remains; this engine allows it and ends the hand as an exhaustive draw (now with a proper `HAND_END` — the DEF-16 fix). | Tightening `legal_actions` would shift every seeded rollout/golden hash for a rare edge case; the current behaviour is a coherent house ruling. | Botzone S1 integration (the live judge will enforce its own legality) or a rules-fidelity audit. | `GANG` legality in [engine/legality/discard.py](../../mahjong/engine/legality/discard.py) / [claim.py](../../mahjong/engine/legality/claim.py) — neither consults `wall.remaining` |
| DEF-18 | **Rules fidelity: PyMahjongGB awards "Chicken Hand 8" to a zero-fan discard win** (surfaced authoring Spec 37 fixtures): a hand with no regular fans scores 8 through `pymj.calculate_fan`, which clears both the `mcr-2006` 8-fan cliff and the house 3-fan floor — so a fanless hand is currently a *legal, 8-fan-paying win* on this server. Real MCR has no chicken hand (a 0-fan hand simply cannot win). Self-draw is unaffected (Self-Drawn ≥ 1 fan suppresses it). | Whether to strip the calculator's Chicken Hand entry before the cliff check is a scoring-contract change: it alters legality and payouts and needs its own reward-shape test pass + golden review. | A live winning hand whose only fan is `Chicken Hand`, or the next scoring-config/rules-fidelity audit. | `Chicken Hand` in any `HAND_END.fan[]`; pinned in `tests/analysis/test_hand_stats.py::test_subfloor_wait_fan_shown_raw_not_hidden` |
| DEF-19 | **Draggable / movable table panels** — the player's stated ideal for chat/stats/score panes (click-and-drag, free positioning). Spec 40 ships the fallback instead: chat in a narrower side column, stats + score widget stacked under the game pane. | A free-form windowing system (drag state, z-order, collision, persisted positions) is a large surface that fights the fixed ASCII grid, for a cosmetic gain over the fixed layout. | A player asks again for movable panels, or the fixed layout proves too constraining once the score widget + stats both see use. | [in-game-scoreboard.md § Non-goals](in-game-scoreboard.md); panel grid in [web/static/app.js](../../mahjong/web/static/app.js) `TablePage` |
| DEF-20 | **Persist `serve` logs to a file** (rotating file handler or `tee` in the admin-console supervisor). Logs go to stdout only; when the terminal/process is gone, so is the evidence. The 2026-06-12 FB-19 instance (hand loop stalled or crashed after `...-t1-h0`; no hand-1 row ever reserved) is unattributable for exactly this reason — `hand_loop_crashed`/`hand_step_stalled` may have fired and we'll never know. | Small ops change, but touches logconfig + admin-console supervision; macOS dev box isn't the deploy target. | Next unexplained mid-session stall (any instrument-and-defer grep string firing with no captured output), or the Linux production deploy (DEF-09). | [server/logconfig.py](../../mahjong/server/logconfig.py); admin console supervisor in [admin-console.md](admin-console.md) |
| DEF-21 | **Vestigial EVENT entries in the session-mux resume buffer.** The FB-17 resume policy (fresh current snapshot, no EVENT replay) leaves `SeatSession._buffer`'s EVENT entries written-but-never-read (only a buffered `HAND_END` is consumed on resume). Either strip EVENT buffering or build a true delta-resume (at-drop snapshot + replay) if event-order affordances are ever wanted on reconnect. | Removal touches the observe path + overflow bookkeeping + several fixtures for zero behavior change; delta-resume is speculative until someone misses the affordances. | Next structural change to `sessions/mux.py`, or a player-visible want for replay-fidelity on reconnect. | [session-mux.md § Ring buffer](session-mux.md); `_append_buffer` in [sessions/mux.py](../../mahjong/sessions/mux.py) |
| DEF-24 | **FB-19 soft spot 1: the match-end summary skips the ready gate.** A finite-match table (`max_hands` set) breaks out of the hand loop the instant the last hand ends — *before* `_await_humans_ready` — so the final HAND_END summary is never acknowledged before teardown. (Soft spots 2 + 3 — HELD gating + gate logging — landed 2026-06-14.) | No live impact: `serve` runs `max_hands=None`, so the `max_hands` break never fires in production. Adding the gate here changes finite-match-with-LIVE-human test behavior (e.g. `test_resume_snapshot` runs `max_hands=1` with an attached human → would newly block on a never-sent READY) and needs a distinct match-end summary state, not just the between-hand gate. | A finite-match / tournament mode ships (`max_hands` set on a live table), or a player reports the end-of-match summary flashing. | the `next_hand_index >= self._max_hands` break in [server/registry.py](../../mahjong/server/registry.py) `_run_hand_loop`; fixture (c) in the FB-19 section of this doc. |
| DEF-22 | **Web-client E2E (Playwright) tests don't run in CI** — `tests/web/` requires `playwright` + browser binaries, which aren't in `[dev]` deps or any CI step, so CI now `importorskip`s the whole tree (1 skip). The suite has *never* run in CI; it was masked behind the long-red mypy step until that went green (2026-06-12). Browser-verify of UI remains a manual, owed activity (many `project_layer8_browser_verified`-class items). | Wiring it in needs a CI `playwright install chromium` step (~150 MB download) across the 4-cell matrix, and macOS-runner browser E2E is notably flaky — real cost for tests that duplicate manual browser-verify. | A web-UI regression slips through that an E2E test would have caught, or CI gains a dedicated (non-matrix) E2E job. | `pytest.importorskip("playwright")` in [tests/web/conftest.py](../../tests/web/conftest.py); CI matrix in [.github/workflows/ci.yml](../../.github/workflows/ci.yml) |

When you close a DEF row, delete it (or mark it `verified`/done) in the **same PR** that
does the work — same rule as the FB table above.

---

## FB-01 — Concealed-gang hang / concealed tiles not displayed

- **Report(s):** `20260606_000643_bug.txt` (ConnorL, 2026-06-06).
  > "After a concealed gang the game hangs, I can confirm that it does in fact not
  > display the tiles that are supposed to be concealed."
- **Priority:** P0 — game-breaking (freezes the table).
- **Status:** triaged.

### Leading hypothesis (needs reproduction before any fix)

The game ran on a **post-Spec-29 build** (reports timestamped ~3 h after PR #13 merged;
FB-02 confirms the cache-bust reached the browser). Spec 29 **Bug D** introduced
opponent-side masking of a concealed kong: `project()` emits the opponent's meld as
`{"type": "GANG_CONCEALED", "hidden": true}` with **no `tiles` key**
([state.py](../../mahjong/engine/state.py) `_mask_concealed_kong_for_opponent`). The
owner's own view is unmasked and correct, so the symptom is **opponent-side**: when a
*bot* declares a concealed gang, the client receives a meld with no `tiles` and the
renderer/reducer (`apply_event.js` / `render.js`) likely dereferences `meld.tiles` →
throws → the render loop wedges = "hangs", and the four face-down tiles never draw =
"does not display the tiles that are supposed to be concealed."

This is the [test-the-wire→UI-seam] failure mode: Spec 29's `test_concealed_kong_privacy.py`
pins the **projection** (server) but may not exercise the **live frame dispatch** of an
opponent's `GANG_CONCEALED` through the real client reducer.

### Investigation outcome (2026-06-05) — the hypothesis was WRONG; real cause is a silent hand-task crash

Reproduced from the recorded game `~/.local/share/mahjong-server/records/t1/hand_0000.jsonl`
(timestamp `00:04:19Z`, immediately before the report): seat 2 (a bot) draws W1 → declares a
**concealed GANG of W1** (seq 77) → replacement draw → discards T3 (seq 79) → the record
**dead-stops** with no claim window, no further turn, and **no `HAND_END`**. Classic frozen table.

Ruled out, by direct probing, every part of the leading hypothesis and more:

- `project_event` replayed over all 80 events for every seat → **no throw**.
- `project` (full SeatView) on the post-kong state for every seat → **no throw** (the masked
  `hidden`-meld is opponent-only; the owner sees its own kong).
- `apply_event.js` `applySelfGang` handles the redacted/opponent kong (count-based removal,
  `hidden:true` meld); `render.js` `renderMelds` is null-safe (`m.tiles ?? []`, explicit
  `m.hidden` → four face-down). **Client doesn't crash.**
- Engine `legal_actions` + `apply_action` stepped 6+ turns forward from the post-kong state →
  **clean**. `diff_to_events` on the continuation (incl. a later claim window) → **clean**.
- `run_hand` is internally exception-proof: every decision goes through `_decide_or_default`
  (catches timeout **and** any adapter exception → default), and `_fanout_observe` bounds
  each observe with `wait_for` + swallows. It **cannot** hang or die uncaught.

**Real root cause:** `WebOrchestrator._run_hand_loop` ran the hand in a background task wrapped
in `try: … finally: _match_done.set()` with **no `except`**. Any unhandled exception in the
loop *surrounding* `run_hand` (`next_dealer`, `begin_next_hand`, adapter construction, or an
unforeseen `run_hand` edge) killed the task **silently** — clients got no `HAND_END`, no error,
just a frozen frame, and the record truncated mid-hand. The precise original trigger isn't
deterministically reproducible from the replay (the game logic is sound), but this gap is the
mechanism by which *any* such failure becomes an indefinite "hang."

### Fix (implemented)

Guard the loop ([mahjong/web/server.py](../../mahjong/web/server.py) `_run_hand_loop`):
re-raise `CancelledError` (normal shutdown), but on any other exception **log with full
context** (`hand_id`/`seed`/`hand_index` + traceback — so the next occurrence is
post-mortem-able, per CLAUDE.md) and **`sessions.shutdown(reason="hand_aborted")`** so seated
clients receive a graceful `DETACH` instead of freezing.

**Verification:** [tests/web/test_hand_loop_crash_guard.py](../../tests/web/test_hand_loop_crash_guard.py)
— a `run_hand` patched to raise: the loop tears down (`shutdown("hand_aborted")`) and completes
rather than propagating. Reproduce-first confirmed: **fails without the guard** (RuntimeError
escapes), passes with it.

**Honest caveat:** this converts the *silent hang* into a logged, visible failure and a clean
client teardown — it does **not** pin the exact original trigger (the offline logic is clean).
If FB-01 recurs, the new log line will carry the actual stack trace to fix the trigger directly.
The "concealed tiles not displayed" half is likely the *correct* new Bug-D face-down masking
(opponent kongs are private now) being read as a regression — not a defect.

---

## FB-02 — End-of-game summary too brief; needs ready-up gate

- **Report(s):** `20260606_002220_bug.txt`.
  > "The end of game display worked but lasted about a second, I was playing on fast mode.
  > The feature should wait for all human players to ready up for the next match and
  > acknowledge they read the summary."
- **Priority:** P0 — the user's standing "urgent" item; the Spec 29 cache-bust made the
  summary *appear*, but it auto-advances too fast (worse on fast bots).
- **Status:** triaged.

### Shape (implemented)

Spec 29 confirmed the `HAND_END` summary renders correctly. The gap was **flow control**:
the table auto-advanced after a fixed ~2s pause, flashing the summary. Now the live
between-hand advance (`TableHandle._run_hand_loop`, registry.py) holds the next hand at a
**ready-up gate** until every **LIVE human** seat sends a `READY` ack, with a
`ready_timeout_seconds` (default 120s) safety net so a disconnected / walked-away human
can't stall the table forever. Pure-bot tables aren't gated (no LIVE humans → the gate
returns immediately, so self-play / bot timing is unchanged).

Design decisions:

- **`READY` wire message** (client → server, game wire — `KNOWN_KINDS` + round-trip).
  `READY_STATE` (server → client) is reserved in the codec for future multi-human
  "waiting on N players" feedback; not broadcast yet (the live case is solo).
- **Only LIVE humans gate.** A human who drops during the gate falls out of the LIVE set,
  so the remaining humans (or none) advance — reuses the 8.7 `SeatState.LIVE` model.
- **Brief pause kept** before the gate (preserves drain/`stop_event` behavior); the gate
  is purely additive on top.
- **Client:** `<game-pane>` shows a `Ready ▶ Next hand` button at `HAND_END`; click sends
  `READY` (via a `ready-submitted` CustomEvent → app `_conn.send`) and swaps to
  "Waiting for the next hand…" (idempotent — no double-submit). `setSnapshot` re-arms the
  button when a terminal-free snapshot (new hand) arrives.

### Verification (all green)

- Gate unit tests [tests/server/test_ready_gate.py](../../tests/server/test_ready_gate.py):
  no-live-humans advances immediately; un-readied human times out (waited, didn't flash);
  a mid-gate READY advances well under the timeout; `_mark_ready` ignores non-human/unknown.
- Codec round-trip for `READY`/`READY_STATE` ([tests/wire/test_codec.py](../../tests/wire/test_codec.py)).
- Browser wire→UI [tests/web/test_hand_end_dispatch.py](../../tests/web/test_hand_end_dispatch.py)
  `test_ready_button_sends_ready_and_shows_waiting`: real `<mahjong-app>` over the fake wire —
  button appears at HAND_END, click emits exactly one `READY`, button → waiting indicator.
- Full fast suite 1076 passed.

---

## FB-03 — Reconnect / rejoin an in-progress game

- **Report(s):** `20260606_001109_bug.txt` (first clause).
  > "There is no way to rejoin a game you were previously connected to."
- **Priority:** P1 — high value.
- **Status:** in-progress — **specced** in [reconnect-rejoin.md](reconnect-rejoin.md) (Spec 31).

### Shape — FB-03 specced (the reframe)

Spec-time grounding flipped the difficulty estimate. The seat-hold state machine
(`UNBOUND/LIVE/HELD`, ring-buffer replay, takeover/reject) is **already built and tested**
in `mahjong/sessions/mux.py`, and Spec 29 Bug A already persists the token so refresh
re-authenticates. What's missing is the *player-facing loop*: surfacing your held seat
(new `HELLO.seat_holds`), auto/-manual re-`ATTACH` from the lobby, and raising the hold
window (60→180s) for deliberate return. So FB-03 is **client orchestration + a small
discovery API**, not a new server subsystem. Full design + 10 verification fixtures in
Spec 31.

**Implemented 2026-06-07.** Grounding moved the discovery field from `HELLO` (pre-auth,
account unknown) to `AUTH_RESPONSE { ok:true }`. Shipped: `SeatSession.hold_deadline_ms`,
`TableRegistry.seat_holds_for`, `AUTH_RESPONSE.seat_holds[]`, hold default 60→180,
client auto/-manual rejoin. Tests green (registry discovery, codec round-trip, lobby
rejoin rows).

---

## FB-04 — Per-account game records (replay + stats)

- **Report(s):** `20260606_001109_bug.txt` (second clause).
  > "Also game records should be saved by account for replay and stat purposes."
- **Priority:** P1.
- **Status:** in-progress — **specced** in [account-records-replay.md](account-records-replay.md) (Spec 32).

### Shape — FB-04 specced (the reframe)

Spec-time grounding found the data layer **already complete**: `hand_index` +
`hand_participants(account_id)` are written live (`registry.py` reserve/finalize), and
`find_hands_by_account` / `account_stats` / `account_score_series` exist — Spec 28's
profile already renders stats + a recent-hands list. `records/replay.py` already
reconstructs a hand from disk. The genuinely missing half is the **replay *viewer***: a
thin `GET_HISTORY`/`GET_REPLAY` wire API (projecting recorded events for the viewing seat,
reusing the live renderer) + a `<history-view>`/`<replay-view>` client surface with
authorization (participant = own seat; admin = public view). Full design + 10 fixtures in
Spec 32.

**Implemented 2026-06-07.** Shipped: `records/replay_stream.py`, `GET_HISTORY`/`GET_REPLAY`
wire + handlers (auth: participant→own seat, admin→public, else `not_authorized`;
corrupt-record→`replay_unavailable`), `<replay-view>` folding the projected stream through
the live reducer with step/play/scrub, and `[▶ watch]` on profile recent rows. Tests green
(projection/privacy, wire auth paths, replay transport). **Deferred:** a standalone
paginated "my games" view beyond the profile's recent-20 (`GET_HISTORY` is wired +
server-tested, no "load more" UI yet); match-replay; public-replays config.

---

## FB-05 — Table management / multi-human join UX

- **Report(s):** `20260606_002430_bug.txt`.
  > "There is no way for multiple players to manage which table they are joining etc. This
  > connects to the not being able rejoin bug."
- **Priority:** P2.
- **Status:** in-progress — **specced** in [table-management.md](table-management.md) (Spec 33).

### Shape — FB-05 specced

Spec-time grounding: the lobby (create/join/`START_HAND`, per-seat occupancy on the wire)
**already works** for one human + bots. Thin is the multi-human *coordination*: seated
players' **display names** (only `user_id` ships today), **live lobby refresh** on
occupancy change (fetch-only today), a clear open-human-seat vs bot-seat join, a readiness
/ start-authority display, and the held-seat "▶ Rejoin" row (consuming FB-03's
`HELLO.seat_holds` — the explicit FB-03↔FB-05 seam). So FB-05 is **lobby UX + a push-on-
change + display-name plumbing**, not new architecture. Full design + 8 fixtures in
Spec 33.

**Implemented 2026-06-07.** Shipped: `display_name` + `state` (LIVE/HELD) on
`TABLE_LIST.seats[]`; lobby seat rows show the name and mark dropped players "(away)";
held-seat rejoin rows come from FB-03. Tests green (server TABLE_LIST fields, lobby seat
display). **Deferred:** the server push-on-change (the existing 2s lobby auto-refresh
already keeps the list fresh — the spec lists polling as an acceptable fallback); explicit
start-authority reason chip; bot↔human mid-lobby seat conversion (Spec 33 open questions).

---

## FB-06 — Audio cues + clearer claim-opportunity notifications

- **Report(s):** `20260606_002336_feature.txt`.
  > "There needs to be satisfying sounds when you draw a tile, and also more clear
  > notification when you have a chi peng gang or hu opportunity... different sounds of
  > increasing intensity to build the hype."
- **Priority:** P2 — player-facing polish; client-only.
- **Status:** **implemented** (initial pass landed silent; re-fixed 2026-06-11 — see below).

### Shape (implemented)

Client-only ([mahjong/web/static/audio.js](../../mahjong/web/static/audio.js)): **synthesized**
Web Audio tones (no binary assets — build-free client), in two families:

1. A **private "your turn to decide" notification** (`alert`, a bright rising ding) — fired only
   on the local human's own `CLAIM_WINDOW` prompt with a real (non-PASS) claim. Tells *you* to
   call or pass; leaks nothing to opponents (only the prompt owner hears it).
2. **Public declaration cues**, heard by *every* seat the instant a claim lands, escalating in
   importance **chi < peng < gang < hu**. A `PENG`/`CHI`/exposed-`GANG` rides the authoritative
   `CLAIM_RESOLUTION` (so a losing contender never double-fires); a self-declared concealed/added
   kong rides its `CLAIM_DECISION` (no resolution exists); a winning `HU` rides the `HAND_END`
   frame (`cueForTerminal`). Plus the original soft `draw` blip on your own DRAW.

Pure cue-selection (`cueForEvent` / `cueForPrompt` / `cueForTerminal`) + a side-effecting
`audioCues.play` that no-ops when muted or when Web Audio is blocked (records `lastCue` for tests).
Wired into app.js's EVENT / PROMPT / HAND_END dispatch; a **Sound on/off** row in the Settings menu
(Spec 28), persisted to `localStorage` (`mahjong-sound`). No server/wire change.

### Why the first pass was silent (re-fixed 2026-06-11)

The user reported audio "raised in the past but not working." Three compounding causes, all fixed:

1. **Suspended AudioContext.** Browsers start a context created *outside* a user gesture in the
   `suspended` state. Ours was created lazily inside `play()` — triggered by an inbound websocket
   frame, never a gesture — so it stayed suspended and **every cue was silent**. Fix: an
   idempotent `AudioCues.unlock()` called on the first `pointerdown`/`keydown` (app.js
   `connectedCallback`), plus a `ctx.resume()` on the suspended context inside `play()`. This is
   the standard Web Audio autoplay-policy unlock dance.
2. **Declaration cues never existed.** The original `cueForEvent` only blipped on your own DRAW;
   there was *no* sound when a chi/peng/gang/hu was actually declared. Added the resolution /
   self-gang / terminal hooks above.
3. **The tests were vacuous.** `_wait_for_cue` polled `import('/static/audio.js').then(...)` inside
   `wait_for_function`, which treats the returned **Promise object as truthy** and resolves on the
   first poll without ever comparing the cue — so the suite was green while the feature did
   nothing. Fixed by stashing the module on `window.__cues` and polling a *synchronous* value
   predicate. (This is the [test-the-wire→UI-seam] failure mode in a new disguise.)

### Verification (all green + negative control)

- [tests/web/test_audio_cues.py](../../tests/web/test_audio_cues.py): real `<mahjong-app>` over the
  fake wire — own DRAW → `"draw"`; own claim prompt → `"alert"`; an **opponent's** PENG resolution
  → `"peng"` (public); a concealed kong → `"gang"`; a winning HAND_END → `"hu"`; an exhaustive
  draw → silent; muting suppresses everything. **7 passed.**
- **Negative control:** with the source reverted (test expectations kept), the 4 new
  notification/declaration assertions **fail** (timeout) and pass only with the fix — a genuine
  fail-without/pass-with artifact, per the project verification rule. Full fast web suite: 133
  passed.
- **Browser-verify still owed** (headless Web Audio can't actually sound; tracked in DEF-04).

---

## FB-07 — Feedback-tracking system

- **Priority:** P0 enabler (the meta-feature the user explicitly asked to build).
- **Status:** in-progress → see [feedback-tracking.md](feedback-tracking.md) (Spec 30).

This backlog doc is half of it (dev-facing source of truth); the other half is the live
status overlay + admin-console UI so the product owner can triage from the running server.

---

## FB-08 — Profile page unreachable / re-login on refresh — IMPLEMENTED

- **Report(s):** `20260605_194009_bug.txt`, `_194239_`, `_194244_` (triplicate — the
  duplication *was* Spec 29 Bug E, feedback double-submit).
- **Status:** implemented in **Spec 29** ([live-play-bugfixes.md](live-play-bugfixes.md)):
  Bug A (token → `localStorage`, refresh auto-`RESUME`s; profile reachable again) + Bug E
  (single-submit + auto-close). Merged PR #13.
- **Owed:** browser-verify on the deployed build (`verified` status pending a live check).

---

## FB-09 — Concealed fans awarded to an exposed hand; Self-Drawn fan missing

- **Report(s):** `20260611_003958_bug.txt` (ConnorL, 2026-06-11).
  > "There is a bug with concealed Pung and concealed Kong, I was playing and south won a
  > hand and was not supposed to have gotten a concealed hand, concealed pung concealed
  > kong. Also there was no extra fan for self drawn."
- **Priority:** P0 — wrong fan totals change winners' payments every hand.
- **Status:** **implemented** (2026-06-11).

**Root cause** (`mahjong/engine/pymj.py` `_melds_to_pack`): PyMahjongGB's pack `offer`
field uses **0 to mark a concealed meld**; an exposed meld must be non-zero. The wrapper
emitted the *absolute* `called_from_seat` as the offer, so **any meld claimed off seat 0
became offer=0** and the calculator scored the exposed meld as concealed — awarding bogus
"Fully Concealed Hand"/"Concealed Hand"/"N Concealed Pungs"/"Concealed Kong". (This is why
it mis-scored *many* hands: ~¼ of exposed melds are claimed off seat 0.) Verified directly
against MahjongGB: `offer=0` → "Fully Concealed Hand"; any non-zero → "Self-Drawn".

**Fix:** offer is now `0` only for `GANG_CONCEALED`, a fixed non-zero sentinel otherwise
(the 1..3 value is fan-irrelevant — only 0-vs-nonzero affects scoring). A second bug in the
same function was fixed alongside: CHI emitted its *claimed* tile instead of the run's
**middle** tile, so MahjongGB read the run shifted by one (a claimed B7 turned B7B8B9 into
B6B7B8) and granted bogus terminal-sensitive fans like "All Simples" on a hand holding B9.

**Regression test:** `tests/engine/test_pymj.py::test_exposed_melds_not_scored_as_concealed`
(seat 1's real `records/t1/hand_0000_3` self-draw with three CHI off seat 0 — pins no
concealed bonus, "Self-Drawn" present, and no "All Simples" with a terminal in a chow).
Note: without the +4 concealed bug the hand totals 7 < 8, so the bug was also letting an
**illegal-under-MCR self-draw** through.

## FB-10 — Fan→point conversion: live tables run `mcr-2006`, not the house ruleset

- **Report(s):** `20260611_004040_bug.txt` (ConnorL).
  > "Fan to point conversion is broken, should not be be negative twenty"
- **Priority:** P1.
- **Status:** **implemented** (2026-06-11).

The arithmetic was actually *correct for the ruleset the table ran*: the record header said
`ruleset: mcr-2006`, and the −20/+60 split is standard MCR table payment
(self-draw: each loser pays 8 + fan = 8 + 12 = 20). Two real issues hid under the report:
(1) the inflated `fan_total` came from FB-09 (now fixed); (2) the Spec-26 house ruleset
(`mcr-house-3fan.json`) is **not what live tables played** — the server default was
`mcr-2006`, whose 8-fan floor also blocked ordinary winning hands (the FB-15 "missed
mahjong" reports).

**Fix:** the server default ruleset is now `mcr-house-3fan` (3-fan floor) —
`mahjong/server/config.py` `default_ruleset`. Official MCR remains available via
`MAHJONG_DEFAULT_RULESET=mcr-2006` and will return as a per-table `CREATE_TABLE` choice
(deferred — see below). Verified end-to-end: the default config resolves to `fan_cliff: 3`,
and the real FB-15 hand (seat 2's Dragon-Pung + B9 wait, 5 fan) is now offered HU where it
was silently refused under the 8-fan floor.

## FB-11 / FB-12 — Mobile + quit-game; minimalist UI mode

- **Report(s):** `20260610_005659_feature.txt`, `20260610_005730_feature.txt`
  (tectoskepsis): mobile-friendly layout; no way to quit a game (on mobile at least).
  `20260611_003128_feature.txt` (ConnorL): "a minimalist UI which only shows discards,
  melds for other players and your tiles."
- **Priority:** P2 (client-only polish).
- **Status:** triaged — likely one spec: a compact/minimalist pane doubles as the mobile
  layout, and a "leave table" control falls out of the same menu work. Not started.

## FB-13 — Mid-hand table freeze at a pending prompt (2026-06-11 session)

- **Report(s):** `20260611_004828_bug.txt` (ConnorL):
  > "It seems when a bot or player gets a concealed gang, the replacement tile does not
  > work and the game freezes."
  Plus `20260610_003239_bug.txt` (Lillian, "help i cannot play the game") from the same
  evening. (The "concealed gang / replacement tile" framing turned out to be the player's
  read of the symptom; neither frozen record contains a GANG event near the stop.)
- **Priority:** P0 — game-breaking.
- **Status:** in-progress — stall watchdog + forensics fixes on `fix/hand-stall-watchdog`;
  root cause parked as **DEF-12**.

### Investigation (2026-06-11, from live records + the admin-console log ring)

Two tables froze in one evening, **both dead-stopping at the same game position**:

- `t1/hand_0000_1.jsonl` (seed `1781137499`): last event `seq 79, DRAW, seat 1 (human),
  tile T5, turn 32` at 00:46:04Z — then nothing. No timeout default ever fired (>1 h).
- `t2/hand_0000.jsonl` (**same seed** — per-table seeds collided too): last event
  `seq 79, DRAW, seat 1 (v0 bot), tile T5, turn 32` at 00:54:50Z. A *bot* seat — no human
  input, pacing ≤10 s, decide bounded at 30 s — yet nothing for 22+ min while the process
  stayed responsive (HTTP answered; CPU flat, so no spin — a parked await or a vanished task).

Ruled out by direct offline probing of the reconstructed frozen state: engine legality
(10 legal PLAYs for the actor), `project`/`_build_prompt`/`_default_action` (all clean),
v0 `decide` (returns instantly), `run_hand` awaits (every decide/observe is
`wait_for`-bounded; PacedAdapter clamps under the deadline), external cancellation (no
caller closes tables mid-hand). A full 4-bot `run_hand` on the same seed terminates
normally. No `hand_loop_crashed` line in the captured log ring — this is a **different
failure mode than FB-01** (DEF-01 stays open, unfired).

Confirmed config anomaly: t2's human DISCARD default fired at **267 s with
`crashed: true`** (= socket-drop + 180 s seat-hold expiry), not at the 60 s default with
`timeout: true` — so the live decide timeouts are overridden well above defaults, which is
how a zombie-but-connected client (TCP alive, page wedged) can hold a prompt open
near-indefinitely. That fully explains the *t1* freeze; the *t2 bot-turn* stop remains
unexplained → DEF-12.

### Fix (this branch) — contain the class, instrument the instance

1. **Per-step stall watchdog** in `run_hand` (`_guarded_step`): no phase step may exceed
   `4 × max(decide deadlines) + 60 s` (override: `step_stall_seconds`). On breach it logs
   `hand_step_stalled [DEF-12]` with hand_id/phase/actor/turn/next_seq **and the pending
   coroutine chain** (`stuck_at=` — the artifact DEF-12 waits for), then raises
   `HandStepStalled` so the existing FB-01 guards tear the table down gracefully (clients
   get a DETACH, not an eternal freeze). Deliberately uses `asyncio.wait`, not `wait_for`:
   a step that swallows cancellation cannot wedge the watchdog itself.
2. **`RecordWriter` flushes per event** — both frozen records were only readable up to the
   last 8 KiB flush boundary; records are the primary forensics artifact and must be
   durable line-by-line.
3. **`hand_loop_cancelled` logged in both hand loops** before re-raising — an unrequested
   cancel previously looked identical to this silent dead-stop.

**Verification:** [tests/table/test_step_stall_watchdog.py](../../tests/table/test_step_stall_watchdog.py)
(stall → logged + raised; cancellation-swallowing stall → still aborts, escalation logged;
cap derivation pinned; happy path unaffected) and the writer durability test in
[tests/records/test_writer.py](../../tests/records/test_writer.py). Full fast suite: 1120 passed.

## FB-14 — No way back to the main menu from a game — IMPLEMENTED

- **Report(s):** `20260611_022044_bug.txt` (ConnorL, 2026-06-11):
  > "Cannot go to main menu, there is no back feature."
  Corroborated by two same-evening frustration reports from a hung game
  (`20260611_020435`, `20260611_020523` — Lillian, not separately actionable).
- **Priority:** P0 — on its own this is the FB-11 quit-game gap (P2), but combined
  with a hung table (FB-13) it became a trap: the in-game loop had no exit, and on
  refresh the FB-03 auto-rejoin re-attached the lone HELD seat **straight back into
  the hung table**. The only escape was staying logged out for the 180 s hold expiry.
- **Status:** implemented (this branch).

### Root cause

Two halves, both "missing exit", not broken code:

1. **Server:** `MultiTableOrchestrator._handler` Phase 2 (attached) forwarded every
   frame to the table and only exited on socket drop. A client `DETACH` *did* release
   the seat (the mux acks `DETACHED`), but the connection stayed glued to the table —
   every lobby message thereafter got `ERROR unknown_kind`. There was no path back to
   the Phase 1 lobby loop.
2. **Client:** no UI sent `DETACH {reason: "leaving"}` at all — the wire kind existed
   since the session-mux spec but nothing triggered it (the FB-11 report's "no way to
   quit" half).

### Fix

- **Server** ([server/orchestrator.py](../../mahjong/server/orchestrator.py) `_handler`):
  Phases 1↔2 now cycle. Phase 2 returns the connection to the lobby loop after the
  leave kind matching how it entered — `DETACH` for a seated connection,
  `STOP_SPECTATING` for a spectator. The mismatched kind deliberately does **not**
  escape (the mux no-ops it; breaking out would strand a still-subscribed connection
  in the lobby). Works mid-hang: the dispatch runs in the connection read loop,
  independent of the (possibly wedged) hand task.
- **Client** ([web/static/app.js](../../mahjong/web/static/app.js)): a `[ ⌂ menu ]`
  header button in table view; first click arms (`[ leave? ]`, 4 s window — two-step
  confirm against stray clicks), second click sends `DETACH {reason: "leaving"}` and
  enters the lobby **optimistically** — no waiting on a server ack, because the whole
  point is escaping a server that may never answer. Leaving releases the seat
  (UNBOUND, not HELD), so the FB-03 auto-rejoin trap disarms itself.

### Verification

- [tests/server/test_leave_table.py](../../tests/server/test_leave_table.py) — 4 fixtures:
  DETACH → lobby usable again (re-LIST, re-ATTACH on the same connection); DETACH
  **mid-PROMPT** escapes (the hung-hand shape); spectator STOP_SPECTATING → lobby;
  role-mismatched kind does NOT escape. Reproduce-first: the first fixture failed
  with `ERROR unknown_kind` before the router change.
- [tests/web/test_leave_table_button.py](../../tests/web/test_leave_table_button.py) —
  real `<mahjong-app>` over the fake wire: button absent in lobby; two-step confirm;
  second click emits exactly `DETACH {reason: "leaving"}` and the lobby renders with
  no server ack.
- **Browser-verify owed** on the deployed build (→ DEF-04 bucket): click the button in
  a live game, land in the lobby, join a new table.

## FB-15 — Missed a mahjong (HU) on a discard of 9B / 7B

- **Report(s):** `20260611_015746_bug.txt` (Lillian) "missed a mahjong with a discard of
  nine b"; `20260611_032539_bug.txt` (North) "east discard seven dot which north would
  mahjong on but was not given option".
- **Priority:** P1.
- **Status:** **implemented** (2026-06-11) — it was hypothesis (1): the **8-fan MCR floor**.
  Reconstructed from `records/t1/hand_0000_3`: seat 2 (North) finished fully-concealed,
  tenpai on a **B9 pair wait** (W6W7W8 · B4B5B6 · T2T3T4 · J2J2J2 · B9). On a B9 ron that
  hand is Dragon Pung + Concealed Hand + Single Wait = **5 fan** — a legitimate win, but
  below `mcr-2006`'s 8-fan floor, so the engine *correctly-but-frustratingly* refused the
  HU and offered no claim window. Across the whole evening's last two hands, **zero HU
  opportunities** were ever surfaced; both hands resolved on self-draw. Fixed by the FB-10
  default-ruleset change (3-fan floor): the same hand now clears and HU is offered. No
  engine defect — the claim-window legality (`legality/claim.py`) was working as designed.

## FB-16 — Text-field keystrokes hijacked as game shortcuts

- **Report(s):** `20260611_025210_bug.txt` (ConnorL): "After starting a new hand, the
  ability for me to type a bug report was hampered. I could not input a space or an H. … it
  also kept reporting that I could Hu when I couldn't. When I press enter it accidentally
  discarded a tile."
- **Priority:** P1 — corrupts the bug-report box and chat, and can discard a tile / declare
  HU unintentionally mid-hand.
- **Status:** **implemented** (2026-06-11).

**Root cause:** `<game-pane>._handleKeydown` (`mahjong/web/static/app.js`) is a `window`
keydown listener that maps **Space→PASS, H→HU, Enter→PLAY(discard), letters→tile-select**
with no check for whether the user is typing in a text field. So in the bug-report textarea
(or chat), Space was swallowed as PASS, H toggled HU, Enter discarded the auto-selected
tile. The textarea lives in a Lit **shadow root**, so at `window` level `e.target` is the
retargeted host — the guard must read `e.composedPath()[0]`.

**Fix:** new `isEditableTarget(e)` helper bails the handler when the composed target is an
`INPUT`/`TEXTAREA`/`SELECT`/`contentEditable` node.
**Regression test:** `tests/web/test_prompt.py::test_typing_in_text_field_does_not_fire_action`
(Playwright: a focused shadow-root textarea + active prompt; Space/Enter must send no
`ACTION`). Pinned the shadow-DOM retargeting specifically — a naive `e.target` check passes
this test wrongly.

## FB-17 — Reconnect/refresh serves the deal-time snapshot (board desync; "new hand = same hand")

- **Report(s):** verbal (ConnorL, 2026-06-12, relaying seat 1/South — Helio — in game
  `20260612T154802Z-ef44e5-t1-h0`): "persistently not able to discard EW which was in my
  hand"; "not able to discard newly drawn tiles halfway through the hand"; "new hand gives
  the exact same hand as old hand upon refreshing the page".
- **Priority:** P0 — desyncs the board for the rest of the hand; makes specific tiles
  silently un-discardable; faked "same hand redealt" after refresh.
- **Status:** **implemented** (2026-06-12). `run_hand` gained a `state_callback`;
  both hand loops keep a `_live_state` ref the snapshot provider projects from;
  mux resume sends a fresh current snapshot and no longer replays EVENTs
  (would double-apply); the projection now carries `last_drawn` (redacted) and
  `terminal.final_hands` so a reconnect snapshot is self-sufficient. Specs
  amended: session-mux.md (§ Ring buffer, fixture 2, alternatives),
  state-schema.md (§ Per-seat projection). EVENT buffer is now vestigial →
  DEF-21. **Verification:** `tests/server/test_resume_snapshot.py` (3 fixtures,
  red before the fix), revised `tests/sessions/test_ring_buffer.py` fixture 2,
  projection tests in `tests/engine/test_state.py`. Browser-verify owed
  (refresh mid-hand on the deployed build) — tracked in DEF-04's next pass.

### Root cause (verified against spec + record)

`TableHandle._snapshot_provider` ([server/registry.py:479](../../mahjong/server/registry.py#L479))
returns `project_state(self._initial_state, seat)` — the **deal-time** state. `_initial_state`
is only reassigned between hands ([registry.py:892](../../mahjong/server/registry.py#L892)).
The session-mux replay buffer only accumulates while a seat is HELD
([sessions/mux.py:514-534](../../mahjong/sessions/mux.py#L514-L534)), and the same-user
takeover path replays nothing ([mux.py:425-436](../../mahjong/sessions/mux.py#L425-L436)).
So any refresh / socket drop mid-hand rebuilds the client from **the original deal plus only
the disconnect-window events** — every event between deal and drop is lost to the new page.

This violates the spec: [session-mux.md fixture 3](session-mux.md) asserts the resume
snapshot "matches **current** `project(state, seat)`". The mux unit tests pass because they
wire a current-state provider closure; the registry (and its twin
[web/server.py:206](../../mahjong/web/server.py#L206) — mirror-both-loops rule applies)
wires the stale one. Same seam-test gap as the wire→UI lesson: the provider contract was
never integration-tested. DEF-15's premise ("reconnect snapshot has no global discard
order") silently assumed the snapshot carried current per-seat discards — it carries none.

### How it explains all three symptoms

After a mid-hand resume the client hand = deal(13) + post-drop draws, re-sorted, **minus
nothing discarded before the drop**. Concretely for South: the F1 (East Wind) discarded at
seq 3 (15:53:20Z) **reappears** in the displayed hand. Pressing its key → `actionForKey`
matches the stale tile string against the server's `legal_actions`, which no longer contain
`PLAY F1` → returns `null` → **silent no-op, forever** ("persistently not able to discard
EW"). The oversized array (15+ tiles) shifts real tiles past the 14-slot key map, so newly
drawn tiles become hard/impossible to target ("can't discard drawn tiles"; latency spikes
of 68s/141s/87s on South's turns in the record). After HAND_END, a refresh re-serves the
hand-0 deal — which reads as "the new hand dealt me the exact same tiles".

### Fix shape

Make the snapshot provider serve the **current** authoritative state (per spec), not the
deal: thread a live-state read out of `mgr.run_hand` (e.g. a state-ref holder the table
manager updates per step) and have both hand loops' providers project from it. Buffer
replay semantics stay as-is. *Alternative rejected:* always-buffer-whole-hand + replay
(event-sourcing the resume) — keeps a second source of truth and still breaks on overflow.

### Verification fixtures

1. Mid-hand resume: run a scripted hand to turn N, drop + reattach a human seat, assert
   `ATTACHED.snapshot` equals `project(current_state, seat)` (concealed reflects all
   draws/discards to date). Pins fixture 3 at the **registry** boundary.
2. Same-user takeover mid-hand (refresh race): same assertion through the LIVE-takeover path.
3. Post-HAND_END refresh: reattach after terminal, assert snapshot is **not** the hand's
   deal (terminal or next-hand state, per lifecycle position).

## FB-18 — Drawn-tile discard targeting: Enter falls back to sorted-last, key map fights the display order

- **Report(s):** same session as FB-17 (contributing cause of "can't discard newly drawn
  tiles").
- **Priority:** P1 — wrong-tile discards and unreachable tiles even with a fully synced board.
- **Status:** **implemented** (2026-06-14).

### Root cause

Two related defects in the client input layer:

1. **Enter fallback targets the wrong tile.** `actionForKey`
   ([web/static/prompt.js:107-116](../../mahjong/web/static/prompt.js#L107-L116)) falls back
   to `ownConcealed[length-1]`, with a comment claiming that's "the just-drawn tile". The
   engine **sorts concealed after every draw**
   ([`engine/transition/__init__.py:140-141`](../../mahjong/engine/transition/__init__.py#L140-L141)),
   so the last element is the highest-sorting tile (honors), not the draw. This is the
   8.7.e bug class again (derive from a canonicalised collection instead of reading the
   authoritative `view.last_drawn.tile` slot).
2. **Digit keys index the raw sorted array; the display reorders it.**
   `renderOwnConcealedTiles` ([web/static/render.js:300-318](../../mahjong/web/static/render.js#L300-L318))
   pulls the just-drawn tile out of sort order and renders it last, but
   `TILE_CODE_TO_INDEX` ([prompt.js:26-41](../../mahjong/web/static/prompt.js#L26-L41))
   maps keys to **raw** indices. Every tile sorted after the drawn tile's slot is
   off-by-one between what the player counts on screen and what the key selects; the
   visually-last tile (the draw) is *not* selected by the last key.

### Fix (implemented)

The display-order computation `renderOwnConcealedTiles` did inline is now an exported pure
helper **`concealedDisplayOrder(concealed, view, ownSeat)`**
([web/static/render.js](../../mahjong/web/static/render.js)) — the single source of truth for
"which on-screen slot is which raw concealed index (`origIdx`)", shared by the renderer and
the keystroke layer.

1. **Enter default** ([prompt.js](../../mahjong/web/static/prompt.js) `actionForKey`): an
   explicit cursor selection still wins, but with none the fallback is the **just-drawn tile**
   (passed in as `lastDrawnTile`), not `ownConcealed[length-1]`. Tsumogiri now discards the
   draw.
2. **Position / arrow keys run in display order** ([app.js](../../mahjong/web/static/app.js)
   `_handleKeydown`): a tile key maps screen slot N → `order[N].origIdx`; arrows step through
   `order` and map back to `origIdx`. `selectedTile` stays a raw index (what the renderer
   marks `.selected` and `actionForKey` reads), so screen position N always means key N even
   though the draw renders out of sort order.

### Verification

[tests/web/test_prompt.py](../../tests/web/test_prompt.py) — four Playwright cases on a
seed-8 DISCARD snapshot where the dealer's draw (W3) sorts to the *front* (so on-screen
order ≠ raw order; sorted-last is J3): Enter → discards the draw W3 not J3; key `1` → first
on-screen tile W5 not raw[0] W3; key `]` → the draw W3 not raw[13] J3; ArrowLeft → on-screen
neighbour J3 not raw[12] T9. **Negative control:** all four fail against the unfixed source
(Enter→J3, keys hit raw indices) and pass only with the fix. (Playwright runs locally; CI
skips the web tree — DEF-22.)

## FB-19 — Next hand can start without every player's ready; no ready gate at match end

- **Report(s):** verbal (ConnorL, 2026-06-12): "it did not wait for all players to ready up
  when the end of the game was reached."
- **Priority:** P2 — flow correctness; compounded badly by FB-17 (a desynced player never
  sends READY and gets steamrolled or stranded).
- **Status:** **implemented** (2026-06-14) for the two live-path soft spots (2 + 3); the
  match-end gate (soft spot 1) is deferred — **DEF-24**, no live impact (`serve` runs
  `max_hands=None`). Exact trigger for the 2026-06-12 instance stays indeterminate (no
  persisted server logs — see DEF-20); the gate logging added below is what attributes the
  next one.

### The three gate soft spots ([server/registry.py](../../mahjong/server/registry.py))

1. **Match end skips the gate entirely**: the `max_hands` break
   ([registry.py:861-863](../../mahjong/server/registry.py#L861-L863)) exits before
   `_await_humans_ready()` is ever reached — the FB-02 gate only guards *between* hands.
   (Live `serve` runs `max_hands=None`, so this affects finite-match tables.)
2. **Non-LIVE humans are vacuously ready**: `_all_live_humans_ready`
   ([registry.py:720-723](../../mahjong/server/registry.py#L720-L723)) counts only LIVE
   seats — a player mid-refresh (HELD) at gate time is silently skipped, so the next hand
   can begin the moment the *other* player readies (or instantly, if no one is LIVE).
3. **120 s timeout advances silently** ([registry.py:737-738](../../mahjong/server/registry.py#L737-L738))
   with no signal to the still-reading player.

For the 2026-06-12 hand: HAND_END at 16:02:39Z, and **no hand-1 row was ever reserved**
(`hand_index` table) — the loop either sat in the gate until the server was killed
(~16:04) or crashed in the between-hands block; stdout was not captured, so the instance
can't be post-mortemed (hence DEF-20).

### Fix (implemented — soft spots 2 + 3)

Both live-path soft spots are fixed in [server/registry.py](../../mahjong/server/registry.py):

- **Soft spot 2 (HELD vacuously skipped).** `_live_human_seats` → `_gated_human_seats`,
  now counting human seats that are **LIVE *or* HELD**. A player mid-refresh (HELD) is no
  longer skipped: the between-hand gate holds for them up to `ready_timeout_seconds` (the
  backstop for a hold that expires without resuming). Pure-bot tables and all-UNBOUND tables
  stay vacuous (advance immediately) — bot self-play timing unchanged.
- **Soft spot 3 (silent advance).** `_await_humans_ready` now logs `ready_gate_opened`
  (which seats, the timeout) and `ready_gate_advanced reason=all_ready|timeout|vacuous|stop`
  — so a gate that holds too long, or advances while a player was reconnecting, is
  attributable from the server log (complements DEF-20, which still owes persisted logs).

**Verification** ([tests/server/test_ready_gate.py](../../tests/server/test_ready_gate.py)):
the existing FB-02 mechanics still pass; new cases drive a *real* seat to HELD (attach +
socket-drop) and assert it joins `_gated_human_seats()` and the gate does **not** advance
when only the other (LIVE) human readies — it advances only once the returning HELD player
readies too; plus log-reason assertions for the vacuous and timeout paths. **Negative
control:** both HELD tests fail against the old LIVE-only gate (seat 0's READY advances the
gate while seat 1 is still HELD) and pass only with the fix. Full server suite: 219 passed.

**Soft spot 1 (match-end gate) is deferred — DEF-24.** It has no live impact (`serve` runs
`max_hands=None`, so the loop never reaches the `max_hands` break), and gating at match end
changes finite-match-with-LIVE-human test behavior and needs a distinct match-end summary
state. Original fixture (c) — max-hands match end → summary held until ack — moves with it.

## Triaged but not yet fixed (this session's reports)

- **`20260611_030111` (claim priority):** "North discards one dot, priority was given to
  east for chi before south could claim peng." MCR priority is HU > PENG/GANG > CHI, and
  `table/manager.py::_resolve_claim_priority` enforces it — so either South's PENG window
  wasn't *offered*, or the CHI resolved before South's claim was collected. **Needs a
  record repro** (the cited hand predates the surviving `hand_0000_3`). Not yet an `FB-NN`;
  open until reproduced.
- **`20260611_025404` (score tracking):** "Game isn't tracking total scores across rounds."
  Cross-hand cumulative scores — separate from per-hand `score_delta`. Candidate next FB.
- **`20260611_030212` ("option for HU but no HU"):** likely a compound of FB-15 (floor) +
  FB-16 (the H key being eaten); re-verify in a live session on this build before opening a
  distinct item.

---

## Open questions

1. **Granularity of P1 splits.** FB-03 (reconnect) and FB-04 (per-account records) are each
   large enough to be multi-step specs. Split when specced.
2. **FB-01 vs Spec 29.** If repro confirms FB-01 is a Bug D regression, do we hotfix on a
   `fix/` branch ahead of the queue (game-breaking) rather than wait its turn? (Recommend
   yes.)
3. **Backlog ↔ console drift.** Spec 30 must define the reconciliation direction (this doc is
   canonical) and ideally a check that flags divergence.
