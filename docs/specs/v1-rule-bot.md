# Spec 35 — v1 rule-based bot (hard accounting + defense)

The next bot-zoo architecture (AI-plan *Architectures* § v1), designed for the
**`mcr-house-3fan` ruleset first**. v0 is a pure-offense greedy bot: it never
looks at an opponent and counts a wait as live even when every copy is in the
discard pond. v1 keeps v0's offense skeleton and adds the three cheapest
high-leverage upgrades from the AI plan:

1. **Belief Stage A — hard accounting** (component 1, Stage A): per tile type,
   how many copies are *not visible* (wall + opponents' concealed hands).
   Upgrades ukeire from type-counting to copies-remaining weighting and kills
   dead waits.
2. **Opponent threat heuristic** (component 2, lite): a per-opponent scalar
   threat estimate from exposed melds, game lateness, and flush commitment.
3. **Deal-in risk + push/fold** (component 3, heuristic): a per-discard danger
   score, used to fold when our hand is hopeless against a visible threat and
   to break near-ties defensively when pushing.

Plus a **lite slice of component 4**: at tenpai, candidate hands are ranked by
payout-weighted wait EV (live copies × house payout tier), not raw wait width —
the convex house table makes a 6-fan wait worth literally double a 3-fan wait.

**Status:** implemented this step. TDD mandatory (decision policy = RL-adjacent
core logic).

## Scope honesty: what this v1 is and isn't

The AI plan's full v1 is "components 1–4 wired together" including Stage B
archetype reweighting and a self-applied top-K archetype planner. This spec
ships a **deliberate subset** — the parts with the best win-rate-per-complexity
at the 3-fan floor:

| Plan component | This spec | Deferred (revive trigger) |
| --- | --- | --- |
| 1A hard accounting | ✅ `mahjong/bots/belief.py` | — |
| 1B Stage B reweighting | ❌ | after component-2 archetype *distributions* exist (build-order step 4) |
| 2 hand-shape forecaster | lite: scalar threat + hot-suit flag | full archetype distribution when the analyzer overlay is built (build-order step 3) |
| 3 deal-in risk | ✅ heuristic (no corpus calibration) | calibration when the MCR corpus pipeline lands (v2 work) |
| 4 payout-weighted ukeire | lite: tenpai-only wait EV, k=1 | top-K archetype pruning / deeper EV when 8-fan support is needed |

At the 3-fan floor this is the right cut: hands are short and natural shapes
clear the floor, so *not dealing in* and *not waiting on dead tiles* dominate
deliberate archetype targeting (which is an 8-fan necessity). The deferred
rows stay on the AI-plan build order (steps 3–6); no separate ledger entries —
the plan is the ledger for bot-zoo components.

## Goals

- **Pure-function policy**, same contract as v0: `decide_action(view,
  legal_actions, seat, prompt_kind) -> Action`. No I/O, no RNG, deterministic.
- **Strictly more informed offense.** Ukeire weighted by live copies
  (Stage A); tenpai waits weighted by live copies × payout tier; a tenpai whose
  every ron-feasible wait is exhausted (0 live copies) is treated as dead
  (distance `SUBFLOOR_TENPAI_DISTANCE`), so the bot reshapes instead of waiting
  forever.
- **Defense.** Per-discard danger vs. each threatening opponent; fold mode when
  our hand is hopeless against a visible threat; danger-aware tie-breaking when
  pushing.
- **HU stays unconditional; GANG becomes distance-gated.** v0's always-GANG
  can wreck its own hand (konging four tiles that were serving as run
  components destroys a tenpai; an open kong can push a concealed tenpai
  below the floor by killing the Concealed Hand fan). v1 takes a kong iff the
  post-kong distance is no worse than the best non-kong alternative — which
  in practice keeps nearly every kong (the fan + replacement draw) and
  refuses only the self-destructive ones. Claim (PENG/CHI) logic stays v0's
  strict-improvement rule, evaluated with v1's dead-wait-aware distance.
- **Website-selectable.** A `SEAT_BOTS` entry (`bot_id: "v1"`) — the create-table
  picker is data-driven from `HELLO.bots`, so the registry entry is the whole
  client wiring.
- **Verification artifact for "v1 > v0".** A paired self-play eval (common
  random numbers: the same walls played with and without v1) showing v1 ahead
  of v0 on win rate and on score/hand. *(Achieved — see § Result: +1.2 pp win
  rate, +4.7 pts/hand paired over 2,999 hands.)*

## Non-goals

- **Stage B wall reweighting, archetype distributions, corpus calibration,
  lookahead > 1, learned anything** — see the scope table.
- **8-fan competence.** v1 is designed and evaluated at the 3-fan house floor.
  It *runs* under `mcr-2006` (everything reads `fan_cliff`/conversion from
  config) but no claim is made about its strength there.
- **Defensive claim logic** (e.g. PENG to deny a tile). Claims remain offense-
  only.
- **Tuning beyond hand-set constants.** Constants are named and pinned by
  *ordering* tests, not literals, same pattern as v0's
  `SUBFLOOR_TENPAI_DISTANCE`.

## Component 1A: `mahjong/bots/belief.py`

The first standalone perception module (later it backs the "tiles out"
overlay, build-order step 2). Botzone-style stateless: recomputed from the
`SeatView` each decision, no incremental event tracking.

```python
def remaining_counts(view: SeatView, seat: int) -> dict[Tile, int]:
    """Per playable tile type, copies NOT visible to `seat`:
    4 - (own concealed + all seats' discards + all seats' exposed meld tiles).

    "Remaining" = wall + opponents' concealed hands. last_discard is already
    in the discarder's `discards` list (pinned by test).
    """
```

- **Opponent concealed kongs** (`GANG_CONCEALED` with masked tiles in the
  opponent projection) contribute 4 tiles whose identity we don't know. Stage A
  treats them as unseen — a small, documented overcount of the unseen pool.
  (The projection hides their identity on purpose; Spec 29 fixed the leak.)
- Counts are clamped at ≥ 0 defensively; a negative count would mean a
  projection bug, and an assert would crash a live table over an overlay-grade
  signal.

## The v1 policy: `mahjong/bots/v1.py`

Reuses v0's exported primitives (`fan_aware_distance`, the claim-application
helper pattern) — two bots is below the three-uses bar for extracting a shared
module, so v1 imports from `mahjong.bots.v0` directly.

### Decision order

```
1. HU if legal (always).
2. GANG if legal AND post-kong effective_distance <= best non-kong
   alternative (best discard on own turn; PASS baseline on a claim).
   Deterministic (tile, kind)-minimal pick among viable kongs.
3. CLAIM  → v0's strict-improvement rule, with v1's effective_distance.
4. DISCARD → push / careful-push / fold (below).
```

### Effective distance (dead-wait awareness)

```python
def effective_distance(concealed, melds, seat_wind, round_wind, config, remaining) -> float:
    d = fan_aware_distance(...)              # v0's metric
    if d == 0.0 and all(remaining[t] == 0 for t in ron_feasible_waits):
        return SUBFLOOR_TENPAI_DISTANCE      # structurally tenpai, can never win
    return d
```

A ron-feasible wait with `remaining == 0` is *provably* dead — all four copies
are in discards/melds/our hand, so no opponent can discard it and we can never
draw it. v0 would sit on that hand to the wall; v1 reshapes.

### Offense EV (the push-mode ranking signal)

For a candidate post-discard hand:

- **Non-tenpai:** `weighted_ukeire` = Σ over shanten-lowering tile types of
  `remaining[t]` (v0 counted types; v1 counts live copies — 8 live improvers
  beat 3, even across fewer types).
- **Tenpai:** `wait_ev` = Σ over ron-feasible waits of `remaining[t] ×
  win_value(fan_total(t), conversion)`, where `win_value` is
  `scoring.lookup_x` for the `house-table` scheme and `fan + 8` (the official
  per-loser additive payment) otherwise. This is the discrete-fan-integration
  idea at k=1: integrate over the actual waits and their actual payouts, not a
  mean fan.

The two live in different units; they are never compared against each other —
candidates are first partitioned by `effective_distance`, and within a distance
tie all candidates are on the same side of tenpai.

### Threat model (component 2, lite)

```python
@dataclass(frozen=True)
class Threat:
    level: float        # 0.0 .. 1.0
    hot_suit: str | None  # "W" | "B" | "T" — flush-committed suit

def opponent_threat(opp: SeatViewOpponent, view: SeatView) -> Threat
```

- `level = min(1.0, 0.3 * melds + lateness)` with `lateness` = 0.1 when the
  wall is below a third of total, 0.2 below a sixth. Exposed melds are the
  strongest public tenpai signal MCR has (no riichi declaration). *(Retuned
  from the draft's half/quarter thresholds: the first 400-hand eval showed
  "half the wall" arrives a few turns after the deal, making 2-meld opponents
  cross the fold line constantly — see Eval history.)*
- `hot_suit`: set when the opponent has ≥ 2 melds whose suited tiles all share
  one suit (honor melds don't break commitment) — the public half of the
  flush signal. Discard-pattern flush detection (which suit they *never* shed)
  is deferred to the full forecaster.
- **Known blind spot:** a fully concealed hand shows `level ≤ 0.2` no matter
  how close it is. Accepted for v1 — concealed tenpai is exactly what Stage B
  / the forecaster exist to estimate later.

### Deal-in danger (component 3, heuristic)

```python
def discard_danger(tile, view, seat, remaining, threats) -> float
```

Per opponent with `level > 0`, a base danger by tile class, scaled by evidence,
summed weighted by `level`:

| signal | effect | rationale |
| --- | --- | --- |
| tile class | middle (3–7) 0.8 > edge (2/8) 0.6 > terminal (1/9) 0.4 > honor 0.3 | middles sit in the most run windows |
| honor copies | honor danger × `min(remaining_after, 2) / 2` | an honor with all other copies visible cannot be a pair/pung wait — near-provably safe |
| run liveness | suited danger × 0 if *every* run window through the tile has an exhausted neighbor *and* `remaining_after == 0` (no pair/shanpon) | the "no-chance" idea: a tile no wait can include is safe regardless of class |
| hot suit | × 1.5 if tile is in the opponent's `hot_suit` | feeding a visible flush |
| their own discard | × 0.3 if the opponent already discarded this tile type | MCR has no furiten rule, so not provably safe — but they shed it, so they don't need it (empirical, the strongest practical safety signal) |

Constants are hand-set; tests pin **orderings** on fixtures (fresh middle tile
in a hot suit > plain terminal > thrice-visible honor ≈ 0), never literals.

### Push / careful-push / fold discard

```
best_d = min effective_distance over candidate discards

fold iff max(threat.level) >= FOLD_THREAT (0.8) and best_d >= FOLD_DISTANCE (2.0)
fold mode:  argmin danger; ties -> min effective_distance (keep shape for free);
            ties -> tile_sort_key.

careful push iff max(threat.level) >= CAUTION_THREAT (0.7), best_d >= 1.0
            (never break a live tenpai — we are racing too), every best_d
            candidate has danger > SAFE_DANGER (0.1), and a candidate with
            danger <= SAFE_DANGER exists at distance <= best_d +
            CAUTION_WINDOW (1.0): play the best-offense such safe candidate
            — pay a step for provable safety.

push mode:  among candidates tied at best_d:
            score = offense_ev / max(offense_ev over tied) - DEFENSE_WEIGHT * danger
            argmax score; ties -> tile_sort_key.        (DEFENSE_WEIGHT = 0.3)
```

- Fold = "this hand is >= 2 steps from winning and someone is visibly close"
  — stop paying for a hand we won't finish. This is the asymmetry the plan
  calls out: the discarder pays double under the house conversion
  (`dealer_in_mult: 2`), so deal-in avoidance is worth more than marginal
  offense. `FOLD_THREAT = 0.8` means a 3-meld opponent anytime, or a 2-meld
  opponent in the endgame — *never* a 2-meld opponent early. The draft's 0.6
  was measured (CRN eval r1) to gut offense for no deal-in gain.
- Careful push is the middle regime the draft lacked: full fold is rare and
  free tie-breaks are weak, so without it defense almost never paid anything
  for safety. The `SAFE_DANGER` bar means "near-provably safe" (exhausted
  honors, no-chance tiles) — a one-step delay is bought only with real safety,
  not with vibes.
- Push mode normalizes offense within the tied set to [0, 1] so the danger
  term is on a comparable scale regardless of whether the tie is in ukeire
  units or wait-EV units. With no visible threat (`level ≈ 0` ⇒ danger ≈ 0),
  push mode reduces to pure offense — early-game v1 ≈ v0 with better counting.
- Determinism: pure float arithmetic on a deterministic candidate order, final
  `tile_sort_key` tie-break — no RNG anywhere.

## Adapter, registry, website

- `mahjong/adapters/v1.py` — `V1Adapter`, byte-for-byte the v0 shell pattern;
  `identity = {"kind": "bot", "bot_id": "v1", "version": "1", "runtime":
  "in_process"}`.
- `mahjong/server/seat_bots.py` — register `"v1"` (label "v1 — offense +
  defense"). `HELLO.bots` and the create-table dropdown pick it up with zero
  client changes (verified by the existing picker test pattern). `"v0"` stays
  the default (first entry) until v1's eval artifact exists; flip-the-default
  is a one-line follow-up the eval result justifies.
- Bot pacing, decide-timeout `bot` row, and `build_bot_adapter` all key on
  `kind == "bot"` / registry membership — no changes.

## Eval protocol (the verification artifact)

`scripts/eval_inprocess.py` — a thin asyncio CLI over `SelfPlayRunner` +
`selfplay.eval.aggregate`, taking `--bots v1,v0,v0,v0 --hands N --seed S
--ruleset mcr-house-3fan --rotation round-robin`. It builds adapters via
`seat_bots.build_bot_adapter` — the same factory the live server uses, so the
evaluated bot is byte-identical to the website one.

- **Design: 1×v1 + 3×v0, round-robin seat rotation**, fixed master seeds.
  Round-robin removes seat (dealer) bias. For the final artifact the same
  seeds are replayed with **4×v0** and compared per hand, per seat
  (`scripts/paired_compare.py`) — true *common random numbers*: the focal
  seat sees the identical wall under both policies, so the shared-deal
  variance cancels and only the policy difference remains.
- The draft's pass criterion (win rate > 0.315 on one 400-hand seed) turned
  out to be miscalibrated against the real effect size — see Eval history.
  The achieved artifact is the paired comparison below.

### Eval history (what tuning actually did)

All runs `mcr-house-3fan`, CRN master seed `20260611`, 400 hands, v1+3×v0,
metrics are v1's per-bot aggregates:

| run | config | win rate | score/hand | deal-in | verdict |
| --- | --- | --- | --- | --- | --- |
| r1 | draft constants (`FOLD_THREAT 0.6`, lateness at half/quarter wall) | 0.233 | −1.82 | 0.180 | **fail** — folds on any 2-meld opponent, offense gutted |
| r2 | fold retuned (0.8, lateness third/sixth) | 0.250 | +0.82 | 0.182 | neutral wins, positive score |
| r3 | + careful push (0.7 / window 1.0 / safe 0.1) | 0.250 | +1.26 | 0.175 | kept |
| r4 | + equal-distance claims on better ukeire | 0.247 | −3.94 | 0.190 | **rejected**, reverted (pinned by test) |
| r5 | r3 + distance-gated GANG | 0.247 | +1.20 | 0.175 | kept on correctness grounds (≈ neutral aggregate) |

### Result (the verification artifact)

Final config (r5), seeds 101–106, 500 hands each: **2,999 paired hands**
(one hand lost to the DEF-16 missing-`HAND_END` engine bug, found by this
eval; see the Deferred ledger).

- **Unpaired:** v1 win rate **0.2574** vs v0 **0.2472**; score **+3.97** vs
  **−1.32** per hand; deal-in **0.166** vs **0.177**; avg fan when won
  **6.85** vs **6.42**.
- **Paired (CRN, v1-seat vs same seat under 4×v0):** win-rate delta
  **+1.20 pp** (z **+1.62**; discordant hands 265 won-only-by-v1 vs 229
  won-only-by-v0), score delta **+4.74/hand** (z **+2.17**).
- **Reading:** every metric points the same way. On the house reward contract
  (points/hand — the metric the AI plan names as what the bot optimizes) the
  improvement is significant at the 5% level; the win-rate edge alone is
  ~90% confidence. v1 is better; "decisively better" in *wins* awaits the
  forecaster/Stage B components (the deferred rows) — defense converts losses
  into smaller losses, which shows up in points, not wins.
- Git SHA, seeds, and these tables go in the PR description (post-mortem
  logging guardrail).

## Verification fixtures (test-first)

Belief (`tests/bots/test_belief.py`):

1. **Fresh-deal accounting.** A view where only our 13 tiles are visible →
   `remaining` = 4 − own copies for held types, 4 elsewhere; total = 144 −
   flowers − 13 ... pinned exactly per type.
2. **Each visibility source counts.** Discards (incl. `last_discard` already in
   the pond — pins the no-double-count contract), exposed melds (PENG/CHI/
   exposed kong), own concealed. One fixture per source with a pinned vector.
3. **Opponent concealed kong is unknown.** Masked `GANG_CONCEALED` meld → no
   per-type subtraction (documented overcount).
4. **Clamp.** A (synthetic, contradictory) view that would go negative clamps
   to 0.

Policy (`tests/bots/test_v1.py`):

5. **HU / GANG unconditional** (carried v0 contract, re-pinned for v1).
6. **Dead tenpai reshapes (load-bearing).** A tenpai whose only ron-feasible
   wait has all 4 copies visible → `effective_distance ==
   SUBFLOOR_TENPAI_DISTANCE`; the policy prefers a discard keeping a live
   tenpai over the dead one. v0, given the same position, keeps the dead wait
   (asserted, as the differential receipt).
7. **Weighted ukeire.** Two discards tie on distance; type-count ukeire ties
   them but live-copy weighting separates (copies of one improver are in the
   pond) → v1 plays the live one.
8. **Payout-weighted waits.** Two tenpai candidates, equal live copies; one
   wait pays a higher house tier → v1 takes the higher-EV wait (pins
   `lookup_x` integration, house config).
9. **Threat ordering.** 0-meld early opponent < 2-meld opponent < 3-meld
   late-game opponent (levels strictly increase); 2 same-suit melds set
   `hot_suit`, mixed-suit melds don't, honor meld doesn't break commitment.
10. **Danger ordering.** Against a threatening opponent: fresh middle tile in
    `hot_suit` > same tile outside hot suit > terminal > honor with all other
    copies visible (≈ 0); opponent's own discarded type is discounted.
11. **Fold engages.** Hopeless hand (best distance ≥ 2) + a 2-meld late
    opponent → the chosen discard is the safest tile, not the ukeire-max tile
    (and differs from v0's choice on the same view — the differential receipt).
12. **Fold disengages.** Same hand, no threatening opponent → v1 plays the
    offense-optimal tile (push mode ≈ offense).
13. **Claim logic carried.** Strictly-improving PENG taken, useless CHI
    refused, using effective_distance.
14. **Floor-conditioned.** The same position under `mcr-2006` vs
    `mcr-house-3fan` flips the feasibility verdict (config-driven, no
    constants).
15. **Determinism.** Same view + legal_actions → byte-identical action, and a
    seeded 4×v1 `run_hand` rollout reproduces its action trace across two runs
    (rolls up to the project determinism contract).

Added during implementation/tuning (same files):

- **Careful push engages** (`CAUTION_HAND`, no floaters: every fastest discard
  live-suited, exhausted-honor pair one step behind): v1 pays the step for
  safety at threat 0.7; v0 plays the fast tile. **Needs a real threat**: same
  view early (threat 0.6) → v1 pushes. **Never breaks live tenpai**: same
  threat against `TWO_TENPAI` → the tenpai-keeping discard stands.
- **Gated GANG**: harmless kong taken (matches v0); hand-wrecking kong refused
  (`WRECK_KONG_HAND` — v0 kongs its own tenpai away, v1 keeps the tenpai).
- **Equal-distance claim rejection pinned** (`CLAIM_EQUAL_HAND`): the measured
  r4 regression cannot silently return.

Adapter/registry (`tests/adapters/test_v1_adapter.py`, `tests/server/`):

16. **Protocol conformance + hand-to-terminal.** Four `V1Adapter`s drive
    `run_hand` to TERMINAL with a well-formed zero-sum record.
17. **Registry/HELLO.** `"v1"` in `SEAT_BOTS`, appears in
    `available_bots_wire()`, `build_bot_adapter("v1")` returns a `V1Adapter`,
    default bot unchanged (`"v0"` first).

## Alternatives considered

**Full plan-v1 (Stage B + archetype forecaster + top-K planner) vs this
subset.** The full stack is 3–4 specs of work and most of its payoff is at the
8-fan floor (archetype targeting) — at 3 fan, natural shapes clear the floor
and the dominant losses are deal-ins and dead waits. Shipping the subset gets
a measurable ladder rung now and leaves the deferred rows where the build
order already tracks them.

**Single combined score (offense − λ·danger over all candidates) vs
distance-partition + tie-break.** A single scalar lets defense override
distance — but distance units (shanten steps) and danger units are
incommensurable, and a bad λ silently cripples offense (the classic
reward-shaping misspecification failure). Partitioning preserves v0's proven
offense as the primary key; defense acts only where offense is indifferent
(push) or hopeless (fold). The two-regime design is also how human play is
taught (push/fold), which makes the bot's behavior reviewable.

**Fold = safest tile vs fold = stop claiming too.** Fold mode could also
refuse all claims; but claims are already strict-improvement-only and a
folding hand rarely sees an improving claim. Skipped for simplicity; the
deal-in eval would surface it if it matters.

**Equal-distance claiming (tried and rejected, CRN eval r4).** Claiming
PENG/CHI when distance stays equal but live-copy ukeire strictly improves
sounded like Stage A earning its keep at the claim seam — v0's spec had
explicitly deferred claim aggressiveness "until there's an opponent ladder to
measure against". Measured: **−5 pts/hand swing** (v1 avg score +1.26 → −3.94
on the same walls), deal-in rate up. Opening the hand costs concealment fan
and flexibility worth more than the speed bought. The strict-improvement rule
stands, and `test_equal_distance_claim_still_refused` pins the rejection so
the "obvious improvement" doesn't sneak back in.

**Always-GANG vs distance-gated GANG.** v0's always-GANG (a user-pinned house
heuristic) is kept for the overwhelmingly common case, but v1 gates it on
"post-kong distance no worse than the best alternative" after the eval work
exposed the self-destructive cases: konging four tiles that serve as run
components (destroys a tenpai — `test_gang_refused_when_it_wrecks_the_hand`
shows v0 doing exactly this), and open kongs that drop a concealed tenpai
below the floor. Aggregate effect measured ≈ neutral (the cases are rare);
the gate is kept on correctness grounds, not win-rate grounds.

**Genbutsu-style hard safety (× 0 for opponent-discarded tiles).** Riichi
intuition, but MCR has **no furiten rule** — a player *can* ron a tile they
discarded earlier, so 0 would encode a rule that doesn't exist. × 0.3 keeps it
the strongest soft signal without pretending it's a proof. (The only
*provable* safeties in MCR are exhaustion-based — the honor/no-chance rows.)

**Eval: 2×v1 + 2×v0 vs 1×v1 + 3×v0.** 2v2 doubles v1 samples per hand but
makes v1's opponents half-v1 — the question is "does v1 beat v0", so v1 should
face a v0 field. 1v3 with rotation is the clean A-vs-field design; the cost is
needing 400 hands for the margin, which is cheap in-process.

## Open questions

- **Constant tuning.** `FOLD_THREAT = 0.8`, `CAUTION_THREAT = 0.7`,
  `FOLD_DISTANCE = 2.0`, `DEFENSE_WEIGHT = 0.3`, `SAFE_DANGER = 0.1`, the
  danger table — pinned by ordering tests, already retuned once on eval data
  (see Eval history). Further tuning only on a measured failure.
- **Default bot flip.** Once the eval artifact lands, should `"v1"` become the
  default seat bot? One line in `seat_bots.py`; decide on the eval result +
  a human play-test (v1 folding might *feel* less fun to play against).
- **Concealed-hand blind spot.** Threat reads melds only. If review shows v1
  dealing into concealed hands the model never saw coming, that's the trigger
  for the forecaster (build-order step 3), not for more heuristics here.
- **Always-GANG under defense.** A kong feeds a robbed-kong win and reveals
  information; v0's stats run showed no harm, but v1's fold mode arguably
  shouldn't kong into a 3-meld opponent. Deferred until the eval's kong
  numbers say otherwise.
