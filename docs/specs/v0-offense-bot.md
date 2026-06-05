# Spec 27 — v0 offense bot (greedy, fan-aware)

The second Layer-9 step (AI-plan *Build order* step 1). It adds the first real
decision-making seat: an **in-process, offense-only, k=1 greedy bot** that
replaces the `CannedAdapter`-PASS placeholder filling `kind: "bot"` seats. With
it, the server becomes **solo-playable** — three v0 instances fill a table
around one human.

v0 is the project's **walking skeleton / tracer bullet**: the thinnest
end-to-end perception→decision→seat slice, built to surface integration bugs
early. It is also the **fan-aware greedy baseline** every later architecture
(v1 rule-based, v2 imitation, v3 self-play) must beat head-to-head.

**Status:** draft, pre-implementation. This spec is the design artifact;
implementation is test-first per CLAUDE.md — the decision policy is RL-adjacent
core logic (a reward-shaping-relevant contract), so TDD is mandatory.

## Why v0 isn't trivial: fan-awareness

The one subtlety that keeps v0 honest. Under MCR you must declare a **legal**
win — one clearing the ruleset's `fan_cliff` (8 on Botzone, 3 house) — not
merely a *complete* 14-tile hand. So a hand can be standard-shanten `0` (tenpai)
where **every** winning tile scores below the floor: structurally finished, but
no legal win exists. A shanten-only bot walks straight into these dead-end
tenpai and then can never declare `HU`.

v0 therefore optimizes **shanten toward a fan-feasible archetype**, not toward
any 14-tile completion. This is the whole reason v0 *debuts at the 3-fan house
floor* and that choice is load-bearing: at 3 fan a mostly-shanten-driven bot
with a light fan-feasibility filter is already competent (many natural hands
clear 3); at the 8-fan floor the same simple architecture would be incompetent
(clearing 8 demands deliberate archetype targeting that belongs to v1+).

## Goals

- **A decision policy as a pure function.** `decide_action(view, legal_actions,
  seat, prompt_kind) -> Action` — no I/O, no async, no RNG. Fully deterministic
  and unit-testable in isolation from the async seat machinery.
- **Offense-only, k=1 myopic.** Greedy one-ply efficiency: minimize
  fan-aware distance to a winning hand, break ties by fan-feasible ukeire.
  No opponent modeling, no defense, no lookahead beyond the current decision.
  These are the explicit non-goals that keep v0 simple (they arrive in v1).
- **Always take a legal win.** If `HU` is in `legal_actions`, take it
  unconditionally — legality already guarantees it clears the floor.
- **Take melds (PENG/CHI) only when they strictly help.** A claim is taken iff
  it strictly reduces fan-aware distance versus passing; otherwise PASS.
- **Always GANG.** Any legal kong (concealed/added on own turn, exposed off a
  discard) is taken unconditionally, second in priority only to `HU`. A
  **house-rules heuristic** (not valid MCR strategy): a kong adds fan directly
  (melded kong 1, concealed kong 2) and so does most of the work toward the
  3-fan house floor. It's low-risk structurally — you can only kong a tile you
  hold four of, which was already a complete set, so konging keeps your other
  shapes intact and draws a replacement.
- **A thin `SeatAdapter` shell.** `V0Adapter` implements the five-method seat
  port by delegating `decide` to the pure policy; lifecycle methods are no-ops
  apart from caching its own seat index.
- **Wired into the live server.** `kind: "bot"` seats in `registry.py` and
  `web/server.py` are backed by `V0Adapter` instead of `CannedAdapter`-PASS.
- **Determinism is non-negotiable.** Same `view` + same `legal_actions` → same
  `Action`. A seeded 4×v0 rollout is byte-reproducible (rolls up to a
  determinism-hash fixture, per the AI-plan verification ladder).

## Non-goals

- **Defense / deal-in avoidance — deferred to v1 (component 3).** v0 never reads
  opponent discards to avoid dangerous tiles. It is pure offense. This is a
  deliberate weakness, not an oversight; v1 adds the deal-in risk model.
- **Opponent modeling / belief Stage B — deferred (components 1B, 2).** v0 uses
  only *hard accounting of its own hand*. It does not estimate opponents'
  archetypes or reweight the wall distribution. (Build-order step 2 ships
  Stage A "tiles out" as an overlay; v0 doesn't even need that — its decisions
  read only its own concealed tiles + melds.)
- **Lookahead > 1 ply / learned value function — deferred to v3/v6.** v0 is
  deliberately myopic. The plan's explicit framing: don't deepen the search by
  hand; absorb deep lookahead into a learned function later.
- **Self-applied archetype planner — deferred to v1's component 4.** v0's
  fan-awareness is the *minimal* filter (avoid sub-floor tenpai), not a planner
  that targets a specific high-fan archetype.
- **Tuning the policy against opponents.** v0 ships with hand-set constants. The
  paired-eval / win-rate-margin gates (v0 beats random, beats fan-blind greedy)
  are AI-plan verification; this spec ships the bot and a *sanity* rollout, not
  the full eval-harness tournament (that lands with v1 when there's a ladder of
  opponents to compare).

## The decision policy

`decide_action(view, legal_actions, seat, prompt_kind)`. The self seat's full
hand is `view["seats"][seat]` (a `Seat` with a list `concealed`; opponents are
`SeatViewOpponent` with count-only `concealed`). The ruleset config (for
`fan_cliff`) is resolved from `view["ruleset"]` via the memoized
`resolve_config` — the same seam legality uses, so the bot's floor and the
engine's floor can never disagree (no skew).

```
decide_action(view, legal_actions, seat, prompt_kind):
    # 1. A legal win is always taken — legality already enforced the floor.
    if any(a.type == "HU" for a in legal_actions):
        return the HU action
    # 2. Always GANG (house-rules heuristic). Deterministic pick if several:
    #    sort kong actions by (tile_sort_key(tile), kind) and take the first.
    gangs = [a for a in legal_actions if a.type == "GANG"]
    if gangs:
        return min(gangs, key=lambda a: (tile_sort_key(a.tile), a.kind))
    if prompt_kind == "CLAIM":
        return decide_claim(view, legal_actions, seat, config)
    else:  # DISCARD
        return decide_discard(view, legal_actions, seat, config)
```

`prompt_kind` is authoritative (carried on the `Prompt`); the policy does not
infer phase from the presence of `PASS`.

### The fan-aware distance metric

The core scalar everything ranks on. For a candidate hand `(concealed, melds)`:

```
fan_aware_distance(concealed, melds, seat_wind, round_wind, config) -> float:
    s = pymj.shanten(concealed, melds)          # standard shanten; -1 won, 0 tenpai
    if s != 0:
        return float(s)                          # far from done: fan-awareness can't bite yet (myopic)
    # tenpai: is at least one wait a *legal win off a discard* (a ron)?
    for wait in pymj.winning_tiles(concealed, melds):
        if pymj.calculate_fan(concealed, melds, wait,
                              win_type="DISCARD",     # robust probe (see note)
                              seat_wind=..., round_wind=..., ruleset_config=config):
            return 0.0                            # fan-feasible tenpai — a real, legal win is reachable
    return SUBFLOOR_TENPAI_DISTANCE               # not ron-able at floor: needs reshaping
```

- `SUBFLOOR_TENPAI_DISTANCE = 0.5` — a named, tunable constant. A sub-floor
  tenpai is *structurally* one tile from completion but not from a *legal* win,
  so it ranks **between** a fan-feasible tenpai (`0.0`) and a genuine 1-shanten
  (`1.0`). The exact value is a judgment call; tests pin the **ordering**
  (`feasible tenpai < sub-floor tenpai < 1-shanten`), not the literal `0.5`, so
  it stays tunable.
- **Fan-awareness only bites at tenpai.** For `s >= 1` we cannot cheaply check
  whether *all* reachable completions are sub-floor (that's the full
  archetype-planning problem deferred to v1). v0 honestly applies the filter
  only where it's a single `winning_tiles` enumeration away. This is the
  documented myopic limitation.
- **`win_type="DISCARD"` for the feasibility probe.** This is the *robust*
  direction, not the permissive one. A hand that clears the floor on a discard
  (ron) automatically clears it on self-draw too (self-draw fan ≥ 0), so
  "feasible = ron-able" means "a real, both-ways-winnable hand." The rejected
  alternative — probing with `SELF_DRAW` — would mark a hand that can win *only*
  on self-draw (e.g. 2 fan in hand waiting on the self-draw fan for the third)
  as feasible `0.0`, locking the bot into a shape that wins only ~1-in-4 of the
  time instead of reshaping toward one that also wins off a discard. With the
  `DISCARD` probe, a self-draw-only tenpai scores `SUBFLOOR_TENPAI_DISTANCE`
  (the bot tries to improve it) — yet it **still wins** if it draws the tile,
  because `HU` is taken unconditionally at step 1 and the engine's HU-legality
  uses the correct win type. No self-draw win is forfeited; the bot just stops
  *targeting* self-draw-only locks. *(Decision: corrected this step per house-
  play feedback — ron-ability is the right feasibility bar.)*

### DISCARD: which tile to play

```
decide_discard(view, legal_actions, seat, config):
    plays = [a for a in legal_actions if a.type == "PLAY"]   # GANG already taken upstream
    best = argmin over plays of the sort key:
        ( fan_aware_distance(concealed - tile, melds, ...),   # 1. minimize distance
          -fan_feasible_ukeire(concealed - tile, melds, ...), # 2. maximize useful draws
          tile_sort_key(tile) )                               # 3. deterministic final tie-break
    return best
```

- `fan_feasible_ukeire(concealed, melds, ...)` = the standard **acceptance
  count** (the k=1 efficiency metric the AI-plan names): at tenpai, the number
  of distinct winning tiles (wait width); otherwise, the number of distinct tile
  types whose draw lowers shanten. It is *raw* (not fan-weighted) because
  fan-awareness already lives in the primary `fan_aware_distance` key — this only
  separates equal-distance candidates by hand flexibility. (Note: it can't be
  defined as `fan_aware_distance(concealed + t) < …` directly — a 3k+2 hand has
  no shanten; the acceptance count is the well-defined form.)
- The three-level key is the textbook efficiency rule — *minimize shanten, break
  ties by ukeire* — with a deterministic terminal tie-break so the determinism
  contract holds with no RNG.

### CLAIM: whether to PENG/CHI

```
decide_claim(view, legal_actions, seat, config):
    claims = [a for a in legal_actions if a.type in ("PENG", "CHI")]  # GANG taken upstream
    pass_distance = fan_aware_distance(concealed, melds, ...)          # do nothing
    best_claim, best_after = None, pass_distance
    for c in claims:
        after_concealed = concealed - (tiles c removes from hand)
        after_melds     = melds + [meld formed by c]
        # claiming forces an immediate discard, so the achievable distance is
        # the best over the subsequent legal discards:
        d = min over t in distinct(after_concealed) of
              fan_aware_distance(after_concealed - t, after_melds, ...)
        if d < best_after:   # STRICTLY better than passing or the prior best
            best_after, best_claim = d, c
    return best_claim if best_claim is not None else PASS
```

- **Claim only on a strict improvement.** Equal distance → PASS. Opening the
  hand sacrifices concealment (fan value and flexibility), so v0 keeps the hand
  closed unless the claim *strictly* advances it. This is a conservative,
  defensible default for a myopic bot.
- Distances are comparable across the call: `pymj.shanten` measures steps to
  tenpai of the *whole* hand (melds reduce the concealed sets required), so a
  13-tile pass-hand and an (11-tile + new meld) claim-hand are on the same
  scale.
- When the claim itself completes the hand, `HU` is already in `legal_actions`
  and was taken at step 1 — `decide_claim` never sees a winning claim.

### Worked examples

1. **Legal self-draw win.** Own turn, `legal_actions` contains `HU` (drawn tile
   completes a ≥floor hand). → `{"type": "HU"}`. (Step 1.)
2. **Always GANG.** Own turn, the bot holds four of a tile (concealed kong
   legal) and no `HU`. → returns the `GANG` action even if a `PLAY` would keep a
   slightly better-shaped hand. (Step 2.) Likewise an exposed kong off a discard
   is taken over any PENG/CHI/PASS.
3. **Fan-feasible vs sub-floor tenpai discard.** Two candidate discards: one
   leaves a tenpai whose waits include a ≥floor *ron* (`distance 0.0`), the other
   a tenpai whose every wait is sub-floor (`distance 0.5`). → play the tile
   yielding the `0.0` hand, even if both are standard-shanten `0`. This is the
   load-bearing fan-aware behavior.
4. **Self-draw-only tenpai is not a target.** A discard leaves a tenpai that
   clears the floor on self-draw but not on a ron (e.g. 2 fan in hand, third
   from the self-draw fan) → `distance 0.5`, *not* `0.0`. The bot prefers a
   discard reaching a ron-able tenpai; but if it later draws the winning tile,
   `HU` is taken (step 1) and the win stands.
5. **Ukeire tie-break.** Two discards both reach fan-feasible tenpai
   (`distance 0.0`); one keeps an open wait (8 fan-feasible ukeire), the other a
   closed wait (4). → play the tile keeping the wider wait.
6. **Beneficial PENG.** Discarded tile lets the bot PENG to drop from 2-shanten
   to 1-shanten toward a fan-feasible shape (`best_after < pass_distance`). →
   `{"type": "PENG", "tile": ...}`; the subsequent DISCARD prompt is handled by
   `decide_discard`.
7. **Useless claim → PASS.** A CHI is legal but claiming + best discard leaves
   distance unchanged (or only reaches sub-floor tenpai no better than
   passing). → `{"type": "PASS"}`.
8. **Determinism.** Identical `view` + `legal_actions` across two calls → byte-
   identical `Action`.

## Architecture & placement

The pure decision logic is the first real inhabitant of the **bot zoo**, so it
lives under `mahjong/bots/`; the async adapter shell lives with its siblings
under `mahjong/adapters/`. Split rationale: the policy is heavily-tested pure
logic with no async surface; the adapter is a trivial async wrapper. Keeping
them apart means the decision tests never touch the event loop.

- `mahjong/bots/v0.py` — `decide_action(...)` and the private helpers
  (`fan_aware_distance`, `fan_feasible_ukeire`). Pure, synchronous, importable
  with no server dependencies. Calls `mahjong.engine.pymj` and
  `mahjong.engine.rulesets.resolve_config` only.
- `mahjong/adapters/v0.py` — `V0Adapter` (`SeatAdapter`). `kind = "bot"`;
  `identity = {"kind": "bot", "bot_id": "v0", "version": "0", "runtime":
  "in_process"}`. `seated(ctx)` caches `self._seat = ctx["seat"]`; `decide(
  prompt)` returns `decide_action(prompt["view"], prompt["legal_actions"],
  self._seat, prompt["kind"])`; `observe`/`left` are no-ops.

No new `BotInterface` abstraction (scope discipline: one bot, not three). v1
will extract the shared seam when it exists.

## Wiring & rollout

`run_hand` takes an explicit `adapters` list, and the table-manager test suite
builds its own scripted `CannedAdapter`s — so those tests are **unaffected**.
The swap touches only the two live-server adapter-construction sites:

- **`registry.py`** `_build_adapters_for_hand` ([registry.py:554](../../mahjong/server/registry.py#L554))
  — for `kind: "bot"` seats, append a `V0Adapter` instead of
  `self._canned_adapters[seat]`.
- **`web/server.py`** the equivalent non-human-seat branch
  ([web/server.py:295](../../mahjong/web/server.py#L295)).

**Rollout constraints:**

- `canned_seat_actions` is **unused by any test** and only ever wired a
  PASS placeholder; the constructor param is kept for now (removing it is
  out of scope) but the default bot adapter becomes `V0Adapter`.
- `bot_pacing_enabled` wraps non-human adapters in `PacedAdapter`. `V0Adapter`
  (`kind == "bot"`) is wrapped the same way `CannedAdapter` was — the pacing
  branch keys on `kind in ("bot", "canned")` and must include `"bot"` (it
  already does; verify).
- The decide-timeout table already has a `bot` row (spec 19); `V0Adapter.kind
  == "bot"` picks it up with no change.
- **Server/web integration tests that ran a hand with unscripted bot seats and
  asserted a specific outcome will change** (the seats now play, so hands
  progress and may end in a win rather than a wall-exhaustion draw). These are
  re-pinned during implementation: prefer asserting *structural* invariants
  (hand reaches TERMINAL, record is well-formed, scores sum to zero) over exact
  tile sequences. Tests needing an exact script keep using `CannedAdapter`
  directly via `run_hand`.

## Engine bugs surfaced by v0 (fixed in this step)

v0 is the first seat that actually *wins by claiming* and *wins at all* (canned
PASS bots only ever produced all-PASS claim windows and wall-exhaustion draws).
That exposed two latent, production-affecting bugs, both fixed here with
regression tests:

1. **Claim-HU dropped `HAND_END`** (`table/manager.py`). `_resolve_claim_window`
   sliced `events[1:]` to drop the duplicate `CLAIM_DECISION` that PENG/CHI/GANG
   re-emit — but a winning `HU` emits *only* `[HAND_END]`, so the slice deleted
   the terminal event. Every client waited forever; any ron stalled the table.
   Fix: drop the leading event only when it's actually a `CLAIM_DECISION`.
   (`tests/table/test_manager_claims.py::test_claim_hu_emits_hand_end_event`.)
2. **Replay couldn't reconstruct winning claims or HU terminals**
   (`records/replay.py`). Replay applied *every* `CLAIM_DECISION` (double-applying
   losers in a resolved window) and treated `HU`/`HAND_END` as informational
   (never applying the win) — so any record ending in HU, or containing a winning
   PENG/CHI/kong, was unreplayable. This breaks the records-as-source-of-truth
   contract (persistence rebuild, late-join replay). Fix: a window/terminal-aware
   replay that applies only what the manager applied, keyed off the (non-uniform)
   window closers — `CLAIM_RESOLUTION` (PENG/CHI/all-pass), the replacement `DRAW`
   (exposed kong), and `HAND_END` (HU).
   (`tests/records/test_replay.py`.)

## Verification fixtures this spec implies

Test-first; each is a pinned `(input) → (expected)` contract. Fixtures 1–8 are
pure-policy (cheapest, no async); 9–11 are integration / determinism / stats.

1. **HU is unconditional.** Any `legal_actions` containing `HU` → policy returns
   `HU`, regardless of phase or hand shape.
2. **GANG is unconditional (after HU).** A `legal_actions` with a `GANG` and no
   `HU` → policy returns the `GANG` (concealed on own turn; exposed off a
   discard, beating PENG/CHI/PASS). With multiple kongs, the
   `(tile_sort_key, kind)`-minimal one is chosen (deterministic). A position
   with both `HU` and `GANG` → `HU` wins.
3. **Fan-aware tenpai ordering (the load-bearing test).** A hand-built position
   where discard A → ron-feasible tenpai and discard B → sub-floor-only tenpai
   (both standard-shanten 0). Policy plays A. Asserted at the `decide_discard`
   level and at the `fan_aware_distance` level (`A == 0.0`, `B == 0.5`,
   `feasible < sub-floor < 1-shanten`).
4. **Self-draw-only tenpai is penalized.** A tenpai that clears the floor under
   `win_type="SELF_DRAW"` but not under `win_type="DISCARD"` →
   `fan_aware_distance == 0.5`, not `0.0` (pins that the probe is DISCARD, the
   house-play correction). A discard reaching a ron-feasible tenpai is preferred
   over one reaching this shape.
5. **Greedy shanten discard.** A position where one discard is clearly
   shanten-reducing and the rest are not. Policy plays the shanten-reducer
   (pins the primary key).
6. **Ukeire tie-break.** Two discards tie on distance; policy plays the one with
   strictly higher fan-feasible ukeire (pins the secondary key). Plus a final-
   tie position where only `tile_sort_key` separates the candidates (pins
   determinism of the terminal tie-break).
7. **Beneficial claim taken.** A claim position where PENG (or CHI) strictly
   lowers distance → policy returns that claim. The resulting DISCARD prompt,
   run through the policy, produces a legal discard.
8. **Useless claim refused.** A claim position where no claim strictly improves
   distance → policy returns `PASS`. Includes a case where the only claim
   reaches a sub-floor tenpai no better than passing.
9. **Floor-conditioned behavior (integration).** The *same* tenpai-choice
   position decided under `mcr-2006` (floor 8) vs `mcr-house-3fan` (floor 3):
   the fan-feasibility verdict (and thus the chosen discard) differs, proving
   the policy reads the floor from config, not a constant.
10. **Adapter conformance + wiring.** `V0Adapter` satisfies the `SeatAdapter`
    runtime-checkable protocol; a `run_hand` with four `V0Adapter`s drives a
    hand to `TERMINAL` and writes a well-formed record whose `score_delta` sums
    to zero.
11. **Sanity rollout + determinism (the RL verification artifact).** A seeded
    4×v0 rollout under `mcr-house-3fan`: (a) **determinism** — the action trace
    hash is byte-reproducible across two runs; (b) **sanity baseline** — over a
    small batch of seeds, the hands are not *all* wall-exhaustion draws (a
    non-trivial fraction reach `HU`), the receipt that the offense policy
    actually wins hands rather than just discarding. "It ran without crashing"
    is not the artifact; "it wins hands and the trace is reproducible" is.

### Post-implementation stats run (not a unit test)

After the bot is functional, run a **~100-game 4×v0 self-play batch** through
the existing self-play harness (`mahjong/selfplay/`) under `mcr-house-3fan` and
report the eval-summary metrics: win rate vs. draw rate, average winning fan,
self-draw vs. discard-win split, kong frequency, and average hand length. This
is the "does the bot actually work" receipt the user asked for — and the first
data point on whether always-GANG and the `0.5` constant behave sensibly in
play (e.g. an implausibly high draw rate would flag the offense policy stalling;
a kong on nearly every hand would flag always-GANG distorting play).

## Alternatives considered

**Discard-only thinnest skeleton (no melds) vs. discard + claims.** Considered
shipping a v0 that never claims (PASS on every PENG/CHI, only draw-and-discard).
Rejected: in MCR many hands are only reachable by melding, so a non-melding bot
at the 3-fan floor would mostly produce wall-exhaustion draws — "playable" but a
poor baseline and a poor sparring partner. Claims that *strictly* reduce
distance are cheap to evaluate (one shanten recompute per claim) and make v0 a
genuine offense baseline. *(Confirmed with the user this step.)*

**Treating sub-floor tenpai as standard-shanten 0 vs. penalizing it.** The
shanten-only choice is simpler but is exactly the silent bug fan-awareness
exists to prevent — the bot would lock into a finished-but-illegal shape and
never `HU`. Penalizing it (`0.5`) is the minimal correct fix. Pinning the
*ordering* rather than the literal constant keeps it tunable without re-baking
the test.

**Pure-function policy + thin adapter vs. logic inside the adapter.** Folding
the decision logic into `V0Adapter.decide` would couple the heavily-tested core
to the async seat machinery and the event loop. Splitting keeps the decision
tests synchronous and fast (verification-ladder rung 4: core unit tests <10s).

**Eager claiming (claim on any non-worsening) vs. strict-improvement-only.**
Eager claiming opens the hand more, sacrificing concealment fan and flexibility
for no myopic gain. Strict-improvement-only is the conservative default; whether
v0 *should* be more aggressive is an empirical question for the eval harness,
deferred until there's an opponent ladder to measure against.

**Always-GANG vs. deferring / conditioning kongs.** Considered deferring kongs
entirely (a myopic distance metric rarely makes a kong the single best move) or
gating them on a distance check. Chosen — per house-play feedback — to take
**every** legal kong unconditionally (after HU). The rationale is house-rules-
specific, *not* valid MCR strategy: at a 3-fan floor a single concealed kong
(2 fan) or melded kong (1 fan) does most of the work toward a legal win, and
konging is structurally low-risk (you only kong a tile you hold four of, already
a complete set). The cost — exposing an added kong to robbing, and forgoing the
spare-tile flexibility — is accepted and will be measured in the 100-game stats
run rather than assumed away. *(Decision: always-GANG, per user this step.)*

**Feasibility probe: `DISCARD` vs `SELF_DRAW`.** See the metric section. Probing
with `SELF_DRAW` (the permissive direction) would let the bot settle for a
self-draw-only tenpai — a hand that wins only when the bot draws the tile
itself. Probing with `DISCARD` makes "feasible" mean "ron-able," which is the
robust, both-ways-winnable bar; self-draw-only shapes are penalized so the bot
reshapes toward ron-able ones, while still taking any self-draw win that
materializes. *(Decision: `DISCARD` probe, per house-play feedback this step.)*

## Open questions

- **Default-flip blast radius.** Exactly which server/web integration tests
  assert outcomes incompatible with playing bot seats is known only once the
  swap is made. Resolution: re-pin those to structural invariants during
  implementation; this spec commits to the *direction* (V0Adapter is the default
  bot seat), not to a frozen list of test edits.
- **Sub-floor-tenpai constant.** `0.5` is a first guess. If a sanity rollout
  shows v0 thrashing between sub-floor tenpai and 1-shanten, tune it (or make it
  ruleset-derived). Pinned by ordering, so tuning won't break tests.
- **Always-GANG fallout.** The 100-game stats run is the first check on whether
  unconditional konging ever hurts (e.g. forgoing a better-shaped concealed hand
  to kong, or feeding robs). If kong frequency is implausibly high or win rate is
  depressed, revisit toward a distance-gated kong rule. Pinned by the stats run,
  not assumed.
- **Self-draw-only shapes.** With the `DISCARD` probe, a self-draw-only tenpai is
  treated as needing reshaping (`0.5`). If the stats run shows v0 abandoning
  hands it could have self-drawn (a depressed self-draw-win share), consider a
  middle tier (self-draw-only ≈ `0.25`, between ron-feasible and dead). Deferred
  until the data justifies the extra constant.
- **When to add defense.** v0's pure-offense stance is a known weakness. The
  trigger to start v1's deal-in model is "v0 is verified to beat random + fan-
  blind greedy but loses points to obvious deal-ins in review" — measured, not
  assumed.
