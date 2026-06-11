---
name: audit-log
description: Running log of memory audit runs — dates, scope, finding counts, and which entries had issues.
metadata:
  type: reference
---

## 2026-06-11

Memory set scanned: project memory dir (PRs #16–#20 merged, HEAD d0e2289). Focus: 2026-06-11 ruleset-default flip + stall watchdog.

Finding counts: STALE: 0, CONTRADICT: 1, MERGE: 0, DATE_FIX: 0 (+1 OK-but-dated)

Issues:

1. CONTRADICT — `project_fb13_freeze_investigation.md:30-31` still says "FB-10 (live tables run mcr-2006, not the house ruleset)". Wrong: `config.py:202` default is now `mcr-house-3fan`; FB-10 RESOLVED. Suggested EDIT (not applied — report-only run).
2. OK-but-dated — `project_live_play_bugfixes_spec29.md:10` "on a branch + PR" now merged (PR #13).

Verified clean (all file:line / function cites resolved): config.py default_ruleset + _default_data_dir, engine/scoring.py, table/rotation.py::next_dealer, mcr-house-3fan.json, pymj.py::_melds_to_pack (FB-09 offer/CHI logic) + MCR_FAN_CLIFF=8 still default, bots/v0.py + adapters/v0.py + seat_bots.py, manager.py::_guarded_step + hand_step_stalled [DEF-12], web/static/app.js FB-16 keydown guard. house_ruleset STATUS line already corrected by author. The two HU-legality feedback memories mention 8-fan only as illustrative examples, not live-reality claims — not flagged.

Pattern: project_house_ruleset_conversion's STATUS line was updated for the flip, but the cross-reference in fb13's body wasn't — when a load-bearing fact flips, grep ALL memories for the old assertion, not just the primary memory.

## 2026-05-25

Memory set scanned: `/Users/connorlockhart/.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/` (19 files, 24 entries via MEMORY.md index)

Finding counts: STALE: 2, CONTRADICT: 0, MERGE: 0, DATE_FIX: 0

Stale entries:
1. `project_layer8_roadmap.md` — frames themes 1–4 (multi-hand, multi-table, auth, persistence) as future work; all landed in steps 8.0–8.4 by 2026-05-25. Needs a status header.
2. `project_layer8_status.md` — resumption checklist says "3 deselected"; actual is "2 skipped (Linux-only) + 1 deselected (slow)". Minor label mismatch, count 597 is correct.

All other entries verified clean: 15 file paths, 12 function/class names, 3 count/version references, 4 behavioral patterns — all current.

Frequently moved paths to watch: none yet. Paths that moved in this codebase history: `WebOrchestrator` stayed in `mahjong/web/server.py`; `MultiTableOrchestrator` is NEW in `mahjong/server/orchestrator.py` (8.4). `TableHandle`/`TableRegistry` are NEW in `mahjong/server/registry.py` (8.4).

Pattern noted: project status memories (layer6, layer7, layer8) are written at completion and remain accurate. Roadmap memories (layer8_roadmap) are written before implementation and go stale fast as steps land — always cross-check against the corresponding status memory.
