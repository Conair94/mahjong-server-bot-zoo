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
| FB-01 | bug | Concealed-gang hang / concealed tiles not displayed | **P0** (game-breaking) | triaged | TBD (Spec 31?) |
| FB-02 | bug+feat | End-of-game summary too brief — needs ready-up / acknowledge gate | **P0** (user "urgent") | triaged | TBD |
| FB-03 | feat | Reconnect / rejoin an in-progress game | P1 | triaged | TBD |
| FB-04 | feat | Per-account game records (replay + stats) | P1 | triaged | TBD |
| FB-05 | feat | Table management / multi-human join UX | P2 | triaged | TBD |
| FB-06 | feat | Audio cues + clearer claim-opportunity notifications | P2 | triaged | TBD |
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

### Verification (reproduce-first — no blind fix)

- Drive a seeded game where a bot declares a concealed kong; assert the client reducer +
  renderer produce four face-down tiles and the turn advances (fails on current `main` if
  the hypothesis holds).
- If confirmed: handle the `hidden`/no-`tiles` meld in `apply_event.js` + `render.js`
  without throwing; add a wire→UI dispatch test for the opponent concealed-gang frame.

---

## FB-02 — End-of-game summary too brief; needs ready-up gate

- **Report(s):** `20260606_002220_bug.txt`.
  > "The end of game display worked but lasted about a second, I was playing on fast mode.
  > The feature should wait for all human players to ready up for the next match and
  > acknowledge they read the summary."
- **Priority:** P0 — the user's standing "urgent" item; the Spec 29 cache-bust made the
  summary *appear*, but it auto-advances too fast (worse on fast bots).
- **Status:** triaged.

### Shape

Spec 29 confirmed the `HAND_END` summary renders correctly. The gap is **flow control**:
after `HAND_END`, the table should **pause for a human acknowledgement** ("Ready" /
"Next hand") instead of immediately starting the next hand. Open design points for the
spec: does the gate apply to solo-vs-bots tables (user plays solo), per-human ready in
multi-human tables, and a timeout fallback so an idle human can't stall bots forever
(cf. [human-decide-timeout.md](human-decide-timeout.md)).

### Verification

- Integration: after a `HAND_END`, the next hand does **not** start until a `READY`
  (name TBD) frame arrives from each seated human; a timeout path still advances.
- Browser: summary stays up until "Ready" is clicked.

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

### Shape

Client-only: Web Audio cues on draw + escalating cues for CHI < PENG < GANG < HU claim
windows, plus a clearer visual prompt when the human has an actionable claim. Respect a mute
toggle in the existing Settings menu (Spec 28). No server/wire change expected — the claim
window is already signalled; this is presentation. Keep assets tiny/synth (no large binaries
in git; cf. [var/-runtime-not-committed] discipline for any generated audio).

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
