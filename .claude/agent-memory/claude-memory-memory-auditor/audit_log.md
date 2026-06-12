---
name: audit-log
description: Running log of memory audit runs — dates, scope, finding counts, and which entries had issues.
metadata:
  type: reference
---

## 2026-06-12

Memory set scanned: project memory dir, HEAD efd1965 (main). Focus: post-audit-sweep merges (PRs #26–#42), CI mypy handoff, FB-13 status.

Finding counts: STALE: 1, CONTRADICT: 0 (counted as NEEDS-UPDATE: 2), MERGE: 0, DATE_FIX: 0

Issues:

1. STALE — `project_seat_bot_picker.md:12,15` "only `v0` registered today". `seat_bots.py:54-67` now registers v0 AND v1 (v1 landed PR #26 merged). `DEFAULT_BOT_ID`=v0 sub-claim still holds.
2. NEEDS-UPDATE — `project_ci_mypy_handoff.md`: PR #42 now MERGED (efd1965) → stats landed, "must be merged" satisfied. Handoff doc path `docs/handoff-ci-mypy.md` does NOT exist on main — lives only on unmerged branch `docs/ci-mypy-handoff` (PR #43, still OPEN). Core claim "main CI red at mypy step" STILL TRUE (last 3 main runs = failure).
3. NEEDS-UPDATE — `project_feature_queue_2026_06.md`: PR #35 (Spec 40, in-game scoreboard) now MERGED; memory frames it as just-landed/in-flight. "Fan-on-board" still genuinely queued (no ledger row/code). DEF-19 confirmed real (backlog:87).

Verified clean: engine/scoring.py, table/rotation.py, mcr-house-3fan.json, config.py default_ruleset=mcr-house-3fan + _default_data_dir (~/.local/share/mahjong-server), bots/v0.py + bots/v1.py + bots/belief.py, adapters/v0.py::V0Adapter, table/match_score.py, manager.py:113 hand_step_stalled [DEF-12], FB-13 still in-progress (backlog:48), PRs #26/#27 merged.

Pattern: PR-status memories ("must be merged", "open PR") decay the moment the PR merges — every memory naming an open PR number is a NEEDS-UPDATE candidate each run. Also: handoff-doc path references should note WHICH branch the doc lives on if the PR is unmerged, else the path 404s on main.

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
