---
name: ledger
description: Work through one or more items on the Deferred ledger (DEF-NN in docs/specs/feedback-backlog.md), then commit. Triggers on "work the deferred ledger", "burn down deferred work", "pick up a DEF item", "do a ledger task", "work through deferred tasks", "knock out a deferred item".
argument-hint: "[DEF-NN | count]"
allowed-tools:
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - Bash
  - AskUserQuestion
  - TodoWrite
---

# Ledger burndown

Pick up parked work from the **Deferred ledger** in
[docs/specs/feedback-backlog.md](../../docs/specs/feedback-backlog.md), do it *with a
verification artifact* (this project forbids unverified "done"), retire the ledger
row(s), and commit. One invocation = a small, coherent batch + one or more commits — not
a marathon.

The ledger is the source of truth for what's parked; this skill is the thing that walks
it down. Read [CLAUDE.md § Deferring work](../../CLAUDE.md) for the contract before you
touch a row.

## Workflow

### 1. Load the ledger

Read the **Deferred ledger (DEF-NN)** table in
[docs/specs/feedback-backlog.md](../../docs/specs/feedback-backlog.md). Each row has
**what / why / revive-trigger / grep-or-ref**. Hold the table in a TodoWrite list so
progress is visible.

### 2. Triage actionability — DO NOT skip this

A ledger row is only workable if its **revive-trigger is actually met**. Classify each:

- **Actionable now** — the trigger is "a player asked", "next deploy", a threshold that's
  been crossed, or simply "do the work" (e.g. a punted feature with no external gate).
- **Blocked on signal** — *instrument-and-defer* rows whose trigger is a recurrence. These
  have a **grep string** in the ledger (e.g. `hand_loop_crashed` for **DEF-01**). They are
  NOT workable just because they're listed. Check the trigger:
  ```bash
  # Has the parked signal actually fired? Search the live logs / records.
  grep -rn 'hand_loop_crashed' ~/.local/share/mahjong-server/ 2>/dev/null
  ```
  If the string is absent, the investigation has no stack trace to work with — **leave the
  row, say why, move on.** Working it now would be guessing, which is exactly what the
  deferral avoided.
- **Browser-/play-verify owed** (e.g. **DEF-04**) — the harness cannot truly verify
  audible sound or on-screen rendering. Do any *testable* sub-part, but do **not** flip
  these to `verified` from code alone. Say "owed against a live session" and leave the row.

Report the triage (actionable vs blocked, with the reason for each blocked row) before
doing anything.

### 3. Choose scope

- If an argument names a `DEF-NN`, work exactly that (and refuse with the triage reason if
  it's blocked).
- If an argument is a count `N`, take the top `N` **actionable** rows.
- Otherwise default to the **single top actionable row**, and if more than one is
  actionable, use `AskUserQuestion` to confirm how far to go (1 / a named subset / a small
  batch). Don't silently expand scope — match the action to the request.

### 4. Branch

If on the default branch (`main`), create a working branch first
(`chore/ledger-<def-ids>`), per the working agreement. If already on a feature branch,
stay on it.

### 5. Do the work — with a verification artifact

Follow the project's TDD rules ([CLAUDE.md](../../CLAUDE.md)): **test-first for core**
(rule engine, RL env, training loop, eval harness, wire protocol); pragmatic for glue/UI.
Every closed row needs one of:

- a unit/integration test that fails without the change and passes with it,
- a deterministic seeded-rollout hash match,
- an eval-harness number with its prior for comparison,
- for CLI/scripts: a real run with output shown,
- for UI: an actual exercise of the feature — or an explicit "can't verify the UI here."

"It runs / it compiles" is **not** a verification artifact. If a row's only remaining work
is verification the harness can't do, leave it and say so.

### 6. Retire the row(s)

In the **same change**, update the ledger: delete each completed `DEF-NN` row (or, if only
partially done, rewrite its *what* to the residue and keep it). Keep the FB-table and any
linked spec consistent — if the row pointed at a spec's "Deferred:" note, update that too.

### 7. Commit

Match the repo's existing style (`git log --oneline -10`): **conventional commits**
(`feat`/`fix`/`docs`/`refactor`/`test`/`chore`), imperative subject ≤72 chars. The commit
**body must cite the verification signal** (this repo does — e.g. "Full fast suite 1115
passed"). One commit per logical DEF item; group only tightly-related rows.

End every commit message with the repo's trailer:

```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

Stage explicitly (`git add <paths>`) — never `git add -A`; `var/` runtime data
(SQLite/`-wal`/`-shm`, records JSONL) is gitignored churn and must not be swept in.

**Do not push** unless the user asked for a push or a PR. Commit and stop.

## Output

Per item worked: the `DEF-NN`, what changed, and the one-line verification signal. Then the
commit hash(es). Finally, list any rows you **left** (blocked-on-signal or verify-owed) with
the one-line reason — so the user sees both what moved and what's still parked and why.
