# AI bot development plan

This document is the design plan for the AI components of the project. The [README](../README.md) is the public-facing overview; this is the working plan.

## Framing

Mahjong is an **imperfect-information game**: each player sees their own hand, all discards, all calls, and the round/seat state, but cannot see opponents' concealed hands or the order of tiles remaining in the wall. The standard framing for AI in such games is:

1. Maintain a **belief state** — a probability distribution over the things you can't see.
2. Pick the action with the best **expected value** under that belief.

Most of the components below either improve the belief state or improve the value estimate under it. Keeping that split clear makes the architecture composable.

A reference point: Microsoft's **Suphx** (Riichi mahjong, 2020) is the closest published state-of-the-art and most techniques transfer to MCR. Suphx itself is **not open source**; we are taking lessons from the paper, not the code. The Suphx idea v3 below commits to:

- **Global reward prediction** — a separate network learns to predict end-of-game score from mid-game state, so per-decision training has a denser signal than waiting until the hand ends.

Two further Suphx ideas (oracle guiding, run-time policy adaptation) are *not* committed in v3 and live in [research-ideas.md](research-ideas.md) with the triggers that would prompt us to try them.

## Platform constraints (Botzone)

The bot architecture has to live inside Botzone's execution model. Canonical reference: the [Botzone Chinese Standard Mahjong wiki](https://wiki.botzone.org.cn/index.php?title=Chinese-Standard-Mahjong/en) — protocol, token format, action grammar, time budget, and the 81-fan scoring table all live there. Treat it as the source of truth; flag any deviation when porting.

The relevant facts:

- **Bots are stateless.** Each interaction, the judge sends the *complete game history* and the bot must reconstruct state. There is no persistent in-process memory between turns. The belief-state components below are therefore recomputed from history each turn; design them to be cheap to rebuild, not to be incrementally maintained.
- **Time budget: ~1 second per interaction** (C++ reference; Python may get more — verify). This caps search depth for any MCTS-style component.
- **Action grammar.** Inputs are typed requests (`0` setup, `1` deal, `2` draw, `3` other-player action). Outputs are one of `PASS`, `PLAY`, `PENG`, `CHI`, `GANG`, `BUGANG`, `HU`. Action priority: Mahjong > Pung/Kong > Chow.
- **8-fan minimum to win.** Declaring `HU` under 8 fan triggers a -30 penalty. This is the cliff that forces nonlinear EV calculations in component 4.

**Convention to know:** the stateless-protocol design is common in competitive AI platforms because it sandboxes bots (kill and restart freely) and prevents bots from hoarding compute across turns. The cost is on us: every turn re-pays the cost of belief-state reconstruction.

## Existing libraries we depend on

Per the project's standards-first preference, we use the official Botzone/PKU ecosystem rather than rebuilding equivalents. This also avoids **training/serving skew** — the bug class where your training scorer and the judge's scorer disagree, producing a bot that's optimal for the wrong objective.

| Need | Library | Notes |
| --- | --- | --- |
| Fan calculator | [PyMahjongGB](https://github.com/ailab-pku/PyMahjongGB) | Python bindings over the official C++ `FanCalculator`. Same code the judge runs. |
| Shanten (regular + specialized) | PyMahjongGB | Includes Thirteen Orphans, Seven Pairs, Honors and Knitted Tiles, Knitted Straight variants. |
| Legal-move generation + local simulator | [botzone-mahjong-environment](https://github.com/ccr-cheng/botzone-mahjong-environment) | Python; replicates Botzone's game logic locally. |
| Training corpus | Botzone `mjdata.zip` (~530K human hands) | Preprocessor included with botzone-mahjong-environment. |
| Bot I/O reference | [sample-bot-Botzone](https://github.com/ailab-pku/Chinese-Standard-Mahjong) | Official C++ random-discard sample. Useful for protocol conformance. |
| Local judge | [Chinese-Standard-Mahjong/judge](https://github.com/ailab-pku/Chinese-Standard-Mahjong) | Same scorer Botzone uses. |

**Implication for language choice:** Python. The official Python bindings, official Python environment, and official preprocessor all exist. C++ would buy marginal inference speed at the cost of reimplementing the environment layer.

**One known gap:** legal-action generation exists only in `botzone-mahjong-environment` as static methods, not in PyMahjongGB. If that library proves unmaintained, we'd inherit responsibility for that piece. Worth a maintenance check before committing.

## Components

Each component is a standalone module with a clean input/output contract. Each is testable in isolation, reusable across bot architectures, and useful as an analysis overlay for human players. Build in the order listed — each step's output is the next step's input.

### 1. Tile-distribution tracker (belief state foundation)

**Input:** game history (all visible events: discards, calls, draws, dora, etc.) for the requesting player's perspective.
**Output:** for every tile type, a probability distribution over its location: in the wall, in opponent A's concealed hand, in B's, in C's. Marginals over these are what other components actually consume.

Starts from uniform over unseen tiles, updates on every observed event. This is essentially Bayesian filtering with a very simple state space; no learning required for v1.

**Why this first:** every downstream EV calculation depends on "what's the probability tile X is in the wall vs. an opponent's hand?" The naive answer (uniform over unseen tiles) is wrong as soon as opponents reveal information through their discards.

**Verification artifacts** (required before component 2 starts):

- Unit tests pinning posterior updates for each event type (discard, call, draw, dora reveal). Each test: given a small recorded prefix, the resulting marginals match a checked-in expected vector.
- Determinism test: a recorded full-game prefix produces a byte-reproducible marginals trace across runs.
- Sanity test: in the absence of any informative events, marginals stay uniform over unseen tiles.

### 2. Opponent hand-shape forecaster

**Input:** per opponent — their discards, calls, seat/round wind, and the tile-distribution tracker's current state.
**Output:** per opponent — a probability distribution over MCR hand archetypes (All Pungs, Mixed One Suit, Pure One Suit, Seven Pairs, Thirteen Orphans, Knitted Straight, Mixed Shifted Chows, Honors and Knitted Tiles, etc.), and per archetype the set of tiles that would complete or progress it.

V1: hand-crafted heuristics. A player who discards three honors early and no characters is probably not going for a character flush; a player who discards 1m and 9m early is signalling middle-tile shapes. Encode these as scoring rules.

V2: supervised classifier trained on the MCR database. Label = the archetype the opponent actually completed (or was closest to at hand end); features = the observable game state at each decision point.

**Convention to know:** training a model on the *outcome* of a decision rather than on labels of an *intermediate concept* is called **distant supervision**. It's how you can train an archetype classifier without anyone manually labeling archetypes — you let the final hand reveal what archetype was being pursued.

**Verification artifacts** (V1 heuristics):

- Fixture games where a specific archetype was visibly pursued (early honors discards → not character flush; 1m/9m discards → middle shapes). Each fixture: forecaster's top archetype matches the human-annotated ground truth.
- Calibration test: across the fixture set, predicted archetype probabilities are calibrated within an acceptable ECE (expected calibration error) bucket — overconfident hand-rules are worse than honest uncertainty.

**Verification artifacts** (V2 classifier):

- Held-out validation set: archetype accuracy and per-archetype recall on games not in the training corpus.
- Eval isolation: train/val/test split must be by *game ID*, not by decision-point. Splitting by decision point leaks the same game's later decisions into both sets.

### 3. Deal-in risk model (defense)

**Input:** your hand + (2) forecaster output + (1) distribution tracker.
**Output:** for each tile in your hand, P(discarding this tile deals into a win) and the expected fan you'd pay.

Defense is *underweighted* by beginner bots and beginner humans alike. A bot that never deals in but rarely wins still beats a bot that often wins but often deals in, because MCR's payout structure is asymmetric (the dealer-in pays the full hand value plus the other two contribute base).

**Verification artifacts:**

- Hand-traced fixtures: positions where the deal-in risk for a specific tile is high or low for non-obvious reasons. Model must agree with the hand-trace within tolerance.
- Calibration over the MCR corpus: across recorded games, when the model predicts P(deal-in) = p, the observed empirical rate should be ≈ p over a sufficient sample. Miscalibrated risk models silently corrupt EV calculations downstream.
- Asymmetry check: the model's loss must weight deal-ins by their actual MCR payout (full hand value, not base), not symmetric squared error. Test that the loss function is what the spec says.

### 4. EV-weighted shanten / payout-weighted ukeire (offense)

**Convention to know:** *ukeire* is the Japanese mahjong term for "the set of tiles that would improve your hand toward tenpai (one-away-from-winning)." Standard ukeire counts these tiles. **Payout-weighted ukeire** weights each by its expected value.

**Input:** your hand + (1) distribution tracker + (3) deal-in risk.
**Output:** for each legal discard, an estimate of the expected score from that discard onward.

This component is by far the hardest in the plan. The honest framing:

**The full problem is a POMDP.** The decision is "what tile to discard?" but the consequence is a walk through a stochastic graph: nodes are hand configurations, edges are tile swaps (discard + draw, or discard + call), goal nodes are winning hands paying out their fan value. You don't choose which tile you draw, so chance nodes punctuate every decision. Opponents act between your turns, so adversarial nodes punctuate every chance node. You can't see opponent hands or wall order, so the entire walk is over belief states, not raw states. The formal object is the **Bellman equation** for expected value over a Partially Observable Markov Decision Process — solving it exactly is intractable; the state space is in the hundreds of millions of distinct hands before you even consider melds and discards.

Every realistic implementation of (4) is an **approximation** of this object. The standard toolkit:

1. **Depth-limited expectimax.** Search k turns ahead, evaluate leaves with a heuristic (or a learned value function from (6)). k=1 reduces to standard ukeire counting and is what beginner bots do. k=2–3 captures real lookahead but eats into the 1-second budget fast (branching factor ≈ 34 possible draws × possible opponent actions).
2. **Candidate-archetype pruning.** Don't expand into every reachable hand — only the top-K archetypes you (an internal forecaster, applied to yourself) plausibly build toward. Orders-of-magnitude reduction in branching. Standard trick.
3. **Independent-draws assumption.** Treat each future draw as i.i.d. from the wall distribution. Wrong in principle (the wall is a permutation), small error in practice, huge simplification. Component (5) keeps the wall distribution itself accurate.
4. **Equivalence-class memoization.** Many hand states are equivalent under suit relabeling. Canonicalize before caching; the same expensive subcomputation becomes a hit.
5. **Discrete-fan integration.** **MCR scoring is highly nonlinear** — 8-fan minimum, individual yaku jump discontinuously. EV calculations that average over an *expected fan* scalar badly underestimate hands with a small probability of a big-fan combination. Integrate over the discrete distribution of final fan values, not a point estimate.

**v1 implementation explicitly uses:** k=1 lookahead (myopic), top-K archetype pruning from a self-applied forecaster, i.i.d. draws under (5), no learned value function. This is a strong baseline — much better than greedy — but it is *deliberately myopic*, and we should expect it to leave real money on the table. The fix isn't to deepen the search by hand: it's to train architectures v2/v3 (imitation, self-play) and v6 (value head), which absorb the deep lookahead into a learned function.

**Convention to know:** replacing explicit search with a learned `V(belief_state) → expected score` is **value function approximation**. It's the bridge between "the exact solution is intractable" and "we can still play well." AlphaZero's value head is the same idea; we are doing the mahjong version. The hand-rolled v1 EV calculator and the learned v6 value head are pointing at the *same* mathematical object — the optimal value function of the POMDP — by different routes.

**Verification artifacts:**

- Hand-traced "right answer" fixtures: a small set of positions where the correct discard is non-obvious but humans agree. v1 must match the right answer on a target fraction; future architectures (v2, v3) must do at least as well on the same fixture.
- Greedy-baseline regression: v1's win rate against a greedy ukeire-only bot must be above a fixed margin. This pins that the lookahead+forecaster+risk integration is actually adding value over the cheap baseline.
- Discrete-fan integration test: synthetic positions with a small probability of a big-fan completion. The EV calculation must reflect the discrete distribution, not the mean fan — verified by comparing computed EV to a brute-force-integrated reference on a tiny state space.
- Determinism: same seed + same opponents + same hand → same discard chosen. Refactors that change this hash are flagged.

### 5. Opponent-need-aware draw distribution

The compounding effect. The naive P(draw tile X next turn) treats all unseen tiles as uniformly in the wall. Once (2) says opponent A is heavily flushing bamboo, bamboos are *more* likely to be in A's hand than in the wall — so your P(draw bamboo) is *lower* than the naive count suggests.

**Input:** (1) and (2).
**Output:** P(this tile is in the wall) per tile type, which is what (4) should actually use in place of raw unseen counts.

This is highest-leverage because it's a multiplicative improvement on every other EV in the system.

**Verification artifacts:**

- Reduction-to-naive test: with forecaster (2) returning uniform (no information), the output of (5) reduces exactly to raw unseen-tile counting. Catches the case where the combiner accidentally adds bias even in the no-information limit.
- Synthetic flush fixture: a hand-constructed scenario where opponent A is heavily flushing one suit. P(tile of that suit is in wall) must decrease relative to the naive estimate; the magnitude must match a hand-traced expected adjustment within tolerance.
- Downstream impact test: re-run the component-4 v1 fixture set with (5) plugged in. EV estimates must change; "(5) plugged in but EV unchanged" means the wiring is broken.

### 6. Learned value head

**Input:** full observation (your hand, all visible state, score state, turn number).
**Output:** P(you win this hand), P(each opponent wins), expected score delta at hand end.

V1: supervised regression on the MCR database. V2: refined by self-play (see architectures below). Eventually replaces or augments the hand-rolled EV in (4) as a learned critic.

**Convention to know:** in RL terms, (4) is a *model-based* value estimator (we wrote down how mahjong works and integrated over outcomes); (6) is a *model-free* value estimator (we learned the value function from data, treating mahjong as a black box). Hybrid approaches that use both — model-based rollouts grounded by a learned critic — are how AlphaZero-family agents work.

**Verification artifacts:**

- Eval-isolated split: train/val/test on the MCR corpus split by *game ID*, never by decision-point. Reuse the same split as component 2's V2 classifier so cross-component leakage is impossible.
- Calibration check: predicted P(win) buckets must match observed empirical rates on the held-out set within ECE tolerance.
- Sign-of-improvement test against component 4: on a fixture of positions, using (6) as a leaf evaluator inside (4) must reduce EV-estimate error vs the hand-rolled heuristic leaf. If it doesn't, the value head isn't useful as a critic — fix or scrap before moving to v3.
- Determinism: same data + same seed + same code → byte-identical checkpoint hash. A refactor that changes the hash without a behavior justification is flagged.

## Verification

Per-component verification artifacts are co-located with each component above. This section covers the architecture-level gates that span components.

The working agreement is in [../CLAUDE.md](../CLAUDE.md). RL-specific guardrails from that doc:

1. **Sanity baselines before scaling.** Before claiming any learning algorithm works, demonstrate (a) random-vs-random produces ~uniform win rates, (b) self-play converges on a trivial sub-game (e.g., chow-only ruleset, no defense), (c) rule-based v1 beats random by the expected margin. If these don't hold, the bug is in the environment or the eval harness, not the agent.
2. **Reward shape is a tested contract.** Every reward function (raw fan, Suphx-style global reward prediction, any shaping) is pinned by tests mapping example trajectories to expected return *before* a training run uses it. Reward bugs are the most common silent RL failure; a tested contract converts a silent bug into a loud one.
3. **Determinism is non-negotiable.** Seeded rollouts of the engine + agent stack must be byte-reproducible. Component-level determinism tests roll up to a per-architecture determinism test (`v1 fixture game with seed S → recorded action sequence`). A refactor that changes the hash either changed behavior (investigate) or invalidates the fixture (justify and update).
4. **Eval is separate code from train.** Evaluation must not share mutable state, RNG, normalization stats, or replay buffers with the training loop. Static check: import graphs from `eval/` must not pull from `train/runtime_state`. Cross-contamination invalidates the whole experiment.
5. **Held-out opponents are mandatory.** Every architecture is evaluated against opponents it did not train against (prior-year Botzone entries are cheap-and-good). A v3 self-play agent that beats itself but loses to a Botzone 2025 entry is a v3 self-play agent that has overfit to a tiny opponent distribution.
6. **Per-architecture gate.** Each `vN` must beat `vN-1` in head-to-head play on the held-out opponent set by a margin larger than seed variance, *and* must not regress on the per-component fixture sets above. A regression on a component fixture during architecture work means the component was silently broken; fix before continuing.
7. **Log enough to post-mortem a bad run.** At minimum: seed, config hash, git SHA, eval results per checkpoint, opponent set used. "I think it was a few days ago" is not debuggable.

## Architectures

Each architecture is shippable; build the next only after the previous is working and beats its predecessor in head-to-head play on the held-out opponent set (see Verification above).

### v1: rule-based

Components 1–5 wired together with hand-tuned weights. No learning. Should decisively beat random and greedy baselines. Useful as:
- The first occupant of the always-on spectator table.
- A strong sparring partner for learned bots.
- A baseline to detect regressions in the engine itself (its win rate vs. a fixed opponent should be stable across engine versions).

### v2: imitation-learned

Supervised policy network trained on the MCR database to predict the expert discard given the observation. Cheap to train. Beats v1 if the training data has strong play.

**Convention to know:** training a policy by mimicking expert decisions is **behavior cloning** — the simplest form of imitation learning. Its known failure mode is **distributional shift**: the bot performs well in states the experts visited, badly in states they didn't, because errors compound. Default mitigation in this plan is moving to self-play (v3). If distributional shift bites *within* v2 (late-game collapses against rule-based v1), DAgger is the parked fallback — see [research-ideas.md](research-ideas.md).

### v3: self-play RL

Initialize from v2, refine via self-play with the learned value head from (6). Suphx's recipe.

**Conventions to know:**
- **Self-play** — the bot's opponents are copies of itself; the training distribution evolves with the bot.
- **Policy gradient** family (PPO, etc.) — the dominant on-policy RL algorithms.
- **Replay buffer + off-policy** — store past games and learn from them repeatedly; more sample-efficient when the simulator is expensive. Mahjong games are cheap to simulate so this matters less here than in robotics.
- **Reward shaping** — using intermediate signals (Suphx's global reward prediction) instead of only end-of-game score. Lower variance, faster training, but risks teaching the wrong objective if the shaping is misspecified.

### v4: MCTS at decision time (offline analysis)

Use the v3 policy as a prior and the v6 value head as a leaf evaluator. **The 1-second-per-interaction Botzone budget makes full AlphaZero-style search impractical at submission time** — expect hundreds, not thousands, of rollouts on a wide imperfect-information tree. v4 is scoped *primarily* to offline analysis, the explainer overlay, and post-game review. Live-play deployment is out of scope for v1 of v4.

**Convention to know:** **MCTS** (Monte Carlo Tree Search) guided by a learned policy + value network is the AlphaZero pattern. For imperfect-information games, vanilla MCTS doesn't directly apply; the imperfect-info-correct variant is **IS-MCTS** (Information Set MCTS), parked in [research-ideas.md](research-ideas.md) — if v4 is built at all, IS-MCTS is the form it takes.

## Training data and evaluation

**Data sources:**

- Botzone `mjdata.zip` — ~530K human hands (12,140 draws, 132,994 self-drawn wins, 385,324 discard wins). The `botzone-mahjong-environment` repo ships a preprocessor (`preprocess.py`) that converts this into per-decision training examples in JSON. This is our supervised corpus for v2 and the value head for v6 — do not build an ingest pipeline; the work is done.
- Self-play logs from headless self-play harness (v3 onward). Note: this is a *separate* driver from the always-on spectator table, even though both consume the same engine. Spectator tables run at human-watchable speed; self-play runs as fast as the engine allows.
- Botzone competition replays from prior years (additional supervised data, plus opponent diversity for evaluation).

**Evaluation:**
- Head-to-head win rate vs. fixed baselines (random, greedy, rule-based v1).
- Average score delta per hand (more granular than win rate; captures defense quality).
- Deal-in rate.
- Average final fan when winning (offensive quality).
- Crucially, **evaluate against opponents the bot did not train against.** Self-play can produce policies that exploit specific quirks of their training partners and collapse against outsiders. Running matches against the prior year's Botzone entries is the cheap version of this check.

**Convention to know:** in multi-agent training, **non-stationarity** is the core problem — your opponents change as you train, so the environment is moving under you. Approaches like **fictitious self-play** (play against a uniform mixture of past versions of yourself, not just the current version) mitigate the resulting cycles and exploitation by held-out opponents.

## Open questions

These are decisions deferred until they're actually answerable. Speculative *techniques* (not decisions) live in [research-ideas.md](research-ideas.md) instead.

- **Model size.** Suphx used relatively small networks by 2026 standards. For MCR on a home server, the right scale is whatever trains overnight on consumer GPU hardware. Don't optimize this until v2 is running.
- **Framework.** PyTorch is the default for research; check whether the Botzone runtime constrains submission format (it historically did — bots must fit a size and runtime budget). Pick the framework that exports cleanly to whatever Botzone accepts.
- **Imitation vs. self-play balance.** When to stop supervised pretraining and start self-play. Empirical question; defer until v2 is running.
- **`botzone-mahjong-environment` maintenance.** The library has incomplete edge-case coverage (flood/final-draw wins) and unclear release cadence. We depend on it for legal-action generation. Decide whether to (a) accept that risk and patch upstream as needed, or (b) implement our own legal-action layer on top of PyMahjongGB. *Verification answer:* a fixture suite of flood/final-draw games whose outcomes match the official judge — if `botzone-mahjong-environment` passes, (a); if not, (b). Decide before v1 ships.
- **Python time-budget on Botzone.** The 1-second budget is documented for C++; Python typically gets a longer budget. Confirm the exact number against the current Botzone wiki before sizing v3/v4 search.

## Competition timeline

Botzone 2026 registration closes **2026-06-09**, three weeks from today. Realistic target is **Botzone 2027**. The 2026 deadline is not a project goal; if a working v1 happens to exist by then it can be submitted opportunistically, but no schedule pressure should derive from this date.

## Build order

Same as component order. Each step ships a feature:

1. Tile-distribution tracker → ships as the "tiles out" overlay for human players.
2. Hand-shape forecaster → ships as the "opponent hand analyzer" overlay.
3. Deal-in risk → ships as a per-discard danger indicator for human players.
4. Payout-weighted ukeire → ships as the "possible outs with fan" overlay.
5. Opponent-need-aware draw distribution → upgrades (3) and (4) silently; no new UI.
6. Rule-based bot v1 wired from (1)–(5) → ships as the always-on spectator table occupant.
7. Value head v6 (supervised on MCR DB).
8. Imitation-learned bot v2.
9. Self-play RL bot v3.
10. MCTS bot v4 for offline analysis.

The user-facing analysis overlays and the bot are not separate workstreams — they share components 1 through 5 entirely. Building the overlays *is* building the bot's perception system.
