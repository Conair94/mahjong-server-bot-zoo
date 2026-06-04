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

### Phase 1: "the internals" + basic fixes (Build order step 0)

The **configurable scoring core**. This is a core rule-engine change → **test-first** (CLAUDE.md), and "reward shape is a tested contract" → pin `(hand, ruleset) → (legal?, score_delta)` before anything consumes it.

Concrete tasks with current ground-truth pointers:

- **Enforce `fan_cliff`.** [`transition/hu.py`](../mahjong/engine/transition/hu.py) currently passes `ruleset_config={}` (hu.py:63, :120) and `_find_min_winning_tile` (hu.py:100) accepts any ≥1-fan decomposition. Thread the *resolved* ruleset config in; make legality require `fan_total ≥ fan_cliff`.
- **Conversion from config.** `_score_delta` (hu.py:127) hard-codes the official `+8 / +24` formula. Drive it from a `conversion` block in the ruleset.
- **New ruleset** `mahjong/engine/rulesets/mcr-house-3fan.json` (fan_cliff 3, conversion table, `false_mahjong`, `dealer_repeat_on_win`) + a `MANIFEST.json` entry with its `canonical_hash`.
- **False mahjong:** the HU-below-floor path ends the hand and applies the zero-sum penalty payout.
- **Renchan:** dealer rotation is hard-coded `(dealer+1)%4` in **two** spots — [registry.py:605](../mahjong/server/registry.py#L605) and `web/server.py:322`. Make it config-driven (dealer repeats on win); consider centralizing the next-dealer decision so the two loops don't drift.
- **Determinism:** enforcing `fan_cliff` on `mcr-2006` changes engine behavior → re-freeze determinism goldens *with justification*. First check whether any existing golden actually relies on a sub-8-fan win; if not, nothing changes.

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
