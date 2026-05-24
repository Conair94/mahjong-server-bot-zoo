# Session handoff — 2026-05-24 (end of 7.5c.iii)

Snapshot of Layer 7 implementation status. Read this to pick up where this session left off.

## Where we are

**Layer 7 is 8 of 9 effective sub-steps complete.** 7.5c.iii (PROMPT bar + ACTION round-trip + illegal-action banner) landed this session. The browser client now drives a real engine end-to-end: it renders the table, animates inbound events, presents a key-bound action prompt when seat 0 is on-turn, sends ACTIONs back, and shows a transient banner when the server rejects an action.

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
| **7.5c.iii** | **PROMPT bar + ACTION round-trip + illegal-action banner** | **THIS SESSION** | **Spec fixtures 7, 8, 9 GREEN via Playwright async API** |
| 7.5c.iv (optional) | Bilingual EN/ZH rendering | pending | Spec fixture 15 |
| 7.6 | End-to-end S2 fixture | pending | The S2 exit gate |

**Verification at end of session:** ruff clean · ruff-format clean · mypy clean (57 source files; unchanged from 7.5c.ii because the new JS files don't enter the mypy graph) · **520 tests pass repo-wide** (515 prior + 5 new Playwright web tests, 2 Linux-only skipped). End-to-end smoke check via Playwright against the real `mahjong.web.demo`: load page → table renders → PROMPT appears on seat 0's DISCARD → Enter discards just-drawn → 6 EVENTs animate through other seats' turns → next PROMPT.

## What this session built

### 7.5c.iii — PROMPT bar + ACTION round-trip + illegal-action banner

**New file [mahjong/web/static/prompt.js](../mahjong/web/static/prompt.js)** — pure functions, no DOM deps beyond Lit's `html` tag:

- `renderPromptBar(prompt)` — renders the bar. Non-PLAY actions get one button each; PLAY actions collapse to a single tile-picker hint to avoid 14 buttons in a DISCARD-phase hand.
- `actionForKey(eventCode, prompt, selectedTile, ownConcealed)` — resolves a keystroke to a legal action (or `null` if illegal-for-this-prompt; per spec line 243 illegal keystrokes are no-ops).
- `tileIndexForKeyCode(code)` — maps `Digit1..Digit0/Minus/Equal/BracketLeft/BracketRight` → slot 0..13.

**Locked key map (2026-05-24):**

- **PASS → Space**, **PENG → P**, **CHI → C**, **GANG (EXPOSED/CONCEALED) → G**, **BUGANG (GANG kind=ADDED) → B**, **HU → H**.
- **Tile selection** `1 2 3 4 5 6 7 8 9 0 - = [ ]` → concealed slots 0..13.
- **Arrow Left/Right** nudges `selectedTile` by one slot.
- **Enter** confirms PLAY for `selectedTile`; with no explicit selection, defaults to `concealed[-1]` — during DISCARD this is the just-drawn tile, which is the dominant single-keystroke case.
- The spec's "P = Pass/Peng disambiguated by phase" wording was ambiguous (both legal in CLAIM_WINDOW); resolved in conversation 2026-05-24 with Space=PASS, P=PENG so the on-screen labels are unambiguous.

**[mahjong/web/static/app.js](../mahjong/web/static/app.js)** changes:

- `<game-pane>` gains reactive state `currentPrompt`, `selectedTile`, `illegalBanner`; methods `setPrompt`, `clearPrompt`, `showIllegalBanner` (4s auto-dismiss).
- Renders the prompt bar (when `currentPrompt`) and illegal banner (when set) above the wire-log toggle.
- New window-level `keydown` listener with **early-return on `e.altKey || e.ctrlKey || e.metaKey`** so it doesn't fight the `<table-page>` / `<mahjong-app>` Alt-chord handlers.
- On a matched action, dispatches `action-submitted { prompt_id, action }` (bubbles + composed). `<mahjong-app>` listens and sends it as an `ACTION` wire frame.
- **Prompt is NOT optimistically cleared on submit.** Spec fixture 9 requires the prompt to remain rendered when the server replies `ERROR illegal_action`. A fresh inbound PROMPT replaces it; phase-transition clearing lives in 7.6.

**[mahjong/web/demo.py](../mahjong/web/demo.py)** rewrite:

- The handler now runs a real engine loop. Auto-plays seats 1–3 (first-legal-action policy). When `state.current_actor == own_seat` and `legal_actions(state, own_seat)` is non-empty, builds and sends a real PROMPT, then awaits matching ACTION via `_await_action_for_prompt` (filters by `prompt_id`).
- Calls `apply_action`. On `IllegalAction`, sends `ERROR { code: "illegal_action", message }` and loops back with the **same** `prompt_id` — matches the spec's "prompt stays open" expectation. On success, broadcasts events with `DEMO_INTER_EVENT_DELAY_S = 0.4s`.
- `prompt_id` derived as `f"p_{seat}_{turn_index}_{phase}"` to match `HumanAdapter`'s convention (so a real session-mux would round-trip the same id).

**New [tests/web/conftest.py](../tests/web/conftest.py) + [tests/web/test_prompt.py](../tests/web/test_prompt.py)** — first Playwright tests in the repo:

- `FakeWireServer` runs on the test's own asyncio loop (no thread), exposes `send(frame)` / `inbound` / `wait_for_inbound(predicate)`. The same shape the spec's `fake_wire_server` helper documents.
- `browser` / `browser_context` / `page` async fixtures backed by `playwright.async_api`. **Deliberately not `pytest-playwright`'s sync fixtures** — those install a separate asyncio loop that breaks every `pytestmark = pytest.mark.asyncio` test elsewhere in the suite (the wire tests start failing with "Cannot run the event loop while another loop is running"). Async API + pytest-asyncio share the same loop.
- 5 tests cover spec fixtures 7 (bar renders with correct keys), 8 (P → ACTION with right `prompt_id` and payload, plus Space → PASS and an Alt+C non-interference check), 9 (ERROR `illegal_action` shows banner without closing prompt).

**Latent bug fixed in [tests/bots/test_sandbox.py:117](../tests/bots/test_sandbox.py#L117):**

- `test_apply_sandbox_warns_on_macos` was calling `apply_sandbox(m)` on the live test process — which `setrlimit(RLIMIT_NPROC, (1, 1))`d the runner. Harmless until 7.5c.iii: Playwright's chromium launch forks, and forking with NPROC=1 returns `EAGAIN` ("BlockingIOError: Resource temporarily unavailable"). Save/restore doesn't help because hard limits are one-way.
- Fix: `monkeypatch.setattr(resource, "setrlimit", lambda *a, **kw: None)` for this test — it's only verifying the warning machinery, not the rlimit side effect.

## Pinned decisions reaffirmed or added this session

Numbered continuing from prior sessions in [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) — decisions 21–27 are new this session.

- **(21) Action key map locked (2026-05-24):** Space=PASS, P=PENG, C=CHI, G=GANG, B=BUGANG, H=HU. Tile keys `1-9 0 - = [ ]` for slots 0..13. Arrow keys nudge selection once a tile is selected. Enter = PLAY selected (default `concealed[-1]`). Spec line 243's "P=Pass/Peng" wording superseded.
- **(22) Prompt is NOT optimistically cleared on submit.** Fixture 9 requires it to remain rendered after `ERROR illegal_action`. A fresh PROMPT replaces it; phase-transition clearing deferred to 7.6 when a real TableManager is in the loop.
- **(23) Pure `prompt.js` module.** Keystroke-to-action mapping is its own file so it stays unit-testable and is the single source of truth (no per-component reimplementation when `<spectator-pane>` or future overlays render prompts).
- **(24) `<game-pane>` keydown early-returns on any modifier.** `Alt+anything` is reserved for pane / theme toggles; without this guard a bare `C` for Chi would also fire as Alt+C → chat-toggle, or vice versa.
- **(25) `prompt_id = f"p_{seat}_{turn_index}_{phase}"` in the demo** — matches `HumanAdapter._translate_prompt`'s derivation so a real session-mux can round-trip the same id when 7.6 lands.
- **(26) Playwright tests use the async API + pytest-asyncio**, NOT `pytest-playwright`'s sync fixtures. The sync fixtures install a foreign asyncio loop that conflicts with every existing async test (wire / sessions / adapters). Cost: a small amount of fixture boilerplate; benefit: no suite-wide breakage. `pytest-playwright` and `pytest-base-url` were uninstalled.
- **(27) FakeWireServer is in-loop, not threaded.** Earlier draft used a worker thread to bridge sync Playwright to the async server; the async-API switch made the thread unnecessary. Simpler lifecycle and no `run_coroutine_threadsafe` plumbing.

## Known limitations carried forward

- **HAND_END double-emit risk** (carried from 7.3). Will surface in 7.6 e2e. Likely fix: filter HAND_END out of `SeatSession.observe()`'s EVENT path and route to a dedicated `HAND_END` frame sender.
- **The 7.5c.iii demo handler still isn't a real table manager.** It drives `apply_action` + `diff_to_events` directly with an in-handler PROMPT/ACTION loop. The bridge to a real `TableManager` + `TableSessions` + `HumanAdapter` lands in 7.6.
- **Phase-transition prompt clearing.** Right now a stale prompt sticks around if the server progresses without sending a new PROMPT (e.g., the player's PASS resolves and another seat's claim wins — no new PROMPT for seat 0 follows until they're on-turn again). In practice the next inbound PROMPT replaces it, but visually it can lag. Probably fine until 7.6.
- **Opponent meld formation can't reconstruct which specific concealed tiles left.** Unchanged from 7.5c.ii.
- **Concealed tile sorting after DRAW.** Unchanged from 7.5c.ii.

## What remains for next session

- **7.5c.iv (optional) — bilingual EN/ZH rendering** (spec fixture 15). Pull user-visible strings through `t(key, locale)` against `mahjong/web/static/locales/{en,zh,bilingual}.json`. Theme/tile-style toggles get a sibling `Alt+L` locale toggle.
- **7.6 — End-to-end S2 fixture** (the S2 exit gate). Real `TableManager` + `TableSessions` + `HumanAdapter` behind WS; Playwright drives a browser instance through one seat while three `CannedAdapter`s play the others; record byte-identical to a checked-in fixture. Closes Layer 7 and S2.
- **Address HAND_END double-emit** before 7.6 e2e.

Then Layer 8 (sub-steps 8.1–8.6) is the full S3 surface — SQLite, auth, persistence, multi-table, server-lifecycle.

## Outstanding questions / decisions for the user

- **PLAY action UI inside the prompt bar.** Currently collapsed to one hint (`[1-= []] Play tile · [Enter] confirm`). Consider whether to render a horizontal tile-cursor highlight on the concealed row when a slot is selected — better visual feedback than the bar-only hint. Punted; raise in 7.5c.iv or 7.6.
- **Phase-transition prompt clearing** (see Known limitations). When real `TableManager` is in the loop in 7.6, we'll see whether stale prompts feel bad in practice.
- **Chat wire-protocol amendment.** Still required before `<chat-pane>` does anything real. Not blocking 7.5c.iv or 7.6.

## Resumption checklist for the next session

- [ ] Read this file.
- [ ] Read [project_layer7_status memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_layer7_status.md) and [project_client_vision_web_ascii memory](../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/project_client_vision_web_ascii.md).
- [ ] Verify `git log --oneline -5` shows the 7.5c.iii commit at HEAD.
- [ ] Run `.venv/bin/python -m pytest` and confirm 520 passing, 2 Linux-only skipped.
- [ ] Optional sanity: `.venv/bin/python -m mahjong.web.demo` → open `http://127.0.0.1:8400/` → press `Enter` to discard the just-drawn tile → watch the engine animate through other seats and prompt you for the next turn.
- [ ] Decide whether to do **7.5c.iv** (bilingual) before **7.6** (S2 exit fixture). The S2 gate is more load-bearing.
- [ ] Address the HAND_END double-emit limitation before 7.6 e2e.
- [ ] If you skip 7.5c.iv: open Layer 8 spec prep alongside 7.6 since they're conceptually adjacent.
