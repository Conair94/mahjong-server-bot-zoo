# mahjong-server-bot-zoo — working agreement

Project goals and decisions live in [docs/ai-plan.md](docs/ai-plan.md) and [docs/server-plan.md](docs/server-plan.md). This file is the working agreement: how we build, not what we build.

## North star

A home-hosted MCR mahjong server with a TUI client and a zoo of bots trained against it. The user is also using this project to learn AI/ML and server-ops conventions, so name techniques inline as they come up (e.g. "this is a *tracer bullet*", "this is *behavior cloning before RL*") and say briefly *why* the convention exists.

## Verification is the product

This project is RL-heavy. The single biggest failure mode in RL work is **believing the agent is learning when it isn't** — silent reward bugs, broken env transitions, eval-on-train-distribution, off-by-one in terminal states. Every one of those produces curves that look plausible.

So the rule is: **no learning claim without a verification artifact.** A "verification artifact" is one of:

- a unit/integration test that pins the behavior,
- a deterministic seeded rollout whose hash matches a recorded fixture,
- an eval-harness number on a held-out scenario, with the prior number for comparison.

"It trained without crashing" is not a verification artifact.

## TDD: hard for core, pragmatic for glue

**Test-first is mandatory for:**

- Rule engine (legality, scoring/fan calculation, terminal detection, wall/draw mechanics).
- RL environment (observation shape, action mask, reward signal, episode boundaries, seed determinism).
- Training loop invariants (gradient flow, target-network updates, replay-buffer semantics).
- Evaluation harness (matchup scoring, ELO/skill update math, fixture-based regression).
- Bot ↔ server protocol (Botzone JSON contract — see [feedback memory on existing standards](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_prefer_existing_standards.md)).

Workflow: write the failing test that pins the contract → make it pass with the simplest code → refactor. The failing test is the design artifact; skipping it skips the design step.

**Test-first is optional for:** CLI argument parsing, config loaders, logging glue, one-off analysis scripts, TUI cosmetics. Cover after the fact if the behavior is non-trivial; skip if it's obvious.

If you're unsure which bucket something falls in, ask. Drift toward strict.

## Verification ladder (cheap → expensive)

Run cheap checks constantly, expensive ones at decision points:

1. **Formatter** — auto on save.
2. **Linter / static analysis** — pre-commit.
3. **Type-check** — pre-commit.
4. **Unit tests** — pre-commit (must be fast: <10s for the core suite).
5. **Integration tests** — pre-push (env↔agent↔server round-trip on a fixture game).
6. **Determinism check** — seeded rollout hash matches recorded fixture (catches silent env or RNG changes).
7. **Eval harness on tiny fixture** — agent-vs-random and agent-vs-prior-checkpoint on a handful of games. Before any "the new model is better" claim.

Hooks for 1–4 get wired into the repo once the language is chosen (server-plan is still open on this). Until then, run them manually and don't skip.

## RL-specific guardrails

- **Sanity baselines before scaling.** Before claiming a learning algorithm works, demonstrate: random-vs-random produces ~uniform win rates; self-play converges on a trivial sub-game; a known-good policy beats random by the expected margin. If these don't hold, the bug is in the env, not the agent.
- **Reward shape is a tested contract.** Every change to the reward function gets a test that pins inputs → expected reward. Reward bugs are the most common silent failure.
- **Determinism is non-negotiable for debugging.** Seeded rollouts must be byte-reproducible. If a refactor changes the hash, that's a flag — either the refactor changed behavior (investigate) or the test fixture needs updating (justify).
- **Eval is separate code from train.** Evaluation must not share mutable state, RNG, or normalization stats with the training loop. Cross-contamination here invalidates the whole experiment.
- **Log enough to post-mortem a bad run.** At minimum: seed, config hash, git SHA, eval results per checkpoint. "I think it was a few days ago" is not debuggable.

## Prefer existing standards

Default to whatever standard already exists in the surrounding ecosystem (Botzone JSON, MCR replay logs, mature shanten/fan libraries) over custom equivalents. See the project's [prefer-existing-standards memory](../../.claude/projects/-Users-connorlockhart-Documents-GitHub-mahjong-server-bot-zoo/memory/feedback_prefer_existing_standards.md) for the full rationale. Flag any new conversion boundary explicitly.

## Scope discipline

- The plans in `docs/` are the source of truth for what we're building. If you find yourself about to add a feature not in a plan, stop and update the plan first.
- Walking skeleton before depth: end-to-end "ugly but working" path (server ↔ bot ↔ replay) beats a polished rule engine with no surrounding system.
- No speculative abstractions. Three concrete bots before a `BotInterface`. One trained model before a `ModelRegistry`.

## Deferring work

The canonical ledger for *anything parked* — punted features, browser-verify-owed UI, and instrument-and-defer follow-ups — is the **Deferred ledger** section of [docs/specs/feedback-backlog.md](docs/specs/feedback-backlog.md). Player-reported items keep their `FB-NN` ids; everything else gets a `DEF-NN` id in the same doc.

Every entry names **what / why / revive-trigger** (see the global working agreement's "Deferring work"). Don't leave a deferral as a bare paragraph in a spec — the spec can hold the detail, but the one-line ledger row is what makes it discoverable.

**Instrument-and-defer is load-bearing here** because the project's verification rule forbids silent failure. When a fix converts a silent failure into a logged one and parks the root cause (the FB-01 `hand_aborted` guard is the template), the ledger row records the **exact log string to grep** and the log line records the `DEF-NN`. When that string appears in a run, the parked investigation resumes with the stack trace it was waiting for.
