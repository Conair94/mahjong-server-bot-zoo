# Signal Discoverer Agent Memory

## Run history

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
