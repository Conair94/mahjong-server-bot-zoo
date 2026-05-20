# Research ideas

Speculative techniques that could eventually go into the bot but are **not** on the committed roadmap. Items here are *parked, not endorsed*. The bar for promoting an idea from this document into [ai-plan.md](ai-plan.md) is:

1. A current architecture is shipping and stable in evaluation.
2. The idea addresses a *measured* limitation of that architecture (not a guessed one).
3. We can describe a verification artifact that would tell us, with confidence, whether the idea helped.

Promotion path: move the entry from here into ai-plan with a Verification subsection naming the artifact below. Don't start building until the entry has crossed over.

This document is for the **model side** — perception, training, decision-making. Speculative server/ops ideas live in [server-plan.md](server-plan.md). The working agreement that gates promotion (TDD-first for core, verification artifacts required) is in [../CLAUDE.md](../CLAUDE.md).

## Format

Each idea below carries four lines so it can be evaluated honestly:

- **What it is** — one-sentence definition with the convention named.
- **Why it might help** — the specific limitation it addresses.
- **What would prompt us to try it** — the trigger condition, ideally measurable.
- **Verification artifact required to ship it** — the evidence we'd need before merging it.

The third bullet is the most important. An idea without a clear trigger is an idea that will accumulate sunk cost.

---

## Suphx techniques not in v3

The v3 architecture in [ai-plan.md](ai-plan.md) adopts Suphx's basic self-play + value-head recipe. Suphx has two further ideas not committed in v3:

### Oracle guiding

- **What it is.** During training only, the policy sees opponents' actual hidden hands; the loss pulls it toward decisions an omniscient player would make; the oracle signal is gradually decayed so the deployed policy never sees hidden state. Suphx, 2020.
- **Why it might help.** Cold-start in imperfect-information self-play is brutal — early policies have no idea what opponents hold, so action-value estimates are noise and gradients are noisy in turn. The oracle gives a much stronger signal during the phase when the bot is worst at inferring belief state on its own.
- **What would prompt us to try it.** v3 self-play converges slowly or plateaus at a level v2 already reached. Symptom: training loss decreases but evaluation against fixed opponents doesn't improve.
- **Verification artifact.** Controlled A/B with same seeds, with-oracle vs without-oracle, on a held-out opponent set. With-oracle must beat without-oracle by a margin larger than seed variance across N runs.

### Run-time policy adaptation

- **What it is.** At the start of each game, do a few gradient steps tailoring the policy to that game's score state (lead vs trailing changes optimal risk tolerance). Suphx called this "parametric Monte Carlo policy adaptation."
- **Why it might help.** A single policy trained over the full distribution of score states is a compromise. Game-start adaptation turns one weight set into a family of policies indexed by context.
- **What would prompt us to try it.** v3 evaluation shows the bot plays "the same way" regardless of score state — diagnose by stratifying eval results by entering-game score delta.
- **Verification artifact.** Stratified eval: win rate vs fixed opponents, bucketed by entering-game score state. Adaptation must improve disadvantaged buckets without regressing the others.

---

## Imitation-learning extensions

### DAgger (Dataset Aggregation)

- **What it is.** Behavior cloning (v2 baseline) trains only on states experts visited. When the resulting policy errs, it ends up in states experts *didn't* visit, where it has no training signal. DAgger fixes this by iteratively running the policy, labeling the new states it visits with an expert (or expert proxy), and adding them to the training set. Ross et al., 2011.
- **Why it might help.** v2's known failure mode is *distributional shift* — performance degrades when the bot's own play takes it into states the MCR corpus doesn't cover well.
- **What would prompt us to try it.** v2 trains well on validation loss but eval against rule-based v1 shows late-game collapses or repeated illegal/dominated discards.
- **Verification artifact.** Regression test comparing v2-without-DAgger to v2-with-DAgger on a fixture of late-game positions where v2 historically degrades.

### Inverse RL on the MCR corpus

- **What it is.** Rather than imitating the expert's *action*, infer the *reward function* the experts behaved as if they were optimizing, and train against that. Family: Maximum Entropy IRL, GAIL.
- **Why it might help.** MCR's official reward (end-of-game fan delta with -30 penalty for invalid HU) is sparse and discontinuous. Experts may behave as if optimizing a denser implicit reward; recovering that reward gives a learning signal better-shaped than the raw scoreboard.
- **What would prompt us to try it.** v3 self-play with raw-fan reward is high-variance and unstable, *and* hand-designed reward shaping is making the bot exploit the shaping rather than learn.
- **Verification artifact.** Sanity check: the recovered reward ranks recorded expert games above random-bot games. Downstream check: a policy trained against the recovered reward beats one trained against raw fan reward on held-out opponents.

---

## Search and decision-time ideas

### IS-MCTS (Information Set MCTS)

- **What it is.** Vanilla MCTS assumes you can simulate forward from the current state. In imperfect-information games you don't *know* the current state — you have an information set (the set of states consistent with what you've observed). IS-MCTS samples possible hidden states from your belief and runs MCTS over each, aggregating results. Cowling et al., 2012.
- **Why it might help.** v4 in [ai-plan.md](ai-plan.md) is a vanilla-MCTS variant already flagged as Botzone-budget-impractical. IS-MCTS is the imperfect-info-correct generalization; if v4 is worth building at all, IS-MCTS is the form it should take.
- **What would prompt us to try it.** The driver is *not* Botzone-time competitive play (1-second budget rules out any meaningful MCTS) but offline analysis and the explainer overlay.
- **Verification artifact.** Decision-quality test: on a fixture of hand-traced "right answer" discards (hard positions where the correct answer is non-obvious but agreed), IS-MCTS matches a higher fraction of correct answers than greedy v1.

### Policy distillation for the Botzone budget

- **What it is.** Train a small, fast student network to mimic a large, slow teacher's policy distribution. Hinton et al., 2015 for the original framing; standard practice for deploying large models in tight inference budgets.
- **Why it might help.** Botzone's ~1-second-per-interaction limit means whatever wins the competition is *fast*, not necessarily smartest. If our best policy is a heavy v3+v6 hybrid that won't fit the budget, distilling it into a small fast net is the deployment path.
- **What would prompt us to try it.** v3 (or beyond) clearly outperforms v2 in offline eval but exceeds the Botzone budget in live play.
- **Verification artifact.** Distillation regression: student's win rate against a fixed opponent set must be within X% of the teacher's, while measured per-decision latency is below the Botzone budget with margin.

---

## Multi-agent training ideas

### Population-based training (PBT)

- **What it is.** Train a *population* of agents in parallel with slightly different hyperparameters; periodically the worse-performing agents copy weights and perturb hyperparameters from the better ones. Jaderberg et al., 2017.
- **Why it might help.** v3 self-play with a single agent is vulnerable to cycles (rock-paper-scissors dynamics) and to converging on opponent-specific exploits. A population gives diversity and amortizes hyperparameter search.
- **What would prompt us to try it.** v3 single-agent self-play shows non-monotonic eval over time (gets better, then worse, then better), or wins against itself but loses to held-out opponents.
- **Verification artifact.** Top-of-population agent must beat (a) a single-agent v3 trained with the *best* fixed hyperparameter from PBT history, and (b) the held-out opponent set.

### Fictitious self-play and Neural FSP

- **What it is.** Train against a uniform mixture of *all past versions* of yourself, not just the latest. NFSP applies this in deep RL with a current-policy and average-policy network pair. Heinrich & Silver, 2016.
- **Why it might help.** Same diagnosis as PBT — current-opponent-only self-play is unstable due to cycles and overfitting. FSP is the simpler population-of-one version.
- **What would prompt us to try it.** Same trigger as PBT. Try FSP first if simpler-than-PBT appeal matters; try PBT if hyperparameter search is also a bottleneck.
- **Verification artifact.** Same shape: held-out opponent set, FSP agent must beat current-only-self-play agent.

---

## Modeling ideas

### Wall-order permutation modeling

- **What it is.** The wall is a permutation of remaining tiles, not an i.i.d. draw. Model future draws as samples from a conditional distribution over permutations rather than i.i.d. from marginals.
- **Why it might help.** Mostly marginal. The i.i.d. assumption is "wrong in principle, small error in practice" per the analysis in ai-plan.
- **What would prompt us to try it.** Probably never. Listed here for completeness; the question was raised and dismissed during planning, and that dismissal stands until evidence overturns it.
- **Verification artifact.** Side-by-side ukeire calculator (permutation-aware vs i.i.d.) on a fixed test fixture. EV difference must be larger than estimation noise before any further work is justified.

### LLM-based commentary / explainer

- **What it is.** Generate natural-language explanations of bot decisions using an LLM conditioned on the structured decision trace (belief state, forecast, EV ranking). Layered on top of the [explainer paper](https://arxiv.org/html/2506.14246v1) referenced in the README — that one produces structured rationales; the LLM makes them speakable.
- **Why it might help.** Two uses: (1) makes the always-on spectator table interesting to watch, (2) the user's stated learning goal — narrated bot decisions are a teaching tool.
- **What would prompt us to try it.** Spectator table is running, the structured explainer is producing decision rationales, and there's appetite for commentary.
- **Verification artifact.** Faithfulness check: on a fixture of decisions, the LLM's natural-language explanation must agree with the structured rationale's top-2 cited reasons. We explicitly do *not* want an LLM that hallucinates plausible-sounding mahjong reasoning untethered from the bot's actual computation.

---

## Review cadence

This document is reviewed when:
1. A current architecture ships and is stable in evaluation.
2. The user's learning focus shifts toward a technique listed here (this is a learning project as well as a build).
3. A new paper or competition result obsoletes a current plan element or upgrades the prior on something here.

Adding to this document is cheap. Promoting *out* of it requires a Verification subsection in ai-plan naming the artifact.
