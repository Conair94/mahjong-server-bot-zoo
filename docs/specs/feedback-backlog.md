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
| DEF-04 | **Browser-verify owed** on the deployed build: FB-06 audio, FB-07 console, FB-08 (Spec 29 token/profile), Spec 22 §22.x UI, Spec 25 admin tunnel/feedback/training panes, cardinal pinwheel. | Unit/Playwright-green; real-device pass not yet run. | Next live deploy / play session — flip each to `verified`. | (this doc + Spec 22/25) |
| DEF-05 | Auth: real auth-targeted rate limiter (e.g. 10 failures/IP/hr); RESUME token rotation. | Friends-and-family + connection-wide cap suffices pre-S7. | S7 ops hardening, or a public-abuse signal. | [auth.md:23](auth.md), [auth.md:294](auth.md) |
| DEF-06 | Late-join **replay-from-record** (catch a mid-hand joiner up). | Refusal gate (Spec 20) is enough; needs per-table replay-lock design. | Someone actually requests mid-hand late-join. | [late-join-replay.md:133](late-join-replay.md) |
| DEF-07 | Decide-timeout heartbeat extension (`PROMPT_HEARTBEAT`: keep an engaged human's clock alive). | Needs wire + client + timer-reset work; fixed timeout OK for now. | Players report being timed out while actively deciding. | [human-decide-timeout.md:94](human-decide-timeout.md) |
| DEF-08 | Scoring-config false-mahjong penalty. | Declared in config schema but unreachable in-engine today. | The engine can produce an illegal-declared-win state. | [scoring-config.md](scoring-config.md) |
| DEF-09 | Admin-console: control-plane login + network bind; `systemd` supervisor switch. | v1 ships the script + token-gated status. | Production Linux deploy. | [admin-console.md:35](admin-console.md), [admin-console.md:383](admin-console.md) |
| DEF-10 | Feedback-tracking: status filter + auto-archive of `implemented`/`wontfix` rows. | Nice-to-have; backlog is short. | Pane gets noisy enough to need it. | [feedback-tracking.md:137](feedback-tracking.md) |

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

## Open questions

1. **Granularity of P1 splits.** FB-03 (reconnect) and FB-04 (per-account records) are each
   large enough to be multi-step specs. Split when specced.
2. **FB-01 vs Spec 29.** If repro confirms FB-01 is a Bug D regression, do we hotfix on a
   `fix/` branch ahead of the queue (game-breaking) rather than wait its turn? (Recommend
   yes.)
3. **Backlog ↔ console drift.** Spec 30 must define the reconciliation direction (this doc is
   canonical) and ideally a check that flags divergence.
