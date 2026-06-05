# Signal Discoverer Agent Memory

## Run history

### 2026-06-05 — Sessions bdf243da, b648c74f, 399034fe, 5122538c, 0b2add49 (+ older 8.8 ops sessions)
- Scanned 10 sessions (2026-06-03 to 2026-06-04), all in mahjong-server-bot-zoo. Focus: Layer 9 brainstorm + scoring core (Spec 26) + v0 bot (Spec 27).
- Candidate counts: UPDATE: 0, CONTRADICT: 0, FILL_GAP: 2, NOISE: many (most session content already captured in dedicated memories written during the sessions themselves).
- Accepted candidates (both written to project memory):
  - FILL_GAP: `feedback-mcr-fan-win-tile-maximization` — HU legality maxes fan over ALL win-tile choices; interior win tile earns a wait fan (+1). A "7-fan" hand spuriously cleared the 8-fan floor (Spec 26). Author floor-relative fixtures by probing max-over-win-tiles, prefer pung/closed hands. NOT in code comments.
  - FILL_GAP: `feedback-offense-bot-feasibility-probe-discard` — user correction: judge "keep this tenpai?" by ron-ability (win_type=DISCARD), not SELF_DRAW (self-draw fan / Fully-Concealed +4 makes the permissive probe lock the bot into self-draw-only shapes). Generalizes to all offense bots v0→v2+.
- Discarded as NOISE / already-captured:
  - House conversion table, 4X/6X payout, false-mahjong, renchan, per-hand/session split — all already in `project_house_ruleset_conversion.md` (written during session 399034fe).
  - v0 design (always-GANG rationale, fan-aware distance, DISCARD probe *what*), the 2 latent engine bugs (claim-HU HAND_END, replay window/terminal-aware) — all in `project_layer9_v0_offense_bot.md`.
  - "Verify spec premise" (scoring-core premise was inverted) — already `feedback_verify_spec_premise`; also re-noted in the house-ruleset status.
  - git-fetch-before-branch-reasoning (recurred again in 399034fe / 5122538c) — already captured.
  - character-flush heuristic inversion, Component 1+5 merge, conditioned-vs-specialist policy — these live in docs/ai-plan.md (source of truth), not memory-worthy.
  - Layer 8.8 ops sessions (0b2add49 etc.) — already consolidated 2026-06-04.
  - ukeire-at-tenpai counting bug, mypy/ruff baseline handling, key-presence canned_seat_actions semantics — derivable from code / one-off implementation detail.

### 2026-05-21 — Sessions 95f2c9b9, 7b9d8b26
- Scanned 2 sessions (2026-05-21), both in mahjong-server-bot-zoo.
- Candidate counts: UPDATE: 0, CONTRADICT: 0, FILL_GAP: 3, NOISE: 2
- Accepted candidates (all 3 written to project memory):
  - FILL_GAP: `project-hosting-target` — Linux deploy target + Tailscale architecture confirmed in session.
  - FILL_GAP: `project-layer6-status` — Layer 6.1a landed; 6.1b/6.1c deferred; b_random placeholder detail; seed derivation contract pinned.
  - FILL_GAP: `feedback-defer-parallel-until-needed` — user confirmed YAGNI on parallel infrastructure; serial-first validated in 6.1 split.
- Discarded as NOISE:
  - Auto-accept permission instructions (one-off session config question, no recurring pattern).
  - "Continue in this session vs. new session" heuristic (context about context windows, not project-specific).

---

### 2026-05-24 — Sessions 2268cf32, 210214aa, 539e5299, 4776ffa6
- Scanned 4 sessions (2026-05-22 to 2026-05-24), all in mahjong-server-bot-zoo.
- Candidate counts: UPDATE: 1, CONTRADICT: 0, FILL_GAP: 4, NOISE: 2
- Accepted candidates (all written to project memory):
  - UPDATE: `project-layer7-status` — Layer 7 / S2 complete (commit 2337042); all 4 S2 fixtures GREEN; 531 tests; stale "How to apply" section replaced with 7.6 architectural decisions.
  - FILL_GAP: `project-layer8-roadmap` — Layer 8 themes: multi-hand (Table/Hand split), multi-table, auth, score persistence, Linux deploy.
  - FILL_GAP: `feedback-event-callback-spectator-seam` — `event_callback` kwarg is the approved passive-observer seam; `fanout_event` vs `fanout_event_to_spectators` split is load-bearing.
  - FILL_GAP: `feedback-read-spec-before-framing-choice` — read spec for prior art before posing a binary implementation choice; spec often already pins the answer.
  - FILL_GAP: `feedback-playwright-async-only` — Playwright async API + pytest-asyncio only; `pytest-playwright` sync plugin creates a second event loop that silently breaks the wire/sessions suite.
- Discarded as NOISE:
  - Specific ASCII tile shorthand display conventions (derivable from reading the source code).
  - Specific fixture byte-identity diffs between F1/F2 records (per-incident, no reusable principle).

---

### 2026-05-25 — Session c7ffec89

- Scanned 1 session (2026-05-25), mahjong-server-bot-zoo, Layer 8 steps 8.1–8.4.
- Candidate counts: UPDATE: 0, CONTRADICT: 0, FILL_GAP: 5, NOISE: 2
- Accepted candidates (all written to project memory):
  - FILL_GAP: `project-layer8-status` — new status file (8.0–8.4 complete 2026-05-25, 597 tests; 8.5 server lifecycle and 8.6 S3 gate remain).
  - FILL_GAP: `project-multi-table-architecture` — 4 load-bearing Step 8.4 decisions: TableHandle duplication intentional, table_id type boundary, admin_predicate auth seam, Persistence hooks empty until 8.5.
  - FILL_GAP: `feedback-slow-pytest-mark` — @pytest.mark.slow for argon2 timing / multi-hand e2e; must register in pyproject.toml markers to avoid PytestUnknownMarkWarning.
  - FILL_GAP: `feedback-sync-db-run-in-executor` — persistence + auth functions are sync (sqlite3 not async-safe); async WS handlers call them via run_in_executor.
  - FILL_GAP: `feedback-static-invalid-hash` — STATIC_INVALID_HASH timing-attack defense in auth.py: every handle_auth_request failure path must run dummy argon2 verify; never short-circuit before it.
- Discarded as NOISE:
  - ruff RUF043 / re.compile raw-string rule (generic Python linter knowledge, not project-specific).
  - keyset pagination via started_at_ms subquery (derivable from reading hands.py; standard SQL technique).

---

### 2026-06-01 — Sessions eb49ee87, 35cc7618, ecfc219f, dea04ca9, b59ed431, b66e76a5, 16be265e
- Scanned 7 sessions (2026-05-25 to 2026-06-01), all in mahjong-server-bot-zoo.
- Candidate counts: UPDATE: 0, CONTRADICT: 0, FILL_GAP: 5, NOISE: 6
- Accepted candidates (all 5 written to project memory):
  - FILL_GAP: `feedback-wire-codec-known-kinds` — KNOWN_KINDS is a closed allow-list; every new wire kind needs both registration and a test_codec.py round-trip test. Surfaced twice (START_HAND in dea04ca9, CREATE_TABLE.options in 16be265e).
  - FILL_GAP: `feedback-verify-spec-premise` — verify spec's stated root cause and fix location against actual code before implementing; both §22.5 and §22.7 had wrong diagnoses (wrong layer). Spec corrected first, then fixed in actual culprit.
  - FILL_GAP: `feedback-css-tests-mounted-component` — computed-style assertions need real `<game-pane>` mounted (shadow DOM); bare div skips the stylesheet. Surfaced in §22.3/§22.4 test failures (16be265e).
  - FILL_GAP: `feedback-hand-end-settlement-reveal` — HAND_END.final_hands is a legitimate MCR settlement reveal, not a privacy leak; privacy scanner must scope to in-hand frames only (surfaced in 8.7.f e2e test, dea04ca9).
  - FILL_GAP: `feedback-spec-field-names-from-disk` — always read field names from spec file, not memory; LLM difficulty estimates anchor on task genre not execution paths; surfaced in Haiku 4.5 handoff study (eb49ee87/35cc7618).
- Discarded as NOISE:
  - Pinwheel wind-badge semantics (1=E/2=S/3=W/4=N) — derivable from cardinal-ui.md and render.js.
  - `color-mix(in srgb, ...)` vs `--accent-rgb` variable choice — one-off CSS implementation decision.
  - `_BudgetRecordingAdapter` timing test pattern — derivable from reading test_decide_timeouts.py.
  - `crashed` flag as the strike-path breadcrumb in wire records — derivable from reading the code.
  - Layer 8 status/browser-verify memory already current (updated by the 2026-06-01 session itself).
  - HAND_END `next_hand_seq` always null (known limitation noted in status file already).
