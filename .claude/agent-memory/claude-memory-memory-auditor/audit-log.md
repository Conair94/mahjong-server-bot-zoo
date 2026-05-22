---
name: audit-log
description: Running log of memory audit runs for mahjong-server-bot-zoo project memories.
metadata:
  type: reference
---

## 2026-05-21 — Full audit of project memory set

Memory set scanned: `/Users/connorlockhart/.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/`

Files audited (9 entries):
- feedback_prefer_existing_standards.md
- user_learning_goals.md
- feedback_tdd_and_rl_verification.md
- feedback_design_doc_style.md
- project_layer2_claim_priority_deferred.md
- project_layer2_self_draw_win_tile.md
- project_layer3_botzone_export_shape.md
- project_layer5_status_and_s1_split.md
- feedback_pytest_asyncio_mode_quirk.md

Verification targets checked:
- `mahjong/table/manager.py::_resolve_claim_priority` — verified present (lines 291, 334)
- `mahjong/bots/{errors,manifest,sandbox,registry}.py` — all present
- `mahjong/bots/botzone_serializer.py::BotzoneCsmSerializer` — present (line 44)
- `mahjong/adapters/bot_runner.py` — present
- `mahjong/bots/sdk/` — present
- `bots/sample-botzone/` — git submodule at commit 5e818212 (matches memory claim `5e81821`)
- `bots/python-reference/bot.py` — present (+ random_bot.py also present, new since 5.3a)
- `GameState.last_drawn` field — present in `mahjong/engine/types.py` line 199
- `_pick_self_draw_win_tile` in `mahjong/engine/transition/hu.py` — present (lines 44, 89)
- `_discard_event` + `from_hand` in `mahjong/records/diff.py` — present (lines 68, 84)
- `SeatView` omits `last_drawn` — confirmed (types.py lines 207–225)
- `docs/specs/` directory — present with 8 spec files
- pytest-asyncio `asyncio_mode = "auto"` in pyproject.toml — confirmed (line 56)
- `tests/adapters/test_bot_runner.py` + `test_bot_runner_parser.py` — both present
- `tests/bots/test_botzone_serializer.py` — present

Finding counts: STALE: 1, CONTRADICT: 0, MERGE: 0, DATE_FIX: 0

Staleness detail:
- `project_layer5_status_and_s1_split.md` claims `bots/python-reference/bot.py` is "the in-tree Python rule-based bot" — true, but `bots/python-reference/random_bot.py` was added in the same commit window (ca9b5d5) and is not mentioned. Minor omission; not a contradiction.
- `mahjong/selfplay/` module (runner.py, seeds.py) and `mahjong/cli/selfplay.py` exist as untracked files per git status — no memory entry covers this new work yet. Not a staleness finding against existing entries; flag for future memory write when these land.

Overall: all 9 memory files are factually consistent with current codebase state. The one minor gap is the omission of `random_bot.py` from the Layer 5.3a description, and the absence of any memory about the emerging selfplay harness.
