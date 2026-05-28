# Spec 21 — Layer 8 close-out: hand-display polish, bot pacing, remaining wiring

A consolidated spec for the work that takes Layer 8 from "browser-verified happy path" to "shippable to a real multi-human table." Bundles four user-driven UX corrections raised after the 2026-05-27 pinwheel browser pass with the still-deferred Layer 8 wiring items (8.8 lifecycle hardening, 8.11 late-join refusal, persistence + auth wiring close-out).

Sectioned by concern. Each section is independently implementable but is grouped here so the **full** remaining Layer 8 scope sits in one place — the previous follow-up specs ([cardinal-ui.md](cardinal-ui.md), [human-decide-timeout.md](human-decide-timeout.md), [late-join-replay.md](late-join-replay.md)) each handled a single item, and that turned out to leak smaller polish items into session memory instead of into the docs.

Tier-2 / Tier-3 mix — see per-section banner. Implementation order suggested at the bottom; pick step-by-step, not as one mega-PR.

## Context: what is and isn't already done

**Done (browser-verified 2026-05-27):** login, lobby, multi-table CREATE/JOIN, multi-human seat composition, pinwheel widget (visual layout + wind-number badges + arrow + large unicode discard tile).

**Done but unverified:** decide-timeout per-(seat_kind, prompt_kind) wiring (Spec 19) — code paths exist; no user has yet hit a real decide timeout.

**Not done — covered by this spec:**

1. **§1** Tile-display polish in the local player's hand: visible selection highlight; explicit just-drawn-tile separation; suit-grouped sort presentation.
2. **§2** Bot pacing — bots act on a randomised 5–10 s wall-clock delay rather than instantaneously, so a human at the table can read the play.
3. **§3** Pinwheel `?` arrow flicker — intermittently observed; spec records the suspected cause and a verification path.
4. **§4** Late-join refusal (8.11) — wire-protocol-side reference to existing [late-join-replay.md](late-join-replay.md) Alternative A, with the small bit of close-out work to land it.
5. **§5** Server lifecycle hardening (8.8) — graceful drain, signal handling, periodic tasks, startup integrity check.
6. **§6** Persistence + auth WS wiring — `reserve_hand` / `finalize_hand` hook points, `AUTH_REQUEST` / `RESUME` on the WS handler, real `admin_predicate`.

Each numbered section below pins goals, non-goals, interface, verification fixtures, and alternatives.

---

## §1 Hand-display polish (Tier 3 — renderer only)

The local player's concealed hand is rendered as a flat space-separated tile sequence ([mahjong/web/static/render.js](../../mahjong/web/static/render.js) `_renderOwnConcealed` calls `joinTiles(seat.concealed, " ", options)`). The engine already returns `concealed` sorted by `tile_sort_key` ([mahjong/engine/state.py:92](../../mahjong/engine/state.py)), so suit order is already correct at the data layer. Three rendering issues remain:

- **No selection highlight.** `<game-pane>` tracks `selectedTile` (the index into `concealed` the cursor sits on, set by digit keys or arrow keys at [app.js:424–440](../../mahjong/web/static/app.js)) but the renderer emits a single text run with no per-tile element, so there is nowhere to attach a highlight class. The player has no visual feedback for which tile they will discard on `Enter`.
- **Just-drawn tile is buried inside the sorted hand.** After DRAW the engine inserts the new tile in suit order and writes its identity to `state.last_drawn.tile` ([feedback memory: prefer-authoritative-state-over-derivation](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_prefer_authoritative_state_over_derivation.md)). Physically at the table you keep the just-drawn tile *outside* the rest of your hand until you decide whether to keep or discard it — the spec restores that affordance in the UI.
- **No visible suit boundary.** The hand is rendered as `m2 m3 m4 p1 p1 p5 s7 s8 s9 F1 J1`; suit transitions are visible only by reading the suit letter. A small extra space at suit boundaries makes the run readable at a glance, particularly when the hand is wide.

### Goals

- **Per-tile DOM elements in the local player's concealed hand.** Replace the single `joinTiles(...)` text run with a sequence of `<span class="tile">` elements so the renderer can attach `.selected`, `.just-drawn`, and suit classes per tile. Only the local seat is affected — opponent concealed counts are still rendered as a single string (privacy: opponents have `{count: N}`, not a tile list).
- **Selection highlight.** `selectedTile` (index into `concealed`) becomes a `.selected` class on the matching span. CSS gives `.selected` an `--accent` underline + bold weight (existing pattern from `.pick-btn.selected` at [app.js:662](../../mahjong/web/static/app.js)). Highlight is also legible in ASCII mode (no relying on glyph colour alone).
- **Just-drawn tile is offset.** When `view.last_drawn?.seat === ownSeat` and the matching tile is still in `concealed` (i.e., the player has not yet discarded), render that tile *after* a visible gap. Implementation: take the sorted `concealed` list, find the first index whose tile token matches `last_drawn.tile`, split the list at that index — render `[0..i-1]` joined normally, then `[i+1..]` joined normally, then a wider gap (`<span class="gap"></span>`, ~1.5em margin-left), then the matched tile span with `.just-drawn` class.
- **Suit-group separator.** Insert a half-width gap span between tiles whose `tile_suit_letter` (m / p / s / F / J) differs from the previous tile. The sort already groups suits; the gap just makes the boundary visible.

### Non-goals

- **Drag-to-rearrange.** Tiles stay in engine-sorted order. The user cannot manually re-sort.
- **Opponent tile selection.** Opponents have `concealed.count`, not a list — nothing to highlight.
- **Tile sort modes.** Single sort key (suit then rank, honours last). No "sort by frequency", no "show shanten-relevant groups". Out of scope.
- **Animation.** Selection highlight is static. The just-drawn gap appears the moment the EVENT arrives and disappears on the next event that mutates concealed (DISCARD, PENG, GANG). No fade-in / slide-in.

### Interface

[render.js](../../mahjong/web/static/render.js) gains a new helper:

```js
function renderOwnConcealedTiles(seat, view, ownSeat, options) {
  // seat.concealed is a sorted Tile[] for ownSeat (engine guarantees).
  // Returns a Lit fragment of <span class="tile ..."> elements with:
  //   - .selected when index === options.selectedTile
  //   - .just-drawn when tile token === view.last_drawn?.tile and seat === ownSeat
  //   - .suit-break on the first tile of a new suit (margin-left to widen the gap)
  // The just-drawn tile is rendered last (after a fixed-width gap span),
  // even though it is sorted into the middle of concealed.
}
```

CSS additions in `<game-pane>` shadow styles ([app.js:600-ish GamePane CSS block](../../mahjong/web/static/app.js)):

```css
.tile.selected {
  text-decoration: underline;
  text-decoration-color: var(--accent);
  text-decoration-thickness: 0.15em;
  font-weight: 600;
}
.tile.just-drawn {
  margin-left: 1.2em;  /* visible gap from rest of hand */
}
.tile.suit-break {
  margin-left: 0.5em;  /* half-gap between suits */
}
```

`renderOwnConcealedTiles` is consumed by `renderTable`'s own-seat branch only; opponent seats keep the existing string render. The `view` parameter (full `SeatView`, not just the seat) is needed for `last_drawn`.

### Worked example

Sort order: `m2 m3 m4 p1 p1 p5 s7 s8 s9 F1 J1`. Player just drew `p5` (it's already sorted into position 5). `selectedTile = 2` (m4).

Rendered (visualised — gaps shown as `·` for clarity):

```
m2 m3 m4_ p1 p1 ·s7 s8 s9 ·F1 ·J1   ·· p5
         ^^^^                              ^^
         underlined (selected)             just-drawn, offset
```

(`_` underneath `m4` is the underline; `·` is the half-gap at a suit boundary; `··` is the wider just-drawn gap; `p5` appears at the right.)

If the player then discards `m4` (Enter), `selectedTile` clears, `concealed` shrinks by one, `last_drawn` clears. Next render shows just the remaining 10 tiles, no gap, no highlight.

### Verification fixtures

[tests/web/test_hand_display.py](../../tests/web/test_hand_display.py) (new file, Playwright async, matching the [feedback-playwright-async-only](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_playwright_async_only.md) constraint):

1. **selection_highlight** — synthesize a SeatView with `concealed=[m2,m3,m4]`, set `selectedTile=1`, assert exactly one `.tile.selected` exists and its text is `m3`.
2. **selection_arrow_keys** — render with `selectedTile=null`, press `ArrowLeft`, assert `selectedTile` becomes `concealed.length - 1` and the rightmost `.tile` has `.selected`.
3. **just_drawn_offset** — `last_drawn={seat:0,tile:"p5"}`, `concealed=[m2,m3,p5,s7]`, ownSeat=0. Assert the rendered DOM is `<span>m2</span><span>m3</span><span class="suit-break">s7</span><span class="just-drawn">p5</span>` (the just-drawn tile is moved to the end and the s7 picks up `.suit-break`).
4. **just_drawn_only_for_own_seat** — same `last_drawn` but `ownSeat=1`. Opponent seat 0 is rendered as count-only; assert no `.just-drawn` span exists anywhere.
5. **suit_break_present** — `concealed=[m2,m3,p1,p2,s9,F1,J1]`. Assert `.suit-break` appears on p1, s9, F1, J1 and nowhere else.
6. **after_discard_no_just_drawn** — `last_drawn=null` (post-DISCARD), assert no `.just-drawn` span.
7. **selection_clears_on_prompt_change** — already covered by existing app.js test for the `selectedTile = null` reset at new PROMPT; extend to assert the DOM `.selected` class is also gone.

---

## §2 Bot pacing (Tier 2 — adapters + table manager)

Bot adapters today return their decisions as fast as their `act()` coroutine resolves. For `CannedAdapter` and `AutoPassAdapter` that's effectively zero; for `BotRunnerAdapter` it's whatever the subprocess takes (typically <1 s for stock random / shanten bots). A human at the table sees the bots play in a flash, then has to absorb three discards before their own turn — the table feels unfair and hard to follow.

This section adds a configurable wall-clock floor to bot decisions: a uniform-random delay sampled per prompt from `[bot_min_delay_s, bot_max_delay_s]`, defaulting to `[5.0, 10.0]`.

### Goals

- **Bots feel like people you're playing against, not instant decision machines.** A human at the table can read each bot's discard before the next one happens.
- **Pacing is a property of the *adapter wrapper*, not the underlying bot logic.** A pure `CannedAdapter` is still synchronous in tests; pacing is layered on by the same composition seam that picks which adapter a seat uses.
- **No effect on self-play / training / CI.** The harness must run at full speed; tests must run at full speed.
- **Pacing respects the decide-timeout budget.** The delay sample is clamped to `min(sample, budget - 0.5s)` — never enough delay that a bot would self-timeout on a tight per-prompt deadline.
- **No client-visible deadline drift.** The PROMPT's `deadline` field reflects the *human-visible* deadline; injected bot delay does not extend it (bots are the actors deciding, so their adapter just sleeps before responding — no wire change).

### Non-goals

- **Per-bot "thinking time" tuned to model strength.** A future per-bot personality config (`thinking_speed: "fast" | "normal" | "deliberate"`) is out of scope. v1 ships one global range.
- **Variable pacing inside a single decision.** No "pause longer before claiming HU than before passing." A single uniform sample per prompt.
- **Catch-up after disconnect.** When a human reconnects and the bots have been waiting on their seat-hold, bots do *not* burn extra delay to "make up" missed time. The next prompt to them gets a fresh sample.

### Interface

A small wrapper, [mahjong/adapters/paced.py](../../mahjong/adapters/paced.py) (new file):

```python
import asyncio
import random
from dataclasses import dataclass
from mahjong.adapters.base import SeatAdapter, Prompt, Action

@dataclass
class PacedAdapter(SeatAdapter):
    """Adapter that sleeps `uniform(min_s, max_s)` before delegating act()."""
    inner: SeatAdapter
    min_s: float
    max_s: float
    rng: random.Random  # injectable for determinism in tests

    @property
    def kind(self) -> str:
        return self.inner.kind  # "bot" or "canned" — pass through

    async def act(self, prompt: Prompt) -> Action:
        delay = self.rng.uniform(self.min_s, self.max_s)
        # Clamp to leave a safety margin under the prompt deadline.
        budget_s = max(0.0, prompt.deadline - prompt.issued_at - 0.5)
        delay = min(delay, budget_s)
        if delay > 0:
            await asyncio.sleep(delay)
        return await self.inner.act(prompt)

    # All other SeatAdapter methods pass through to self.inner.
```

[mahjong/server/config.py](../../mahjong/server/config.py) `ServerConfig` gains:

```python
bot_min_delay_s: float = 5.0
bot_max_delay_s: float = 10.0
bot_pacing_enabled: bool = True   # off in CI / self-play
```

Env-var bindings:

| Variable                    | Default | Notes                                                  |
| --------------------------- | ------- | ------------------------------------------------------ |
| `MAHJONG_BOT_MIN_DELAY_S`   | 5.0     | floor of the per-prompt uniform sample                 |
| `MAHJONG_BOT_MAX_DELAY_S`   | 10.0    | ceiling                                                |
| `MAHJONG_BOT_PACING`        | `1`     | `0` disables pacing entirely (tests, self-play, eval)  |

The self-play harness ([mahjong/selfplay/runner.py](../../mahjong/selfplay/runner.py)) passes `bot_pacing_enabled=False` directly when constructing adapters, not via env var — explicit > implicit. CI test suite sets `MAHJONG_BOT_PACING=0` in its env or, more commonly, never goes through `cli/serve.py` at all.

Composition point in [mahjong/server/registry.py](../../mahjong/server/registry.py) `TableHandle._build_adapters_for_hand`:

```python
def _build_adapters_for_hand(self, ...):
    adapters = [...existing per-seat construction...]
    if self._cfg.bot_pacing_enabled:
        for i, a in enumerate(adapters):
            if a.kind in ("bot", "canned"):
                adapters[i] = PacedAdapter(a, self._cfg.bot_min_delay_s,
                                          self._cfg.bot_max_delay_s,
                                          rng=random.Random())
    return adapters
```

Note: `kind == "human"` adapters are never paced (the wrapping is no-op for them, but the explicit filter makes it clear). `AutoPassAdapter` reports `kind="canned"` and IS paced — when a human seat strikes out and the seat switches to AutoPass, the bot-style pacing is what makes that takeover feel coherent rather than jarring.

### Worked example

Default config (5–10s). Bot at seat 1 receives DISCARD prompt at `t=0`, deadline `t=30s`. `PacedAdapter.act` samples `delay=7.3s`, clamps to `min(7.3, 29.5)=7.3`, sleeps 7.3s, then calls `inner.act()` which returns near-instantly. ACTION arrives at server at `t=7.3s`.

Tight-budget case: bot prompt with `deadline - issued_at = 4.0s`. Sample `delay=8.6s`, clamp to `min(8.6, 3.5)=3.5s`, sleep 3.5s. Decide still completes inside the deadline; bot does not self-timeout.

Disabled case (`bot_pacing_enabled=False`): `_build_adapters_for_hand` doesn't wrap at all. Bots respond immediately. Self-play harness throughput is unchanged.

### Verification fixtures

[tests/adapters/test_paced.py](../../tests/adapters/test_paced.py) (new file):

1. **delay_within_range** — wrap a synchronous `_InstantAdapter` with `PacedAdapter(min=0.05, max=0.10)`. Call `act()` 20 times under a monkey-patched `asyncio.sleep` that records its argument. Assert every recorded delay is in `[0.05, 0.10]`.
2. **delay_clamped_by_deadline** — `min=10.0, max=20.0`, prompt with `deadline - issued_at = 1.0s`. Assert the slept delay is `0.5s` (clamped to `1.0 - 0.5`).
3. **kind_passthrough** — wrap a `kind="bot"` adapter; assert `paced.kind == "bot"`. Wrap `kind="canned"` (e.g. `AutoPassAdapter`); assert `paced.kind == "canned"`.
4. **disabled_no_wrap** — call `TableHandle._build_adapters_for_hand` with a config whose `bot_pacing_enabled=False`. Assert no adapter in the returned list is a `PacedAdapter`.
5. **enabled_wraps_only_bots** — composition: `[HumanAdapter, BotRunnerAdapter, CannedAdapter, AutoPassAdapter]`. After wrap, assert index 0 is *not* `PacedAdapter` and indices 1–3 *are*.
6. **rng_seeded_deterministic** — pass a `random.Random(42)` into `PacedAdapter`; call act twice; assert the recorded delays match a fixed list. (Pins determinism for any future test that wants reproducible pacing.)
7. **selfplay_unaffected** — integration: run a 1-hand selfplay with default config, assert wall-clock total < 1.0s (the runner takes the disabled path explicitly).

### Alternatives considered

- **Server-side queue delay** instead of adapter sleep. Rejected: queuing on the manager side is invasive (touches `mgr.run_hand`'s prompt loop); the adapter wrapper is a one-file change with the same observable behaviour.
- **Per-bot personality config.** YAGNI. One global range is enough to make the table feel right; per-bot tuning is a Layer-9 nice-to-have.
- **Truncated normal distribution instead of uniform.** Uniform is fine and easier to reason about. We can revisit if play feels metronomic.

---

## §3 Pinwheel `?` flicker (Tier 3 — investigation)

User reported during the 2026-05-27 browser pass: the pinwheel arrow occasionally renders as `?` even when no `CLAIM_WINDOW` is active. The bug was not reproducible reliably, so it's logged here for the next eyes on the renderer rather than dispatched.

### Likely cause

[render.js](../../mahjong/web/static/render.js) `_pinwheelArrow` returns `?` when `view.phase === "CLAIM_WINDOW"`. The most likely culprit is a transient `phase` value during the brief window between:

1. A DISCARD event applied to local state but before the CLAIM_WINDOW EVENT is processed; or
2. The CLAIM_WINDOW resolving (everybody PASSed) and the next phase update arriving.

Either edge could leave `phase` momentarily at the previous-or-next value while `last_discard` flickers (set, cleared, re-set). If `phase === "CLAIM_WINDOW"` is read once during that microsecond, `?` shows.

A second possible cause: the local-store update is non-atomic — `dispatch({type:"event", event})` updates `phase` and `last_discard` independently. If a re-render fires between the two updates, the arrow can briefly mismatch the tile.

### Verification path (not implementation yet)

1. Add a temporary `console.debug` in `_pinwheelArrow` that logs `(phase, last_discard, current_actor)` every render, then play a 4-bot hand and grep the console log for `phase=CLAIM_WINDOW` rows that bracket the `?` flicker.
2. If confirmed transient — wrap the store dispatch in a batched update (Lit's `requestUpdate` is already debounced; check whether the issue is the dispatch shape rather than render scheduling).
3. Pin the fix with a Playwright test that injects an event sequence (`DISCARD → CLAIM_WINDOW → CLAIM_WINDOW_END`) at controlled timing and asserts the arrow glyph at each frame.

Low priority — user explicitly said "do not spend a large amount of time on it." Park here so it isn't lost.

---

## §4 Late-join refusal (Tier 2 — wire close-out)

The detailed spec already exists at [late-join-replay.md](late-join-replay.md). This section pins the v1 implementation choice (**Alternative A — refuse with `hand_in_progress`**) and lists the remaining close-out work.

### What lands

- [mahjong/server/registry.py](../../mahjong/server/registry.py) `TableHandle.attach`: one phase check. If `is_human_seat(seat)` and the table's current phase is not `WAITING_FOR_PLAYERS`, raise / return `WireError(code="hand_in_progress", message=...)`. Spectator attach unaffected. Same-user resume (HELD → LIVE) unaffected — that goes through `_sessions.attach` which has the bind-state check.
- [mahjong/protocol/wire.py](../../mahjong/protocol/wire.py) `ERROR.code`: add `"hand_in_progress"` to the registered code set.
- [mahjong/web/static/app.js](../../mahjong/web/static/app.js) `<lobby-pane>`: on `LIST_TABLES`, render Join button only on tables with `phase === "WAITING_FOR_PLAYERS"`; for `IN_PROGRESS` tables, render a disabled Spectate-only chip (spectator wiring already exists). Lobby polling cadence is unchanged.

### Verification fixtures

Already enumerated in [late-join-replay.md § Verification](late-join-replay.md). Re-stating the load-bearing two for this close-out:

- **attach_during_in_progress_returns_hand_in_progress** — table with 2H+2B, START_HAND issued, second human attaches mid-hand → receives ERROR `hand_in_progress`, no ATTACHED, no snapshot. Existing seat sessions are unaffected.
- **lobby_hides_join_on_in_progress** — Playwright: lobby pane shows table with `phase=IN_PROGRESS`, assert no `.join-btn`, assert spectate option present.

### Why not Alternative B for v1

See [late-join-replay.md § Recommendation](late-join-replay.md): record-replay timing, projection of record events into wire events, and ordering with the next live event each need their own spec. Refuse is the safe cut; replay-on-join is roadmap.

---

## §5 Server lifecycle hardening (8.8 — Tier 2)

The full spec is [server-lifecycle.md](server-lifecycle.md). This section pins the **subset of that spec that ships as "8.8"** so we have a finite list rather than the whole 41 KB doc as the close-out target. Items not in this list are explicitly Layer 9+.

### What ships in 8.8

1. **Graceful `SIGTERM` drain.** On signal: stop accepting new WS connections; mark all `IN_PROGRESS` tables as draining; let each in-flight `mgr.run_hand` finish (or hit decide-timeout); flush record FOOTERs; close the WS server. Bounded by `MAHJONG_SHUTDOWN_GRACE_S` (default 300s); after grace, terminate with `WireError` close frame `code=1012` "server_restart" to remaining connections.
2. **`/health` endpoint.** HTTP GET on the WS port returns `{"status":"ok","tables":N,"uptime_s":S}` when the server can accept new tables; `503` with `{"status":"draining"}` after SIGTERM. Used by systemd readiness probes and Tailscale health checks.
3. **Startup integrity check.** On boot: call `Persistence.integrity_check()` (already specced in [persistence-api.md § Integrity check](persistence-api.md)); if it returns errors, log them at WARN, do not refuse to start. Server runs in degraded mode but is up; ops can choose to take it down. Refusing to boot on a single corrupt record is worse than degraded mode for a single-operator home server.
4. **Periodic seat-hold sweep.** Already exists in [session-mux.md](session-mux.md) as the per-table timer; harden to log a structured event `seat_hold_expired{table_id, seat, account_id}` rather than just mutating state silently, so ops can see strikes vs. disconnects in the log.
5. **Structured logging baseline.** `python -m mahjong serve` logs in JSON-Lines to stderr with at minimum: `ts`, `level`, `event`, `table_id?`, `seat?`, `account_id?`. Log lines per server-lifecycle.md § Structured logging are normative.

### What is explicitly NOT in 8.8

- systemd unit file ([server-lifecycle.md § Deployment](server-lifecycle.md)) — Linux-deploy phase work, depends on actual deploy box.
- Log rotation — handled by systemd/journald on the target host; no server-side rotation.
- Crash recovery beyond integrity check — if the process dies mid-hand, the record's missing FOOTER tells the next boot "this hand was in flight"; the persistence-api spec already covers the fix-up path. No active resurrection of in-flight hands.
- `MAHJONG_LISTEN_ADDR` parsing — already shipped in 8.7.e.

### Verification fixtures

[tests/server/test_lifecycle.py](../../tests/server/test_lifecycle.py) (new file; runs on Linux only, marked `@pytest.mark.skipif(platform.system() != "Linux")` per the macOS-sandbox note in [project memory: hosting-target](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_hosting_target.md)):

1. **sigterm_drains_in_progress** — start server, create 1 table, start hand with 4 bot seats (paced off), send SIGTERM, assert the hand completes within grace and the FOOTER appears on disk.
2. **sigterm_rejects_new_connections** — after SIGTERM, attempt WS connect → close frame with code 1012 / reason "server_restart".
3. **health_ok_when_running** — GET /health → 200 with status=ok.
4. **health_draining_after_sigterm** — GET /health post-SIGTERM, pre-grace-expiry → 503 with status=draining.
5. **integrity_check_logs_but_starts** — drop a malformed record file in `var/mahjong/records/`, boot server, assert WARN log line + server accepts WS.
6. **seat_hold_logs_structured** — fixture: HELD seat times out → exactly one log line with `event=seat_hold_expired` and the expected `table_id` / `seat`.

### Alternatives considered

- **Refuse to boot on integrity errors.** Rejected: too fragile for a hobby-scale server. A single bad record stops the whole machine.
- **Drain by force-kicking after N seconds.** Rejected: 300s grace is enough for one hand to finish even with humans at the table. Force-kick is the fallback inside that bound, not the policy.

---

## §6 Persistence + auth WS wiring close-out (Tier 2)

**Status: largely already wired in 8.3 / 8.5 — discovered during §2 implementation 2026-05-27.** The remaining work is smaller than the spec originally framed. See "what's already done" below.

The Python-side `Persistence` (Spec 15) and `Auth` (Spec 14) modules exist and pass their unit tests, but the WS handler ([mahjong/server/ws.py](../../mahjong/server/ws.py) and friends) does not yet call them on the hot path. This is the bridge work.

### Already done (verified 2026-05-27)

1. **`reserve_hand` / `finalize_hand` in `TableHandle._run_hand_loop`** — wired in 8.3 (`bef3972`) and reused by the multi-table refactor in 8.4 (`2a7c587`). See [registry.py](../../mahjong/server/registry.py) `_reserve_hand_row` / `_finalize_hand_row`.
2. **`AUTH_REQUEST` / `RESUME` on the WS handler first frame** — wired in 8.5; see [orchestrator.py](../../mahjong/server/orchestrator.py) `_run_auth_phase` lines 268-300+, calls `handle_auth_request` / `handle_resume`. Both use `STATIC_INVALID_HASH` timing defence per [feedback memory: static-invalid-hash](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_static_invalid_hash.md).
3. **`admin_predicate` derives from authenticated account role** — `_is_admin` reads `auth['role']` from the DB-backed account record (cleaner than the env-var approach the spec originally proposed). Pre-auth fallback to `_default_admin_predicate` remains for tests that skip auth entirely.

### What remains

- **`abort_hand` cleanup on engine exception.** No `Persistence.abort_hand` method exists today; the spec hypothesised one but didn't pin its interface. Open question: does an aborted hand row become "FINALIZED with winner_seat=null" or stay "RESERVED" forever? Leaving deferred until the first crash-mid-hand failure mode appears in practice.
- **Account registration UI.** Out of scope as stated; CLI-only.

### Originally proposed but superseded

- **`MAHJONG_ADMIN_ACCOUNT_IDS` env var** — the existing role-from-DB approach is preferable and already in place. Skip the env var.

### What is NOT in §6

- **Account registration UI.** Accounts are created via a CLI tool (`python -m mahjong account create`) — already exists per Spec 14. The web UI does not register; it only authenticates pre-existing accounts.
- **Password reset / change.** Out of scope; do it via the CLI.
- **Bot-account auth path.** Already specced in [auth.md § Bot accounts](auth.md); not new work, just verify the existing path still works after the WS handler change.

### Verification fixtures

Most exist already as `tests/persistence/`, `tests/auth/`, etc. The new integration fixtures for §6:

1. **ws_auth_required_first_message** — open WS, send non-AUTH first frame → ERROR `auth_required`, close.
2. **ws_auth_succeeds** — open WS, send `AUTH_REQUEST{user,pass}` → `AUTH_RESPONSE{ok:true,token,account_id}`. Token validates on the next WS message.
3. **ws_auth_fails_timing_safe** — wrong password → AUTH_RESPONSE `ok:false`. Measure wall-clock duration; assert within 10% of the success path (timing-defence smoke).
4. **ws_resume_with_valid_token** — fresh WS, `RESUME{token}` → `AUTH_RESPONSE{ok:true}`. Identity binding present on subsequent messages.
5. **ws_resume_expired_token** — `RESUME` with expired token → `AUTH_RESPONSE{ok:false,reason:"session_expired"}`.
6. **reserve_hand_called_with_correct_args** — fixture: run a hand; assert `persistence.reserve_hand` was called once with `(table_id, hand_index=0, dealer_seat=0, seats=[...])`; `finalize_hand` called once on hand end.
7. **abort_hand_on_engine_exception** — inject a `mgr.run_hand` that raises; assert `persistence.abort_hand(hand_id, reason="engine_exception")` is the cleanup call.
8. **create_table_admin_only** — non-admin identity sends `CREATE_TABLE` → ERROR `not_authorized`. Admin identity → table created.
9. **close_table_admin_only** — same shape, for `CLOSE_TABLE`.

---

## Cross-cutting: implementation order

These items are independent enough to land in separate small PRs. Suggested order, by "smallest blast radius first":

1. **§1 hand-display polish** — renderer only, one file (`render.js`) + CSS, ~7 test fixtures. Half-day.
2. **§4 late-join refusal** — one phase check on the server + lobby tweak, 2 test fixtures. Half-day.
3. **§2 bot pacing** — new adapter class, composition wire, 7 test fixtures. One day. **Note:** ship with `MAHJONG_BOT_PACING=0` default for the first session so verification happens with pacing explicitly on, not as ambient behaviour.
4. **§3 pinwheel `?`** — investigation pass; defer fix until reproducible. Park.
5. **§6 persistence + auth WS wiring** — the bigger one. Touches `ws.py`, `registry.py`, `auth.py`. ~9 test fixtures. One to two days.
6. **§5 server lifecycle hardening** — Linux-only tests; pair with the first deployable target. One to two days.

**Gate to Layer 9:** §1, §2, §4, §6 complete + browser-verified. §3 + §5 can spill into Layer 9 without blocking the Layer 8 close.

## Open questions

- **§2 — should `AutoPassAdapter` be paced?** Spec says yes (`kind="canned"`). Counter-argument: when a human strikes out and the seat switches to auto-pass, you arguably want it to *feel* fast — the table shouldn't slow down because someone disconnected. Worth a one-paragraph debate on first browser verify.
- **§1 — selection highlight in ASCII mode.** Underline + bold may be hard to see under some browser default monospace fonts; spec assumes the existing `.tile` class gets monospace and the underline is legible. If not, fall back to bracketed `[m4]` notation around the selected tile.
- **§6 — admin model.** `MAHJONG_ADMIN_ACCOUNT_IDS` is a flat env list; a future "admin role in DB" is cleaner but adds a migration. For a single-operator home server, env list is fine — but flag if multi-operator becomes a real ask.
