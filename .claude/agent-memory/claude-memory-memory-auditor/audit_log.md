---
name: audit-log
description: Running log of memory audit runs — dates, scope, finding counts, and which entries had issues.
metadata:
  type: reference
---

## 2026-06-05

Memory set scanned: same dir (~40 files via MEMORY.md index). Repo HEAD `4587cca` (Layer 9 step 1, v0 offense bot).

Finding counts: STALE: 0, CONTRADICT: 1, MERGE: 0, DATE_FIX: 0

CONTRADICT: `project_house_ruleset_conversion.md` line 22 still describes renchan as "currently hard-coded to always-rotate `(dealer+1)%4` in TWO duplicated spots: registry.py:605 and web/server.py:322" — but Phase 1 (2026-06-04) centralized it in `table/rotation.py::next_dealer`, called from `web/server.py:330` + `server/registry.py:635`. The line internally contradicts the same file's lines 26 + 30 STATUS. Suggested EDIT to past-tense LANDED note.

Verified clean: v0.py + V0Adapter, scoring.py (score_delta/lookup_x), mcr-house-3fan.json (fan_cliff:3, false_mahjong.enforced:false), rotation.py::next_dealer, `_resolve_claim_priority` (manager.py:448), `state.last_drawn.tile`, pymj MCR_FAN_CLIFF cliff enforcement, resolve_config, MANIFEST house hash.

Low-confidence (not flagged): same file line 30 says "3 calculate_fan call-sites" but there are now 4 engine sites + v0's — but it's a frozen "what shipped in Phase 1" record, v0 post-dates it. Don't re-flag.

Pattern reinforced: when a status block in a memory says "LANDED/centralized", scan the SAME FILE's older body paragraphs for stale present-tense ("currently hard-coded") prose that wasn't updated alongside the status header. The contradiction lives intra-file. Also: cited line numbers (registry.py:605 etc.) decay silently as files grow — registry.py is now 943 lines.

## 2026-05-25

Memory set scanned: `/Users/connorlockhart/.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/` (19 files, 24 entries via MEMORY.md index)

Finding counts: STALE: 2, CONTRADICT: 0, MERGE: 0, DATE_FIX: 0

Stale entries:
1. `project_layer8_roadmap.md` — frames themes 1–4 (multi-hand, multi-table, auth, persistence) as future work; all landed in steps 8.0–8.4 by 2026-05-25. Needs a status header.
2. `project_layer8_status.md` — resumption checklist says "3 deselected"; actual is "2 skipped (Linux-only) + 1 deselected (slow)". Minor label mismatch, count 597 is correct.

All other entries verified clean: 15 file paths, 12 function/class names, 3 count/version references, 4 behavioral patterns — all current.

Frequently moved paths to watch: none yet. Paths that moved in this codebase history: `WebOrchestrator` stayed in `mahjong/web/server.py`; `MultiTableOrchestrator` is NEW in `mahjong/server/orchestrator.py` (8.4). `TableHandle`/`TableRegistry` are NEW in `mahjong/server/registry.py` (8.4).

Pattern noted: project status memories (layer6, layer7, layer8) are written at completion and remain accurate. Roadmap memories (layer8_roadmap) are written before implementation and go stale fast as steps land — always cross-check against the corresponding status memory.
