# Handoff — Layer 9 kickoff (AI plan refresh) — 2026-06-04

This session was a **design brainstorm**, not implementation. It refreshed the AI roadmap and locked the rules/reward decisions that the first Layer 9 code depends on. No production code changed; the deliverables are doc + memory updates.

## Where we are

- **Layer 8 complete** (merged to main). README refreshed (PR #7, merged → `main` at `f1e7bb0`).
- This session updated **[docs/ai-plan.md](ai-plan.md)** with the Layer 9 roadmap and recorded the house ruleset to memory.
- Work is on branch **`docs/layer9-roadmap`** (off up-to-date `main`), **uncommitted** — review, then commit / PR.

## Decisions locked this session

1. **Configurable rulesets.** Two targets: `mcr-2006` (8-fan, official, Botzone) and a new `mcr-house-3fan` (3-fan, house). Fan floor + conversion live *in the ruleset config*.
2. **Fan floor goes in the observation** (not a fixed constant) so one conditioned policy can span both floors; conditioned-vs-specialist is deferred to when v2 runs.
3. **House fan→points conversion** = the RL reward. Zero-sum: discard win → winner `+4X`; self-draw → `+6X`. `X` by fan tier: 1→2, 2→4, 3→8, 4–6→16, 7–9→32, 10–15→64, 16–23→**80**, 24–43→160, 44–63→240, 64–87→360, 88→500. Full rationale in memory `project_house_ruleset_conversion.md`.
4. **False mahjong** (sub-floor `HU`): the *hand* ends; penalty ≈ half a limit hand, zero-sum to the other three.
5. **Dealer bonus = renchan** (deal again on win; no flat bonus). House "game" = 4 complete rotations in theory; in practice an open points ladder.
6. **Per-hand vs session split.** Conversion = per-hand reward (engine transition). Renchan / prevailing-wind / game-end = session structure (orchestration). The bot is a per-hand agent and is unaffected by session rules.
7. **Belief module: merge components 1 + 5** into one two-stage module (Stage A hard accounting → Stage B archetype reweighting), with reduction-to-naive as the seam test.
8. **Build order reordered** to a tracer bullet: scoring core (0) → MVP offense bot v0 (1) → belief Stage A (2) → forecaster/defense/Stage B/ukeire → rule-based v1.
9. **MVP debuts at 3-fan** — load-bearing: a simple fan-aware-but-shallow bot is competent at 3-fan, incompetent at 8-fan.
10. **Overlay gating:** hard-fact overlays (Stage A "tiles out") always on; inference overlays (forecaster, deal-in, Stage B) restricted to solo / "anything goes" table / post-game / admin debug.
11. **Paired eval with common random numbers** is required before crediting any component swap (mahjong is high-variance).
12. Fixed a doc inaccuracy: the character-flush heuristic in component 2 was inverted (keeping characters while shedding other suits is evidence *for* a flush, not against).

## Next session — in the order you asked for

### Phase 1: "the internals" + basic fixes (Build order step 0) — ✅ DONE (branch `feat/layer9-scoring-config`)

The **configurable scoring core**, spec'd as [Spec 26](specs/scoring-config.md) and implemented test-first.

**Premise correction (the kind to watch for):** this section originally said the engine "ignores `fan_cliff`" and pointed at `_find_min_winning_tile`. Both were wrong against the code — the 8-fan cliff *was* enforced (hard-coded `MCR_FAN_CLIFF = 8` in `pymj.calculate_fan`, gating both legality paths), and the function is `_pick_self_draw_win_tile`. So the work was *parameterization*, not new enforcement, and `mcr-2006` stayed byte-identical (its goldens never relied on a sub-8 win because one was impossible).

What landed:

- **Cliff from config.** `pymj.calculate_fan` reads `ruleset_config["fan_cliff"]` (default 8). The three call-sites (legality/discard, legality/claim, transition/hu) thread the *resolved* config via the new memoized `rulesets.resolve_config`.
- **Conversion from config.** New `mahjong/engine/scoring.py` (`score_delta`, `lookup_x`): `mcr-official` additive (default — reproduces the old `+8/+24`) vs. `house-table` tier-lookup. `hu.py` calls it; the old `_score_delta` is gone.
- **New ruleset** `mcr-house-3fan.json` (fan_cliff 3, house conversion, `dealer_repeat_on_win`, declared-but-unenforced `false_mahjong`) + frozen `MANIFEST` entry.
- **Renchan** centralized in `mahjong/table/rotation.py::next_dealer`, wired into both [registry.py](../mahjong/server/registry.py) and `web/server.py` (replacing the two hard-coded `(dealer+1)%4` spots).
- **Determinism:** `mcr-2006.json` untouched → all goldens green, no re-freeze. 974 tests pass; mypy clean (engine+table strict); new fixtures in `tests/engine/test_scoring.py`, `test_scoring_config.py`, `tests/table/test_rotation.py`.

**Deferred (confirmed this session):** false-mahjong *penalty* payout. It's unreachable through our legality + `apply_action` gate, so it would be dead code; it becomes a real training signal only against the Botzone judge. The ruleset declares the rule (`false_mahjong.enforced=false`) so the config is honest.

### Phase 2: MVP offense bot (v0) — after Phase 1

Fan-aware offense, uniform wall (Stage A only), no defense, k=1 myopic, debuts at 3-fan. Replaces the `CannedAdapter`-PASS placeholder for `kind: "bot"` seats ([registry.py:249](../mahjong/server/registry.py#L249)). Three instances fill a table → server becomes solo-playable. The open design question for that session: how "fan-aware" stays simple (shanten + a light fan-feasibility filter vs. a full self-applied archetype planner).

## Deferred / open (do NOT block Phase 1)

- Session-structure details: prevailing-wind advancement rule, exact game-end. (Low priority — bot doesn't need them.)
- False-mahjong exact penalty magnitude (≈ half a limit hand; pin at ruleset-authoring time).
- Conditioned model vs. specialists — decide when v2 runs.
- The 3-fan-vs-8-fan **paper** is a v2+ deliverable; build the hooks now, defer the study.

## Pointers

- Memory: `project_house_ruleset_conversion.md` (full conversion table, open items, engine status).
- Updated plan: [docs/ai-plan.md](ai-plan.md) — see new *Rulesets* section, two-stage Component 1, v0 in *Architectures*, reordered *Build order* + *Overlay gating*.
- This handoff supersedes the stale `SESSION_HANDOFF.md` (Layer 8, 2026-05-25).
