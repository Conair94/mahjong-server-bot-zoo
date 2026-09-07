# Foundation audit — 2026-09-07

This audit establishes a tested re-entry point for development. It is a broad
repository review with targeted runtime reproductions, not a certification that
every rules corner, deployment mode, or historical experiment is correct.

## Starting point and assessment

The checkout was clean on `main` at `8a71612`, five commits behind `origin/main`.
After fetching, `main` was fast-forwarded to `56e00d8` (PR #47), and all audit
changes were made on `codex/foundation-audit`. The initial non-browser baseline
passed **1,123 tests**. That green baseline concealed real bugs: some tests pinned
incorrect rules, protocol tests accepted fallback behavior, packaging was only
exercised through editable installs, and browser tests were absent from CI.

The pure engine, explicit adapter port, deterministic fixtures, and canonical
record reader are useful foundations. The most consequential architectural
weakness is that those boundaries were inconsistently enforced by orchestration:
canonical events crossed the privacy boundary, adapter initialization escaped the
watchdog, and evaluation bypassed record integrity checks. Large orchestration
modules also combine table state, persistence, networking, and UI lifecycle.

## Corrected in this branch

| Finding | Change and verification |
| --- | --- |
| **Private opponent information reached adapters.** The manager sent raw DRAW events, and event projection exposed every seat's claim opportunities. | Project events before adapter delivery; filter claim opportunities for seats and spectators; return independent nested projections. Public hand-run privacy regression and concealed-kong browser coverage. |
| **Botzone's first request could be empty.** No initial deal reached its history serializer. | Initialize from the private `SeatContext`, split dealer's 14th tile into a draw, and handle redacted concealed-kong notifications. Never send the record's shuffle seed to bots. Protocol initialization regression. Full claim compatibility remains DEF-28. |
| **Cancellation leaked work and resources.** A cancelled hand left its phase task running, records open, or bot processes alive. Initialization failures were ignored; cancellation-resistant initialization could hang before the watchdog. | Own phase/initialization/cleanup tasks with bounded waits; degrade failed seats; close interrupted records without a valid footer; release replaced adapters; force-kill/reap bots even if teardown is cancelled. Regressions include a real SIGTERM-ignoring subprocess. |
| **Official discard payments overcharged 16 points.** Code and its tests repeated the same erroneous formula. | Discarder pays fan + 8, each other loser 8; winner receives fan + 24. At 10 fan, deltas are +34/−18/−8/−8. House conversion stays as configured. See the [MCR rulebook, §3.9.2](https://mahjong-europe.org/portal/images/docs/mcr_EN.pdf). |
| **Self-draw legality invented a winning tile.** It searched the completed hand for the most favorable wait instead of using the actual draw, and could offer HU after a claim with no draw. | Use only the current actor's `last_drawn` tile in legality and settlement. Paired regression: actual B3 yields 7 fan and is rejected, actual B4 yields 8 and qualifies. |
| **Flowers counted toward the winning threshold.** | Flowers contribute to payment after qualification, not to the minimum fan floor. Regression covers 6 regular + 2 flower fan under official and house rules. [MCR scoring rules](https://mahjong-europe.org/portal/images/docs/mcr_EN.pdf). |
| **Kongs were offered with no replacement tile.** | Exposed, concealed, and added kong legality all check wall availability. Tests retain the separate flower-replacement exhaustion case. Existing deterministic fixtures still pass. |
| **Resume skipped earlier gaps and trusted unrelated/corrupt output.** Parallel scans could race another worker's newly opened file. | Validate source, seed, ruleset/hash, seat assignment, filename/index, and canonical checksum before recovery; resume a set of completed indices; preflight the corpus once before worker startup; workers read only their filenames. Real two-worker run and missing/partial recovery verified. |
| **Evaluation accepted tampered score claims.** | Use the canonical checksum/sequence reader, require a completed hand, and log `selfplay_record_rejected` on invalid input. Tampered-HU regression fails before the change. |
| **An arbitrary website could connect to the local admin WebSocket.** | Enforce loopback binding, an allowlisted Host, and same-origin browser requests. Preserve native loopback clients without Origin. Tests reject foreign origins, `null` origin, non-loopback binds, and DNS-rebinding hosts. |
| **Built wheels omitted browser/admin assets.** | Explicit package data and a wheel inspection gate; all 16 current static assets/rulesets included. Editable installs no longer hide this packaging omission. |
| **Tooling allowed incomplete green builds.** | Remove dependency-install and integration-test failure suppression; fix pytest asyncio configuration; pin reviewed tool/dependency resolution; add a dedicated Chromium CI job and build verification. Fast pre-commit tests remain a focused engine/record subset. |
| **Auth timing test was flaky under ordinary load.** | Replace sequential wall-clock ratios with assertions that all four login outcomes perform a real Argon2id verification at the configured cost. This guards against early-return bypass, not a formal constant-time proof. |

The current Ruff version also reformatted existing parenthesized assertions across
several tests. Those formatting-only changes are intentional; golden fixture files
were not regenerated. Runtime data and machine-specific agent settings are now
ignored and untracked, with their local files preserved.

## Work to prioritize next

The deferred ledger is canonical; these are the architectural findings behind the
new entries. These limits should constrain claims about evaluation and readiness.

1. **Finish MCR transitions and scoring context (DEF-27).** Added kong currently
   has no robbing claim window. Last-tile/replacement-draw context is not propagated
   to fan calculation, fourth-tile scoring is not implemented, and round wind is
   fixed to East. Wall dealing/replacement is a deterministic approximation, not a
   complete tournament-wall simulation. Add specific rule fixtures before claiming
   full MCR fidelity or training against its rewards.
2. **Complete Botzone claim semantics (DEF-28).** The adapter/parser conflates
   a PENG/CHI response's accompanying discard with a claimed tile; the serializer
   describes claim submissions instead of only resolved actions. Current round-trip
   tests and reference-bot termination can pass while seats degrade to AutoPass.
   Build judge-backed accepted traces and assert no adapter errors/replacement.
   The subprocess examples live outside the Python package and require a source
   checkout; wheel-only self-play needs packaged bot resources or in-process CLI
   policies before it is supported.
3. **Version experimental results (DEF-29).** Live records can say `unknown` and
   self-play defaults to `dev` for git SHA. Ruleset hashes cover JSON configuration,
   not engine implementation, native calculator, or bot code. This audit changes
   legal actions and payouts; an old corpus with an identical configuration hash
   may have different semantics. Resume validation does not solve code provenance.
   Start a new output directory for this engine revision; preserve old records.
4. **Isolate blocking work (DEF-30).** SQLite/RLock operations and synchronous bot
   and analysis work execute in shared async request/table paths. A connection lock
   fixes concurrent database use but does not make SQLite or Argon2 asynchronous;
   a slow operation can delay every table and health checks. Profile representative
   concurrent tables, then introduce a worker/persistence boundary with bounded
   scheduling. Do not scatter `to_thread` calls around shared mutable state.
5. **Consolidate hand orchestration (DEF-31).** The older web server and live registry
   duplicate orchestration; the registry, session mux, and orchestrator are large
   modules with coupled lifecycle responsibilities. Snapshot state is published
   after an entire phase step, so intermediate event delivery can race a reconnect
   snapshot. Define a single owner for state publication and table lifecycle, with
   integration tests spanning disconnect, claim, settlement, and readiness.

Existing DEF-20 (persisted logs), DEF-24 (finite-match ready gate), DEF-25
(settlement analysis cost), and DEF-26 (mid-gate roster) remain relevant. DEF-17 is
closed by the wall-legality fix; DEF-18 is closed as an incorrect report: **Chicken
Hand is an official eight-fan pattern**, not a calculator bug. See fan #43 in the
[MCR rulebook](https://mahjong-europe.org/portal/images/docs/mcr_EN.pdf). DEF-22 is
implemented by the dedicated browser CI job; an actual remote run is still owed.

## Git cleanup and recovery

There were 39 old local branches besides `main` and the new audit branch. Thirty
were ancestors of current `main`; nine held commits outside it. Patch inspection
showed duplicated/cherry-picked fixes, old memory notes, and real unmerged work:
`feat/docs-pane` contains a docs UI, while `fix/fb18-drawn-tile-targeting` and
`fix/fb19-ready-gate` retain a persisted-logging implementation. The audit did not
silently merge those features or discard their history.

- All nine unique tips have annotated tags under
  `archive/pre-foundation-2026-09-07/<old-branch>`; each tag was verified before
  deleting any old branch pointer.
- [The branch inventory](audit/branches-2026-09-07.tsv) records all 39 names,
  exact commits, merge disposition, and recovery tags. To resume an archived
  feature, create a new branch from its tag, review its delta against current
  `main`, then selectively port useful changes.
- Local branches now consist of `main` and `codex/foundation-audit`. Stale remote
  tracking refs were pruned by fetch; no remote branch was deleted or rewritten.
- `git fsck --full` found dangling objects but no corruption. A full metadata backup
  at `var/audit/git-metadata-before-foundation-2026-09-07.tar.gz` preserves `.git`,
  including reflogs, dangling stash-like work, and submodule metadata. The adjacent
  inventory/fsck files aid recovery. These ignored local backups are not in Git.
- No garbage collection or history rewriting was performed. No deployment,
  database migration on live data, or remote push was performed.

A Git clone/push does not include the ignored metadata backup. Keep it until the
archived work is reviewed; push chosen archive tags explicitly if moving machines.
Avoid extracting `.git` over an active checkout—inspect the backup in a separate
scratch directory for recovery.

## Verification record

See the final validation results recorded below. All local tests ran on macOS,
Python 3.13; Linux-specific sandbox behavior and the other CI matrix cells require
an actual CI run. Local suite logs and build outputs are retained under
`var/audit/validation/` for this checkout.

| Check | Local result |
| --- | --- |
| Non-browser suite after fixes | **1,163 passed, 2 Linux-only skipped**, 73 seconds |
| Full Chromium suite during the audit | **162 passed**, 13 minutes 42 seconds |
| Chromium privacy and live-play checks after final projection changes | **6 passed**, 17 seconds |
| Ruff lint and formatting | Passed; 289 Python files formatted consistently |
| Mypy | Passed; 108 source files |
| Distribution contents and runtime dependencies | Fresh sdist/wheel build, 16 packaged assets/rulesets, and `pip check` verified |
| Installed wheel smoke | Temporary server outside the checkout served `/health`, `/`, and `/static/app.js` successfully |
| Pre-commit | All hooks passed; hooks installed in this checkout |

The full browser run preceded the final adapter privacy/initialization changes;
the affected browser paths were rerun afterward. Remote CI, Linux sandbox checks,
manual deployed audio/visual verification, and full Botzone judge acceptance were
not run or claimed. New behavioral regressions were observed failing before their
corresponding fixes; the pre-existing green baseline alone was not used as proof.
