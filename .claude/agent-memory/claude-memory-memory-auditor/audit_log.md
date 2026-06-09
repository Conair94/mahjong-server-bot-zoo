---
name: audit-log
description: Running log of memory audit runs — dates, scope, finding counts, and which entries had issues.
metadata:
  type: reference
---

## 2026-06-09

Memory set scanned: project memory dir (33 entries via MEMORY.md index). Focus: FB-01..07 / Specs 29-33 ground-truth shift.

Finding counts: STALE: 0, CONTRADICT: 1, MERGE: 0, DATE_FIX: 0

Contradicted:
1. `project_public_deployment.md` (~line 87) — lists "seat re-attach after reconnect (currently returns to lobby, not the table/seat)" as a deferred follow-up. FB-03 / Spec 31 (commit 01c03ff "feat(reconnect): rejoin a held seat from the lobby") implemented exactly this. Deferral is resolved.

Verified clean: all paths in priority targets exist (mux.py, registry.py, seat_bots.py, scoring.py, rotation.py, bots/v0.py, adapters/v0.py V0Adapter). KNOWN_KINDS allow-list memory holds — GET_HISTORY/HISTORY/GET_REPLAY/REPLAY all registered (codec.py:57-60) with round-trip tests (test_codec.py:368-370). Two-phase-handler memory NOT contradicted: GET_HISTORY/GET_REPLAY are lobby/pre-ATTACH only (orchestrator.py:316-321, client sends from profile view), so absence from mux.py handle_inbound is correct. All 5 Spec 29 bugs + settings fix verified landed (apply_event.js CLAIM_RESOLUTION authoritative, app.js localStorage RESUME, wire/server.py ETag/no-cache, state.py GANG_CONCEALED redaction, FEEDBACK_ACK, reactive props). v0/seat_bots specifics all accurate.

Pattern noted: deferred-follow-up lists inside otherwise-merged project memories are a staleness hot-spot — each "deferred"/"remaining" bullet is a checkable claim that later specs (FB-xx) quietly close. Cross-check deferral bullets against subsequent feature commits.

## 2026-05-25

Memory set scanned: `/Users/connorlockhart/.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/` (19 files, 24 entries via MEMORY.md index)

Finding counts: STALE: 2, CONTRADICT: 0, MERGE: 0, DATE_FIX: 0

Stale entries:
1. `project_layer8_roadmap.md` — frames themes 1–4 (multi-hand, multi-table, auth, persistence) as future work; all landed in steps 8.0–8.4 by 2026-05-25. Needs a status header.
2. `project_layer8_status.md` — resumption checklist says "3 deselected"; actual is "2 skipped (Linux-only) + 1 deselected (slow)". Minor label mismatch, count 597 is correct.

All other entries verified clean: 15 file paths, 12 function/class names, 3 count/version references, 4 behavioral patterns — all current.

Frequently moved paths to watch: none yet. Paths that moved in this codebase history: `WebOrchestrator` stayed in `mahjong/web/server.py`; `MultiTableOrchestrator` is NEW in `mahjong/server/orchestrator.py` (8.4). `TableHandle`/`TableRegistry` are NEW in `mahjong/server/registry.py` (8.4).

Pattern noted: project status memories (layer6, layer7, layer8) are written at completion and remain accurate. Roadmap memories (layer8_roadmap) are written before implementation and go stale fast as steps land — always cross-check against the corresponding status memory.
