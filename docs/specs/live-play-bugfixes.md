# Spec 29 — Live-play bug roundup (ConnorL session, 2026-06-05)

Five bugs found in real play on the deployed server, all from a single solo game
(`ConnorL` + three `v0` bots, table `t1`, hand `t1-h0`). Grouped into one spec
because they were found together and are mostly small, independent client/wire
correctness fixes — the same pattern as the earlier three-bug roundup in
`b7318da`. They do **not** depend on each other and can land in any order;
priority ordering is called out per the user.

Evidence base: the recorded game at
`~/.local/share/mahjong-server/records/t1/hand_0000.jsonl` (244 events, replayable)
and the three feedback reports at
`~/.local/share/mahjong-server/reports/`.

Builds on / touches:
- [wire-protocol.md](wire-protocol.md) — `RESUME`, `CLAIM_RESOLUTION`, `HAND_END` framing; per-seat projection privacy.
- [state-schema.md](state-schema.md) — `project_event` / per-seat projection; concealed-kong privacy.
- [record-format.md](record-format.md) — `CLAIM_DECISION` / `CLAIM_RESOLUTION` emission rules (`mahjong/records/diff.py`).
- [session-mux.md](session-mux.md) — `RESUME` server path.
- [feedback-reporting.md](feedback-reporting.md) — Spec 23 feedback modal + `FEEDBACK_ACK`.
- The Lit client (`mahjong/web/static/app.js`, `apply_event.js`, `feedback.js`, `render.js`).

**Status:** in progress. Decisions locked 2026-06-05 — **Bug C: authoritative
`CLAIM_RESOLUTION`** (move mutation off `CLAIM_DECISION`, server always emits one
resolution per window incl. GANG); **Bug A: `localStorage`**.

- **Bug C — LANDED 2026-06-05.** `diff.py` emits a `CLAIM_RESOLUTION`
  (`called_tile` + `winning_kind`) for exposed kongs; client `apply_event.js`
  applies PENG/CHI/GANG-EXPOSED on the resolution (decisions are informational;
  self-initiated concealed/added kongs still apply on the decision). Tests:
  `test_diff.py` (resolution emitted for exposed, none for concealed) +
  `test_reducer_claim_resolution.py` (the ConnorL contested-window regression —
  losing CHI leaves no phantom meld).
- **Bug A — LANDED 2026-06-05.** Session token persisted to `localStorage`
  (`mahjong.session_token`), rehydrated in the constructor so HELLO auto-`RESUME`s
  on reload; "resuming" splash suppresses the login-form flash; stale token is
  cleared on `RESUME` rejection. Test: `test_session_persist_e2e.py`
  (reload-keeps-login + stale-token fallback). Full suite 1038 passed.
- **Bug B — REPRODUCED + FIXED 2026-06-05.** Reproduction (`test_hand_end_dispatch.py::test_hand_end_draw_frame_renders_summary`) proved the exhaustive-draw summary renders fine through the real client — **no render defect on `main`**. Root cause: the static server (`mahjong/wire/server.py`) sent **no cache headers**, so browsers heuristically cached `app.js` and served a stale pre-`b7318da` bundle. Fix: `Cache-Control: no-cache` + content-derived `ETag` + `If-None-Match` → 304 on every static asset (build-free cache-busting). Tests in `tests/wire/test_server.py` (revalidation + ETag-changes-on-edit).
- **Settings-menu bug (user-reported follow-up) — FIXED 2026-06-05.** "Settings menu sometimes won't open/close depending on UI colour." Root cause: `_settingsOpen` (and `_profile`, `_serverFeatures`) were **not declared as Lit reactive properties**, so mutating them scheduled no re-render — the menu only appeared/closed when an unrelated reactive change (e.g. a theme/**colour** toggle) flushed a render. Fix: add them to `static properties`. Repro+regression: `test_settings_toggle.py` (open + ×/Esc/backdrop close, both themes). Also **removed the redundant tile-style toggle from the header** (now only in the Settings menu; Alt+U still works).
- **Bug D — LANDED 2026-06-05.** `project_event` redacts a CONCEALED kong's
  `tile` from non-owners; `project` masks an opponent's `GANG_CONCEALED` meld
  tiles (`hidden: true`, no tiles) — settlement `final_hands` still reveals all.
  Client `apply_event.js` builds a hidden meld for an opponent's kong; `render.js`
  draws four face-down tiles for `hidden` melds. Tests: `test_state.py`
  (redaction + meld masking, exposed/added stay public) + `test_concealed_kong_privacy.py`.
- **Bug E — LANDED 2026-06-05.** `feedback.js` guards `_onSubmit` against repeat
  dispatch and auto-closes + clears the dialog ~1.4s after `FEEDBACK_ACK` ("Feedback
  received — thank you!"). Tests: `test_feedback_component.py` (single dispatch on
  repeat clicks; auto-close clears the draft).
- **All of Spec 29 (A–E + settings/header) complete; full suite 1050 passed.**

---

## Priority

| # | Bug | User priority | Root cause confidence | Blast radius |
| --- | --- | --- | --- | --- |
| B | End-of-game display "still" doesn't show | **Highest (urgent)** | Low — wire→UI path is already correct in `main` | Repro-first |
| C | Phantom 5th meld → could not declare mahjong | High (data correctness) | **High** — confirmed from the replay | Client reducer + wire |
| A | Must log in again after page refresh | Medium | **High** — confirmed | Client (+ small server) |
| D | Bot's concealed kong shown face-up to opponents | Low | **High** — confirmed | Server projection + client render |
| E | Feedback submits multiple times; no clear confirmation | Low | **High** — confirmed (3 dup reports) | Client only |

---

## Goals

- Fix the data-correctness bug (C) so a contested claim never leaves the human's hand desynced from the server's authoritative state.
- Make the session survive a page refresh (A) so the profile page and game are actually reachable after a reload.
- Reproduce and close the end-of-game display gap (B) with a verification artifact, not another blind code change.
- Stop leaking concealed-kong tile identity to opponents (D).
- Make feedback submit exactly once and self-close with a clear confirmation (E).

## Non-goals

- No change to the rules engine's *authoritative* claim resolution — `_resolve_claim_priority` already picks the right winner (the server-side state is correct in the replay). The bugs are in **projection and client reconstruction**, not adjudication.
- No new reconnect/seat-hold semantics beyond what `session-mux.md` already specifies for `RESUME`.
- No redesign of the feedback storage format.

---

## Bug C — Phantom 5th meld, hand could not win (confirmed from replay)

### Symptom (user)
"At some point I had 5 melds and was unable to mahjong. I think my opponent drew
a gang and somehow I got an extra tile."

### What actually happened (from the replay)
At `seq 150` seat 3 discarded `B6`, opening a contested claim window (`seq 151`):

- seat 0 (ConnorL) could **CHI** `B6 B7 B8`
- seat 2 could **PENG** or **GANG** `B6`

Both seats submitted: `seq 152` = seat 0 `CHI`, `seq 153` = seat 2 `GANG (EXPOSED)`.
GANG outranks CHI, so the engine correctly awarded `B6` to seat 2 — seat 2's final
hand has the `GANG_EXPOSED B6` meld and seat 0 has **no** `B6B7B8` meld. The
server state is correct.

But notice what the event stream does **not** contain: after `seq 153` it jumps
straight to `seq 154` (seat 2's kong-replacement DRAW). There is **no
`CLAIM_RESOLUTION`** telling seat 0 its CHI lost. Every *uncontested* window in
the same game emits a `CLAIM_RESOLUTION`; the GANG-override window does not.

### Root cause (two layers)
1. **Client reducer applies every claim optimistically.** In
   [apply_event.js](../../mahjong/web/static/apply_event.js), `applyClaimDecision`
   *mutates* the seat view for **any** `CLAIM_DECISION` it sees — it pushes the
   meld and removes the support tiles, assuming that decision wins.
   `applyClaimResolution` is a deliberate **no-op** (its comment says "state
   mutations happened in the preceding CLAIM_DECISION event"). So when seat 0's
   own losing `CHI` decision arrives, the client builds a phantom `B6B7B8` meld
   and deletes `B7`/`B8` from the concealed hand — and nothing ever reverts it.
2. **The wire never signals the loss.** In
   [diff.py](../../mahjong/records/diff.py), the `GANG` branch emits the gang
   `CLAIM_DECISION` + the replacement `DRAW` but **no `CLAIM_RESOLUTION`**. The
   table manager (`mahjong/table/manager.py:_step_claim_window`) emits a
   `CLAIM_DECISION` for *every* claimer in a contested window, then applies only
   the winner — the losers get no rejection event.

Downstream effect: the phantom meld persisted, so the client showed **5 melds**
(2 real CHI + phantom CHI + later PENG + later CHI) and a concealed hand short two
tiles. The hand could never be presented as a legal win → "unable to mahjong."
The server, meanwhile, had a perfectly legal 13-tile tenpai hand the whole time.

### Fix — make `CLAIM_RESOLUTION` authoritative on the client (recommended)
Invert the reducer's trust: `CLAIM_DECISION` becomes purely informational (it may
still drive a transient "seat N is deciding…" cue), and **all meld/hand mutation
moves to `CLAIM_RESOLUTION`**, which already names `winning_seat`, `winning_claim`,
and `winning_chi_tiles`. This matches the comment intent in `apply_event.js`
("resolution is informational") — but reverses which event is authoritative.

Requires a wire change so the resolution is always present and self-describing:

- **Server: always emit exactly one `CLAIM_RESOLUTION` per closed window**,
  including GANG winners. Extend `diff.py`'s `GANG` (exposed-claim variant) to
  emit `_claim_resolution_claimed`. Concealed/added kongs are self-initiated from
  `DISCARD` (no window) and need no resolution.
- **Server: `CLAIM_RESOLUTION` carries the called tile** so the client can build
  the meld from the resolution alone (today PENG/GANG resolutions rely on
  `last_discard`, which the client still has, but making it explicit removes the
  ordering dependency). Proposed shape:

  ```json
  {
    "event": "CLAIM_RESOLUTION",
    "outcome": "CLAIMED",
    "winning_seat": 2,
    "winning_claim": "GANG",
    "winning_kind": "EXPOSED",
    "called_tile": "B6",
    "winning_chi_tiles": null
  }
  ```

- **Client:** move the PENG/CHI/GANG mutation logic out of `applyClaimDecision`
  into `applyClaimResolution`, keyed off `winning_seat` + `winning_claim`. A
  losing claimer's `CLAIM_DECISION` then mutates nothing, so there is nothing to
  roll back.

### Alternative (smaller blast radius) — server suppresses losing decisions on the wire
Keep the client as-is, but have the per-seat projection drop the *losing*
claimers' `CLAIM_DECISION` frames (they remain in the on-disk record for
audit/replay, just not fanned out to clients). Then the only `CLAIM_DECISION` a
client ever applies is the winner's, and the optimistic apply is always correct.
Cheaper, but it leaves the reducer's fragile "every decision wins" assumption in
place and keeps two divergent event streams (record vs wire) — flagged as a new
conversion boundary, which we generally avoid. **Recommend the authoritative-
resolution fix; this is the fallback if that proves too large.**

### Verification fixtures
- **Replay regression:** feed the recorded `hand_0000.jsonl` claim sub-sequence
  (`seq 150–159`) through the client reducer; assert seat 0 ends with exactly the
  melds in `HAND_END.final_hands` (no `B6B7B8`) and the correct concealed count.
  This is the seeded-rollout artifact — it fails on today's code, passes after.
- **Unit (reducer):** contested window, two `CLAIM_DECISION`s + one
  `CLAIM_RESOLUTION` → only the winner's meld appears.
- **Codec round-trip** for the extended `CLAIM_RESOLUTION` fields
  (per the KNOWN_KINDS allow-list rule).
- **Determinism:** re-freeze any record goldens if the GANG resolution event
  changes the event count (it adds one event per exposed-kong claim).

---

## Bug A — Session lost on page refresh (confirmed)

### Symptom (user)
"If I refresh the page I have to log back in." (Filed as "profile page not
working" — the profile button is unreachable once you're bounced to the login
form.)

### Root cause
In [app.js](../../mahjong/web/static/app.js) the session token is held in memory
only: `this._sessionToken = null; // stored in memory; RESUME is a v2 concern`.
A `RESUME` path exists, but it only fires on **websocket reconnect within the same
page** and reuses the in-memory token. A full page reload reinitializes the app,
`_sessionToken` is `null`, there is nothing to `RESUME` with → the auth form
shows.

### Fix
Persist the session token to `localStorage` (the client already uses it for theme
and tile-style, with the same private-mode try/catch fallback). On `firstUpdated`,
read it back; if present and the server advertises `auth`, send `RESUME` instead of
rendering the auth form. On `AUTH_RESPONSE { ok:true }` write the token; on logout
/ `RESUME` rejection clear it.

- Storage key: `mahjong.session_token` (namespaced like the existing keys).
- Token TTL/expiry is already enforced server-side (`auth.md`); a stale token
  simply yields a `RESUME` rejection → fall back to the auth form, clearing it.

### Security note
The token is a bearer credential. `localStorage` is XSS-readable; this is the same
exposure the in-memory token already has against injected script, and the client
is build-free first-party ASCII with no third-party script. `sessionStorage` is
the more conservative option but does **not** survive a tab reopen — and the user
explicitly wants refresh to keep them logged in, which `sessionStorage` *does*
satisfy (it survives reload, just not tab-close). **Open question below.**

### Verification fixtures
- **Playwright (async):** log in, `page.reload()`, assert we land in the lobby
  (no auth form) and the `[ profile ]` button is present and opens the profile.
- **Unit:** `RESUME`-rejection path clears the stored token and shows auth.

---

## Bug B — End-of-game display "still" doesn't show (urgent; reproduce first)

### Symptom (user)
"The end of game display still does not work." ("Still" because `b7318da` already
fixed the HAND_END wire→UI dispatch.) The played game ended in an **exhaustive
draw** (`HAND_END.kind == "DRAW"`, empty `winner`).

### Investigation result — no code defect found on `main`
The full path exists and is unit-tested in current `main`:
- Server sends a top-level `HAND_END` frame for draws too (`mux.py` `observe()`
  routes any `event == "HAND_END"`, including the draw terminal that ended this
  game; the record confirms `seq 242 HAND_END` was emitted and fanned out).
- Client dispatches it (`app.js` line ~2143) — the `pane` reference is stable
  because `<table-page>` is always in the DOM (`?hidden` toggle, not conditional
  creation), so the captured `pane.seatView` is valid at hand-end.
- `applyHandEnd` handles the draw (`winner:[]` → `null`); `renderHandEndSummary`
  renders "Exhausted draw — no winner" + scores + revealed hands; the `.he-*`
  CSS lives in `<game-pane>`'s shadow DOM.
- Tests cover both HU (`test_hand_end_dispatch.py`) and draw-summary rendering
  (`test_hand_end_summary.py::test_draw_summary_shows_no_winner`).

So I **could not reproduce a defect from the code**. The likely real causes, in
order of suspicion:
1. **Stale cached client.** The client is served as static files with **no build
   step and no cache-busting** (CDN import-map). A browser holding a pre-`b7318da`
   `app.js` would still show the old broken behavior after the server was fixed.
   Bug A compounds this: refreshing to re-login is exactly when a cached bundle
   bites.
2. **A draw-terminal-specific gap** not covered by the *dispatch* test
   (the draw dispatch path — `winner:[]` over the real wire — has only the
   renderer pinned with `winner:None`, not an end-to-end dispatch test).
3. An environment/runtime error visible only in the browser console.

### Fix (reproduce-first, per "no learning claim without a verification artifact")
1. **Reproduce in a real browser** against current `main`: play/inject a draw
   terminal, watch the wire log for the `HAND_END` frame and the console for
   errors, and confirm whether the `.hand-end-summary` DOM renders. Capture the
   actual failing link.
2. **Add the missing end-to-end dispatch test for a DRAW terminal**
   (`winner:[]`, `score_delta:[0,0,0,0]`) — the gap in coverage that let a
   draw-specific bug hide, if there is one.
3. **If it's stale-cache:** add cache-busting to the static assets (content hash
   or a `?v=<git-sha>` query on the import-map entries and `app.js`), so a server
   update reliably reaches clients. This is the durable fix and protects every
   future client change.

### Verification fixtures
- Browser repro screenshot/transcript showing the draw summary (or the captured
  failure).
- `test_hand_end_dispatch.py` extended with a DRAW case.
- If cache-busting lands: a test asserting served HTML references the versioned
  asset URLs.

---

## Bug D — Concealed kong revealed to opponents (confirmed)

### Symptom (user)
"When a bot declared a concealed gang, it displayed the tiles openly."

### Root cause
A concealed kong's tile identity is private to its owner until settlement, but the
server leaks it on two surfaces:
1. **`project_event` doesn't redact it.** In
   [state.py](../../mahjong/engine/state.py), `project_event` only strips the
   private `tile` from an *opponent's* `DRAW`. A concealed-kong `CLAIM_DECISION`
   (`{decision:"GANG", kind:"CONCEALED", tile:"W4"}` — see `seq 182`) is passed
   through verbatim to every seat.
2. **The per-seat projection sends full meld tiles.** The opponent projection
   copies `melds` as-is, so a `GANG_CONCEALED` meld arrives at opponents with all
   four `W4` tiles. The client (`apply_event.js` GANG/CONCEALED branch and
   `render.js`) then draws them face-up.

### Fix
- **Server:** in `project_event`, redact the `tile` of a `CLAIM_DECISION` with
  `decision == "GANG" && kind == "CONCEALED"` for non-owner seats (mirror the
  DRAW rule). In the per-seat projection, mask `GANG_CONCEALED` meld tiles for
  non-owner seats (e.g. emit `{"type":"GANG_CONCEALED","concealed":true}` with no
  `tiles`, or four `null`s) — and reveal them only at `HAND_END.final_hands`
  (settlement legitimately reveals all hands — see the existing
  HAND_END-settlement-reveal precedent).
- **Client:** render an opponent `GANG_CONCEALED` as four face-down tiles
  (`render.js` already has `faceDown()`).

### Verification fixtures
- **Unit (projection):** concealed-kong `CLAIM_DECISION` projected for a non-owner
  seat has no `tile`; owner's projection keeps it.
- **Unit (projection):** opponent seat view shows a `GANG_CONCEALED` meld with no
  tile identity; `HAND_END.final_hands` still reveals it.
- **Render:** opponent concealed kong renders face-down; own renders face-up.

---

## Bug E — Feedback submits multiple times; no clear confirmation (confirmed)

### Symptom (user)
"When you click submit multiple times it submits multiple times. It should say
'feedback received', then close the window and clear the text." Confirmed: three
identical reports landed (`19:40:09`, `19:42:39`, `19:42:44`).

### Root cause
[feedback.js](../../mahjong/web/static/feedback.js) sets `_phase = "submitting"`
and disables the button on submit, and `app.js` does handle `FEEDBACK_ACK` →
`onResult(true)` → a "done" screen. But:
- There is **no idempotency guard** in `_onSubmit` itself — it dispatches the
  `feedback-submit` event whenever clicked while `_phase === "draft"`/`"error"`,
  and the disabled state only applies after Lit's async re-render, so the modal
  can emit more than one frame across slow ACK round-trips (and re-opening reuses
  the still-present draft).
- The success UX is a manual "Close" on a "Thank you" screen, **not** the
  auto-close-and-clear the user expects, so the modal lingers and invites
  re-clicks.

### Fix
- **Idempotency guard:** in `_onSubmit`, return immediately if
  `_phase !== "draft" && _phase !== "error"`; set `_phase = "submitting"`
  synchronously before dispatching (already done) and never dispatch twice for one
  draft.
- **On `FEEDBACK_ACK`:** show a brief "Feedback received" confirmation, then
  **auto-close the dialog and clear the draft** (`_text`, `_type` reset) after a
  short timeout (~1.2s) — no manual Close needed.
- Keep the inline error path for `feedback_error` (rate-limit / validation).

### Verification fixtures
- **Playwright (async):** double-click Submit rapidly → exactly **one** `FEEDBACK`
  frame on the wire; dialog auto-closes; reopening shows an empty textarea.
- **Unit:** `_onSubmit` while `_phase === "submitting"` is a no-op.

---

## Open questions

1. **Bug A storage:** `localStorage` (survives tab close, XSS-readable) vs
   `sessionStorage` (survives reload, cleared on tab close, smaller exposure
   window)? The user's stated need (refresh keeps you logged in) is met by either;
   `localStorage` is the more "stay logged in" behavior. *Recommend `localStorage`*
   unless you'd rather re-auth on every fresh tab.
2. **Bug C approach:** authoritative-`CLAIM_RESOLUTION` (recommended, correct but
   larger) vs server-suppresses-losing-decisions (smaller, keeps a fragile client
   assumption + a record/wire divergence). Pick before implementation.
3. **Bug B:** is the deployed server you tested on actually on `b7318da`+? If you
   tested an older build, B may already be fixed and the real fix is just
   cache-busting (which we should do regardless).
4. **Bug D meld masking shape:** `{type:"GANG_CONCEALED", concealed:true}` (no
   tiles) vs four `null` tiles — pick whichever the client renderer handles most
   cleanly without a codec special-case.

## Suggested landing order

C (correctness) and A (reachability) first — both confirmed, both user-blocking.
Then B's reproduce-first work (and cache-busting, which de-risks every other
client fix reaching the browser). D and E are low-risk polish.
