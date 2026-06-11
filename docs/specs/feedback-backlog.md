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
| DEF-04 | **Browser-verify owed** on the deployed build: FB-06 audio, FB-07 console, FB-08 (Spec 29 token/profile), Spec 22 §22.x UI, Spec 25 admin tunnel/feedback/training panes, cardinal pinwheel, **PR #16 chi-picker + opponent-concealed-kong render** (2026-06-10 session ran pre-PR-16 code — reports `20260610_002312`, `20260611_004346` are stale-build, not regressions), **Spec 34 minimal play view + in-game player names** (default view flipped to minimal; large-print legibility, claim banner, `Alt+M` toggle — Playwright-green, visual pass owed). | Unit/Playwright-green; real-device pass not yet run. | Next live deploy / play session — flip each to `verified`. | (this doc + Spec 22/25/34) |
| DEF-05 | Auth: real auth-targeted rate limiter (e.g. 10 failures/IP/hr); RESUME token rotation. | Friends-and-family + connection-wide cap suffices pre-S7. | S7 ops hardening, or a public-abuse signal. | [auth.md:23](auth.md), [auth.md:294](auth.md) |
| DEF-06 | Late-join **replay-from-record** (catch a mid-hand joiner up). | Refusal gate (Spec 20) is enough; needs per-table replay-lock design. | Someone actually requests mid-hand late-join. | [late-join-replay.md:133](late-join-replay.md) |
| DEF-07 | Decide-timeout heartbeat extension (`PROMPT_HEARTBEAT`: keep an engaged human's clock alive). | Needs wire + client + timer-reset work; fixed timeout OK for now. | Players report being timed out while actively deciding. | [human-decide-timeout.md:94](human-decide-timeout.md) |
| DEF-08 | Scoring-config false-mahjong penalty. | Declared in config schema but unreachable in-engine today. | The engine can produce an illegal-declared-win state. | [scoring-config.md](scoring-config.md) |
| DEF-09 | Admin-console: control-plane login + network bind; `systemd` supervisor switch. | v1 ships the script + token-gated status. | Production Linux deploy. | [admin-console.md:35](admin-console.md), [admin-console.md:383](admin-console.md) |
| DEF-10 | Feedback-tracking: status filter + auto-archive of `implemented`/`wontfix` rows. | Nice-to-have; backlog is short. | Pane gets noisy enough to need it. | [feedback-tracking.md:137](feedback-tracking.md) |
| DEF-11 | **Replay divergence**: `records/replay.py` cannot re-apply the live record `t1/hand_0000_1.jsonl` (2026-06-11) — raises `IllegalAction(seat=1, PLAY B4, legal_count=0)` mid-record, so the FB-04 replay viewer breaks on hands with claim choices. Suspect the CHI-with-chosen-tiles event→action translation. | FB-13 forensics took priority; viewer works for most records. | A player hits a broken replay, or before any forensic replay of a live record is trusted again. | the record file + `replay(` in [records/replay.py](../../mahjong/records/replay.py) |
| DEF-12 | **FB-13 root cause** (the exact await where the live hand task wedged). The watchdog converts any future stall into a logged, position-stamped abort with the pending coroutine chain; the underlying trigger is still unidentified (see FB-13 section: every decide/observe await is provably bounded, yet two tables dead-stopped). Also determine the live `MAHJONG_DECIDE_TIMEOUT_*` env (observed behavior implies ≫ defaults). | No deterministic repro; all bounded-await candidates ruled out offline. Instrument-and-defer per the FB-01 template. | `hand_step_stalled [DEF-12]` or an unexpected `hand_loop_cancelled` appears in a run → the `stuck_at=` chain names the wedged await; fix it directly. | `hand_step_stalled` ([table/manager.py](../../mahjong/table/manager.py) `_guarded_step`), `hand_loop_cancelled` (both hand loops) |
| DEF-13 | **Persistence/record collisions across server restarts**: table ids restart at `t1`, so `hand_index.record_path` UNIQUE fails (`persistence.reserve_hand_failed`, 3× on 2026-06-11) — those hands are invisible to history/replay — and the new run **overwrote** the old `t1/hand_0000.jsonl` record (the original FB-01 evidence file is gone). | Needs a per-boot uniqueness scheme (boot-scoped record dir or id offset) — small design decision, not a hotfix. | Next `persistence.reserve_hand_failed` in a log, or before any analysis that assumes records are immutable. | `persistence.reserve_hand_failed` ([server/registry.py](../../mahjong/server/registry.py) `_reserve_hand_row`) |
| DEF-14 | **Flaky test**: `test_fixture_21a_disconnect_and_reconnect_within_hold_window` fails ~1-in-3 full-suite runs **on clean main** (reproduced 2026-06-11 while building FB-14; not introduced by it). Timing-sensitive hold-window assertion suspected. | Pre-existing; unrelated to the FB-14 branch that surfaced it. | Next CI/local failure of this test, or any change to seat-hold timing in `sessions/mux.py`. | `pytest tests/server/test_multi_human_e2e.py::test_fixture_21a_disconnect_and_reconnect_within_hold_window` |
| DEF-15 | **Minimal-view combined pond ordering on mid-hand reconnect**: `view.discard_pond` is exact when a hand is watched from the start, but a reconnect snapshot has no global discard order, so the seed approximates by round-robin-from-dealer interleave (self-heals as play continues). | The from-start path (the normal case) is exact; reconnect is rare and the pond self-corrects. Exact ordering needs per-discard sequence in the snapshot. | A player notices a mis-ordered pond right after reconnecting, or per-discard timestamps get added to the projection. | `_seedPond` in [web/static/apply_event.js](../../mahjong/web/static/apply_event.js); [minimal-play-view.md](minimal-play-view.md) |

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
- **Status:** triaged.

### Shape (implemented)

Client-only ([mahjong/web/static/audio.js](../../mahjong/web/static/audio.js)): **synthesized**
Web Audio tones (no binary assets — build-free client) — a soft blip on the local human's own
DRAW, and an **escalating** tone on a claim window (CHI < PENG < GANG < HU). Pure cue-selection
(`cueForEvent` / `cueForPrompt`) + a side-effecting `audioCues.play` that no-ops when muted or
when Web Audio is blocked (records `lastCue` for tests). Wired into app.js's EVENT/PROMPT
dispatch; a **Sound on/off** row added to the Settings menu (Spec 28), persisted to
`localStorage` (`mahjong-sound`). No server/wire change — the claim window is already signalled
(existing `isClaimAvailable` visual chip stays); this is the audio layer.

### Verification (all green)

- [tests/web/test_audio_cues.py](../../tests/web/test_audio_cues.py): real `<mahjong-app>` over
  the fake wire — own DRAW → `lastCue == "draw"`; a PENG claim prompt → `"peng"`; muting
  suppresses the cue. Full fast suite 1078 passed (+ the settings-rows test updated for the new
  Sound row). **Actual audio is browser-verify-owed** (headless Web Audio needs a user gesture).

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
