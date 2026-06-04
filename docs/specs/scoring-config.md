# Spec 26 — Configurable scoring core (rulesets)

The first Layer-9 foundation step (AI-plan *Build order* step 0). It makes the **fan floor and the fan→points conversion** properties of the *ruleset config* rather than constants baked into the engine, and adds the house `mcr-house-3fan` ruleset. One scorer, parameterized, shared by both the live server and the eventual training reward — so there is no training/serving skew (the bug class where the reward you optimize and the score the server pays disagree).

**Status:** draft, pre-implementation. This spec is the design artifact; implementation is test-first per CLAUDE.md (core rule-engine change → TDD mandatory).

## Premise correction (verify-the-spec-premise)

The AI-plan and the Layer-9 handoff state that *"the engine ignores `fan_cliff` — `transition/hu.py` accepts any fan-bearing (≥1) win"* and reference a function `_find_min_winning_tile`. **Both are wrong against the code as merged at `ef1a455`:**

- The 8-fan cliff **is** enforced today — `MahjongGB`'s output is gated in [`pymj.calculate_fan`](../../mahjong/engine/pymj.py) by the module constant `MCR_FAN_CLIFF = 8`, which returns `[]` for any sub-8-fan hand.
- That `[]` is what makes HU *illegal* below the floor, in **both** legality paths: `_self_draw_hu_legal` ([legality/discard.py](../../mahjong/engine/legality/discard.py)) and `_claim_hu_legal` ([legality/claim.py](../../mahjong/engine/legality/claim.py)). A sub-floor hand is never even offered `HU`.
- The function is `_pick_self_draw_win_tile` ([transition/hu.py](../../mahjong/engine/transition/hu.py)), and it already depends on the cliff (`if fans:`). The existing test `test_hu_self_draw_transitions_to_terminal` asserts `fan_total >= 8`.

So the real task is **not "add enforcement"** — it is **"make the already-enforced cliff and the hard-coded conversion config-driven, and add the house ruleset."** Two consequences follow:

1. **`mcr-2006` behaviour does not change.** If its cliff stays 8 and its conversion stays the official formula, every existing determinism golden and the `MANIFEST` hash are untouched. The handoff's "re-freeze goldens" worry is moot — a sub-8-fan win was never reachable, so no golden can depend on one.
2. The change is a clean **parameterization of an existing single seam** (`pymj.calculate_fan` already owns the cliff; `hu._score_delta` already owns the conversion), not new logic scattered across the engine.

The AI-plan's *Engine status* paragraph and the handoff's Phase-1 bullets should be corrected to match.

## Goals

- **Fan floor is config-driven.** `calculate_fan` reads the cliff from the resolved ruleset config (`fan_cliff`), defaulting to 8 when absent. The floor remains *the* gate on HU legality — nothing about that changes except where the number comes from.
- **Conversion is config-driven.** The fan→score-delta computation reads a `conversion` block from the resolved ruleset config. Two schemes are specified: `mcr-official` (the current additive `fan+base` formula) and `house-table` (tier lookup → multiplier). Absent block ⇒ `mcr-official`, so `mcr-2006.json` stays byte-identical.
- **House ruleset ships.** A new `mcr-house-3fan.json` (cliff 3, `house-table` conversion, `dealer_repeat_on_win`, a declared-but-unenforced `false_mahjong` block) plus its `MANIFEST` entry with a frozen `config_hash`.
- **One scorer, two rulesets.** The live server's terminal/scoring transition and the future training reward call the *same* config-driven code. No second implementation.
- **Renchan is config-driven** (orchestration layer). Dealer-repeat-on-win is read from `dealer_repeat_on_win`; the next-dealer decision is centralized so the registry and web loops can't drift.
- **Determinism preserved.** `mcr-2006` hashes and goldens are unchanged; the new ruleset gets its own frozen hash; the resolved-config lookup added to the per-decision path is memoized (no behavior change, no state bloat).

## Non-goals

- **False-mahjong penalty payout — deferred.** Declaring `HU` below the floor is *structurally unreachable* in our engine: legality never offers it and `apply_action` rejects any action not in `legal_actions`. The −30-style penalty only matters when a bot we train submits to the **Botzone judge** (which, unlike our engine, lets the bot attempt an illegal `HU`). We declare a `false_mahjong` block in the house ruleset so the config is honest that the rule exists, but the engine does not enforce a penalty path. Build it when a bot can reach it (action-masking / Botzone training). *(Decision: deferred this session.)*
- **Session structure beyond renchan.** Prevailing-wind advancement and exact game-end stay out — they're orchestration the bot never models (a Botzone game is a single hand). Only `dealer_repeat_on_win` is in scope here because it's the one session rule that's a ruleset property and already has a stubbed seam.
- **Home-rule overlay merging.** determinism.md anticipates table-creation overlays merged onto a base ruleset. Not in scope; rulesets here are whole, named, frozen configs.
- **The 3-fan-vs-8-fan study.** A v2+ deliverable. This spec builds the hooks (config-driven floor + conversion, ruleset stamped per record); the paper is deferred.

## The config schema

Both fields are **top-level siblings** in the ruleset JSON (matching the existing top-level `fan_cliff` in `mcr-2006.json`, so that file is not restructured).

### `fan_cliff` (already present)

Integer. The minimum fan total for a legal win. Read by `calculate_fan`; defaults to `8` if absent.

### `conversion` (new, optional)

A tagged block. Absent ⇒ treated as `{"scheme": "mcr-official", ...defaults}`.

**Scheme `mcr-official`** — the additive formula the engine hard-codes today (`hu._score_delta`). Zero-sum by construction.

```json
"conversion": {
  "scheme": "mcr-official",
  "self_draw": { "base_each": 8 },
  "discard":   { "base_dealer_in": 24, "base_other": 8 }
}
```

- **Self-draw:** each of the three non-winners pays `fan_total + base_each` (8). Winner receives the sum.
- **Discard:** the dealer-in pays `fan_total + base_dealer_in` (24); each *other* non-winner pays a flat `base_other` (8, **not** `fan+8`). Winner receives the sum.

This is exactly the current `_score_delta`. The defaults (8 / 24 / 8) reproduce it, so `mcr-2006` needs no `conversion` block at all.

**Scheme `house-table`** — tier lookup → per-loser multiplier. Zero-sum: the winner's delta is *derived* as the negation of the losers' total, so it cannot drift out of balance.

```json
"conversion": {
  "scheme": "house-table",
  "tiers": [[1,2],[2,4],[3,8],[6,16],[9,32],[15,64],[23,80],[43,160],[63,240],[87,360],[88,500]],
  "self_draw": { "each_mult": 2 },
  "discard":   { "dealer_in_mult": 2, "other_mult": 1 }
}
```

- **Lookup `X`:** find the first `[max_fan, X]` tier with `fan_total <= max_fan`; for `fan_total` above the last tier, clamp to the last `X` (500). (MCR limit hands cap at 88, but totals can exceed it by stacking yaku — clamping is the safe rule.)
- **Discard:** dealer-in pays `dealer_in_mult · X` (2X), each other loser pays `other_mult · X` (X). Winner = +(2X + X + X) = **+4X**.
- **Self-draw:** each of the three losers pays `each_mult · X` (2X). Winner = **+6X**.
- Self-draw is therefore always 1.5× a discard win — the deliberate house lever rewarding concealed/tsumo play.

The tier upper-bounds `[1,2,3,6,9,15,23,43,63,87,88]` encode the AI-plan ranges `1, 2, 3, 4–6, 7–9, 10–15, 16–23, 24–43, 44–63, 64–87, 88`. The 1- and 2-fan tiers are unreachable under the 3-fan floor; they're kept as floor-independent reference (the same table could front an 8-fan or a 1-fan floor).

### `dealer_repeat_on_win` (new, optional)

Boolean, default `false`. When `true`, the orchestration layer keeps the same dealer for the next hand iff the dealer won the hand just completed (renchan); otherwise it rotates `(dealer+1)%4`. Read in `registry.py` / `web/server.py`, **not** in the engine transition (it's session structure).

### `false_mahjong` (new, declared, **unenforced**)

```json
"false_mahjong": { "enforced": false, "penalty_each": 240 }
```

Present so the ruleset is self-documenting, but the engine ignores it this phase (see Non-goals). `penalty_each` (≈ half a limit hand, zero-sum to the other three) is a placeholder pending the Botzone-training work that can actually reach a false mahjong.

## The two shipped rulesets

**`mcr-2006.json` — unchanged.** Keeps `"fan_cliff": 8`, no `conversion` block (engine defaults to `mcr-official`), no renchan. Its `MANIFEST` hash is preserved — this is load-bearing: `test_loaded_config_hash_matches_manifest` and `test_manifest_only_lists_known_rulesets` assert it, and existing records reference it.

**`mcr-house-3fan.json` — new:**

```json
{
  "id": "mcr-house-3fan",
  "version": 1,
  "description": "House MCR variant: 3-fan floor, convex fan->points conversion incentivizing high-scoring and concealed hands.",
  "fan_cliff": 3,
  "wall_size": 144,
  "hand_size": 13,
  "seats": 4,
  "flowers_replace_at_deal": true,
  "yaku_engine": "PyMahjongGB",
  "dealer_repeat_on_win": true,
  "conversion": {
    "scheme": "house-table",
    "tiers": [[1,2],[2,4],[3,8],[6,16],[9,32],[15,64],[23,80],[43,160],[63,240],[87,360],[88,500]],
    "self_draw": { "each_mult": 2 },
    "discard":   { "dealer_in_mult": 2, "other_mult": 1 }
  },
  "false_mahjong": { "enforced": false, "penalty_each": 240 }
}
```

Its `config_hash` is computed once via `canonical_hash` and frozen into `MANIFEST.json` alongside the unchanged `mcr-2006` entry.

## Threading the config in

`GameState` carries only a `RuleSetRef` (`id`/`version`/`config_hash`) — `initial_state` resolves the config at deal time and discards the dict. Legality and the HU transition need the resolved config:

- **Resolution is memoized.** `load_ruleset` (or a thin cached wrapper) caches by ruleset id; the on-disk config is immutable per process, so the file-read + `canonical_hash` happens once. The per-call `caller_hash` validation still runs (cheap). *Rationale:* legality runs per decision; an uncached file-read there is wasteful. Memoizing — rather than denormalizing the config onto every `GameState` — keeps state snapshots small and leaves the determinism-hash surface untouched.
- **Three `calculate_fan` call-sites** (`legality/discard.py`, `legality/claim.py`, `transition/hu.py`) stop passing `ruleset_config={}` and instead pass the resolved config (the part `calculate_fan` needs is `fan_cliff`).
- **`calculate_fan`** reads `fan_cliff = ruleset_config.get("fan_cliff", MCR_FAN_CLIFF)` instead of the bare constant. The constant stays as the default.
- **`hu._score_delta`** becomes `conversion`-driven: dispatch on `conversion.get("scheme", "mcr-official")` to the additive or table computation. Both build the winner delta by accumulating loser payments, preserving the existing zero-sum-by-construction shape.

## Renchan (orchestration)

A single shared helper centralizes the next-dealer decision so the registry and web loops can't drift (today they're identical copies of `(dealer+1)%4`):

```python
def next_dealer(current_dealer: int, terminal: Terminal, config: dict) -> int:
    """Renchan-aware. Repeat the dealer on a dealer win iff the ruleset says so."""
    if config.get("dealer_repeat_on_win") and terminal["kind"] == "HU" \
            and terminal["winner"] == current_dealer:
        return current_dealer
    return (current_dealer + 1) % 4
```

`registry.py:622` already has `final_state` in scope; `web/server.py:322` currently discards `run_hand`'s return and must capture it. Both call `next_dealer(...)` with the resolved config. An exhaustive `DRAW` is not a dealer win → rotation proceeds (a common house refinement is "draw repeats the dealer too," but that's a session-structure choice left out of scope; default is rotate-on-draw).

## Verification fixtures this spec implies

Test-first; each is a pinned `(input) → (expected)` contract.

1. **Cliff is config-driven (reduction test).** With `mcr-2006` resolved, `calculate_fan` on a 6-fan hand returns `[]` (cliff 8); the *same hand* under `mcr-house-3fan` (cliff 3) returns a non-empty fan list. One hand, two rulesets, opposite legality — pins that the floor comes from config, not the constant.
2. **`mcr-2006` conversion unchanged (golden).** `_score_delta` (or its replacement) under `mcr-2006` reproduces the current `+fan+8 / +fan+24 / +8` deltas for a worked self-draw and a worked discard. This is the regression guard that the default scheme == today's behaviour.
3. **House table lookup (table-driven).** `X(fan)` for boundary fans `{1,2,3,4,6,7,9,10,15,16,23,24,43,44,63,64,87,88, 120}` matches `{2,4,8,16,16,32,32,64,64,80,80,160,160,240,240,360,360,500, 500(clamped)}`. Pins every tier edge and the over-88 clamp.
4. **House discard payout (zero-sum).** A worked house discard win at a known fan: winner `+4X`, dealer-in `-2X`, each other `-X`; `sum == 0`.
5. **House self-draw payout (zero-sum).** A worked house self-draw at a known fan: winner `+6X`, each loser `-2X`; `sum == 0`; and self-draw delta == 1.5× the discard delta at the same `X`.
6. **MANIFEST integrity.** `canonical_hash(load_ruleset({"id":"mcr-house-3fan"})) == MANIFEST["mcr-house-3fan"]`, and the existing `mcr-2006` hash assertion still passes (proves `mcr-2006.json` was not touched).
7. **HU legality at 3-fan (integration).** A hand that totals 3–7 fan: `HU` absent from `legal_actions` under `mcr-2006`, present under `mcr-house-3fan`. End-to-end via `legal_actions`, not just `calculate_fan`.
8. **`next_dealer` renchan (table-driven).** `dealer_repeat_on_win=true`: dealer-win → same dealer; non-dealer win → rotate; draw → rotate. `dealer_repeat_on_win=false`/absent: always rotate. Pins both loops via the shared helper.
9. **Determinism: `mcr-2006` goldens unchanged.** The existing determinism/replay fixtures stay green with no fixture edits — the receipt that this change is `mcr-2006`-invisible.
10. **Memoization is behaviour-neutral.** Resolving the same ref twice returns equal configs and identical hashes (extends the existing `test_load_ruleset_is_idempotent_in_hash`); a cached vs. uncached resolve produces byte-identical engine traces on a seeded rollout.

## Alternatives considered

**Conversion as a single parameterized formula vs. tagged schemes.** Considered forcing both rulesets into one formula shape. Rejected: official is *additive* (`fan + base`) and house is *multiplicative-on-a-tier-lookup* — genuinely different functions, not different constants. A tagged `scheme` with a small dispatch is honest about that; a unified "formula" would be a leaky abstraction encoding two unrelated math shapes.

**Default scheme when `conversion` absent vs. making both rulesets explicit.** Considered adding an explicit `conversion: {scheme: "mcr-official", ...}` to `mcr-2006.json` for symmetry. Rejected: it changes `mcr-2006`'s `canonical_hash`, breaking the `MANIFEST` assertion and invalidating every existing record's `config_hash`, for zero behavioural gain. Defaulting keeps `mcr-2006` byte-identical — the determinism contract rewards *not* touching frozen configs. The asymmetry (official implicit, house explicit) is the lesser cost.

**Denormalize resolved config onto `GameState` vs. memoized lookup.** Considered storing the resolved config dict on every state so transitions read it directly. Rejected: it bloats every snapshot, enlarges the canonical-hash surface (determinism.md hashes the whole state), and duplicates data already keyed by `config_hash`. Memoizing `load_ruleset` gets the per-decision cost to ~zero without any of that.

**Winner delta stored vs. derived (house scheme).** Considered listing the winner's `+4X`/`+6X` explicitly in the config. Rejected: deriving the winner as `-sum(losers)` makes zero-sum a *structural* property — no config typo can produce a non-zero-sum payout. The loser multipliers are the only free parameters.

**Enforce false-mahjong now vs. defer.** Considered building the sub-floor-HU penalty path. Rejected this phase: it's unreachable through our legality + `apply_action` gate, so it would be dead code with a test that can only be exercised through a synthetic back-door. It becomes reachable — and testable for real — only at Botzone-training time. Declared-but-unenforced field keeps the config honest meanwhile (the same pattern `fan_cliff` itself was in).

## Open questions

- **Where the house false-mahjong penalty lands when built.** Likely a Botzone-adapter concern (the judge issues the −30; our reward function mirrors it for training) rather than an engine transition, since our engine never reaches the state. Pin when the bot that can declare illegal `HU` exists.
- **Draw-repeats-dealer.** Some house games repeat the dealer on an exhaustive draw, not just a win. Left out (default: rotate on draw). Add a `dealer_repeat_on_draw` boolean if the house rule actually includes it — confirm with the table.
- **Exact `false_mahjong.penalty_each`.** Placeholder 240 (≈ half a 500-`X` limit hand). Pin at the value the house actually plays when the penalty is implemented.
- **Conditioned-policy feature encoding.** The AI-plan wants `fan_cliff` (and conversion params) lifted into the observation vector so one policy can span both floors. That's an observation-encoder concern (a later build-order step), not this scorer — but the config shape here is what it will read. Flagged so the encoder consumes these fields rather than re-deriving them.
