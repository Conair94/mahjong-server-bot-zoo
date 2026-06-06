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
| FB-03 | feat | Reconnect / rejoin an in-progress game | P1 | triaged | TBD |
| FB-04 | feat | Per-account game records (replay + stats) | P1 | triaged | TBD |
| FB-05 | feat | Table management / multi-human join UX | P2 | triaged | TBD |
| FB-06 | feat | Audio cues + clearer claim-opportunity notifications | P2 | implemented | (this doc) |
| FB-07 | meta | Feedback-tracking system (this backlog + admin console) | P0 (enabler) | implemented | [feedback-tracking.md](feedback-tracking.md) |
| FB-08 | bug | Profile page unreachable / re-login on refresh | — | implemented | [live-play-bugfixes.md](live-play-bugfixes.md) (Spec 29 Bug A/E) |

Priority key: **P0** ship next · **P1** important, larger · **P2** polish.

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
- **Priority:** P1 — high value, architecturally significant.
- **Status:** triaged.

### Shape

Spec 29 Bug A persists the session token (`localStorage`) so a **refresh re-authenticates**,
and `RESUME` ([session-mux.md](session-mux.md)) reattaches a *websocket*. What's missing is
**rejoining the table/seat** you held when you dropped: seat-hold semantics, replaying the
hand state to a returning player, and the lobby affordance to get back in. Overlaps
[late-join-replay.md](late-join-replay.md) (spectator catch-up) and FB-05 (table mgmt).
Non-trivial: spec must define seat-hold timeout, what a bot does while a human is away, and
the state-replay frame on rejoin.

---

## FB-04 — Per-account game records (replay + stats)

- **Report(s):** `20260606_001109_bug.txt` (second clause).
  > "Also game records should be saved by account for replay and stat purposes."
- **Priority:** P1.
- **Status:** triaged.

### Shape

Records are currently keyed **by table/hand** (`data_dir/records/<table>/hand_NNNN.jsonl`),
not by account, so there's no "my games" view. Needs: an account↔record index (seat→account
association persisted with each hand), a query/listing API, and a replay/stats surface in the
client. Builds on [record-format.md](record-format.md) and the SQLite persistence layer
([persistence-api.md](persistence-api.md), [sqlite-schema.md](sqlite-schema.md)). Large;
likely split into (a) the index/association, (b) the list/replay API, (c) the UI.

---

## FB-05 — Table management / multi-human join UX

- **Report(s):** `20260606_002430_bug.txt`.
  > "There is no way for multiple players to manage which table they are joining etc. This
  > connects to the not being able rejoin bug."
- **Priority:** P2.
- **Status:** triaged.

### Shape

The lobby lists tables and supports join (Layer 8 / [multi-human-seats.md](multi-human-seats.md)),
but the UX for *coordinating* which table multiple humans land on is thin. The user links this
to FB-03. Spec should clarify the multi-human lobby flow (table visibility, seat selection,
who can start the hand) and likely share groundwork with FB-03's seat-hold/rejoin.

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
