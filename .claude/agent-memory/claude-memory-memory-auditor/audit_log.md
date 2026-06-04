---
name: audit-log
description: Running log of memory audit runs — dates, scope, finding counts, and which entries had issues.
metadata:
  type: reference
---

## 2026-05-25

Memory set scanned: `/Users/connorlockhart/.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/` (19 files, 24 entries via MEMORY.md index)

Finding counts: STALE: 2, CONTRADICT: 0, MERGE: 0, DATE_FIX: 0

Stale entries:
1. `project_layer8_roadmap.md` — frames themes 1–4 (multi-hand, multi-table, auth, persistence) as future work; all landed in steps 8.0–8.4 by 2026-05-25. Needs a status header.
2. `project_layer8_status.md` — resumption checklist says "3 deselected"; actual is "2 skipped (Linux-only) + 1 deselected (slow)". Minor label mismatch, count 597 is correct.

All other entries verified clean: 15 file paths, 12 function/class names, 3 count/version references, 4 behavioral patterns — all current.

Frequently moved paths to watch: none yet. Paths that moved in this codebase history: `WebOrchestrator` stayed in `mahjong/web/server.py`; `MultiTableOrchestrator` is NEW in `mahjong/server/orchestrator.py` (8.4). `TableHandle`/`TableRegistry` are NEW in `mahjong/server/registry.py` (8.4).

Pattern noted: project status memories (layer6, layer7, layer8) are written at completion and remain accurate. Roadmap memories (layer8_roadmap) are written before implementation and go stale fast as steps land — always cross-check against the corresponding status memory.

## 2026-06-04

Memory set scanned: same dir (35 files / 35 MEMORY.md entries). ~7 sessions since last consolidation (2026-06-01).

Finding counts: STALE: 3, CONTRADICT: 0, MERGE: 0, DATE_FIX: 0, REMOVE: 1

Stale entries (all "spec drafted/in-progress" status memories that flipped to "merged" while the file body lagged the index):

1. `project_layer8_status.md` — claimed "8.8 and 8.11 remain". 8.11 landed (registry.py `hand_already_started`, commit 6c08363); 8.8 partial — 8.8.a/c/d landed (health.py, periodic.py, PR #5); only 8.8.b/e/f remain (see docs/HANDOFF-ops-loose-ends.md §B2). Frontmatter description badly stale (still says 8.7 active).
2. `project_admin_console.md` — MEMORY.md index says "steps 1-5 landed; 6-12 remain" but file BODY already correct (all 12 landed, PR #2 f897a5f). Index line only.
3. `project_public_deployment.md` — index "Spec drafted, not started" + file line 84 "not pushed, no PR" both wrong; Spec 24 MERGED via PR #1 (e21496f). File body already documents all 6 steps done.

REMOVE: `project_s2_s3_prep_status.md` — self-marked historical/SUPERSEDED; all pinned decisions now realized in shipped code. Frozen spec-prep snapshot, re-derivable.

Verified clean: 15+ named functions/classes/paths all current (_resolve_claim_priority, last_drawn, event_callback, fanout_event split, STATIC_INVALID_HASH, WebOrchestrator, TableHandle, CannedAdapter, KNOWN_KINDS, START_HAND, REGISTER, /health). pyproject requires-python>=3.12, slow mark registered, fast suite 939 collected. Commits 2337042 / f8b4693 valid.

RECURRING PATTERN (now confirmed across two runs): the staleness is almost always at the MEMORY.md index line and the frontmatter `description`, NOT the file body. Authors append corrected status to the body during work but don't update the one-line hook. Future runs: diff the index hook + frontmatter description against the file body first — that's where the lag lives. Also: "Spec drafted/not started" and "branch local, not pushed" phrasings are high-decay; re-check against `git log --grep` for the merge PR every run.

New module paths this cycle (none moved, all NEW): mahjong/server/health.py + periodic.py (8.8), mahjong/persistence/invites.py + mahjong/server/ratelimit.py (Spec 24), mahjong/control/* (Spec 25). NB: no mahjong/server/logconfig.py exists (structured logging 8.8.e unimplemented).
