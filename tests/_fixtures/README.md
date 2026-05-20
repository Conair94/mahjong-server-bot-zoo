# Golden fixtures

JSON fixtures for cross-platform determinism tests and behavior pinning.

Conventions:

- One file per fixture; named for what it pins, not when it was added (see CLAUDE.md fixture discipline).
- Loaded via `tests.conftest.load_golden("relpath.json")`.
- Updating a fixture requires justifying the behavior change in the commit message. Auto-updating is the canonical anti-pattern.
- Determinism fixtures (RNG byte streams, canonical tile set, `shuffled_wall(seed=12345)`, canonical-hash goldens) come online with Layer 0.3 in [docs/specs/implementation-order.md](../../docs/specs/implementation-order.md).
