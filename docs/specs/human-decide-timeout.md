# Spec 19 — Human-friendly decide-timeout

A 30-second per-prompt decide deadline (`decide_timeout_seconds`, [mahjong/server/config.py](../../mahjong/server/config.py) default, passed through to [mahjong/server/registry.py](../../mahjong/server/registry.py)'s `TableHandle.__init__`) is reasonable for bot adapters and CI tests but beginner-hostile in real human play. A new player who needs to read the tile glyphs, consider claims, or just glance away from the screen for a moment loses their turn — `_decide_or_default` in [mahjong/table/manager.py](../../mahjong/table/manager.py) returns the prompt's `default_action` (tsumogiri for DISCARD, PASS for CLAIM) plus a `timeout` failure-meta — and after `strike_limit` (default 3) timeouts the seat gets swapped to `AutoPassAdapter` for the rest of the hand.

This spec defines a per-seat-kind timeout policy so human seats get a generous deadline by default, while bot seats keep the short one CI relies on, with explicit knobs to override either.

Tier-2 spec. Surfaces in [seat-port.md](seat-port.md) (the timeout is a contract of the adapter port) and [server-lifecycle.md](server-lifecycle.md) (config / env-var wiring). Driven by user report 2026-05-26 ("the game keeps playing without me making choices") — the root cause was the DRAW.tile bug (now fixed at commit 9f831c7), but the timeout will bite real humans the moment that confounder is gone.

## Goals

- **Human seats get enough time to think.** Default human-seat decide-timeout is 60 seconds for DISCARD prompts and 20 seconds for CLAIM prompts. (Claim windows block all four seats, so a long per-claim deadline stalls the table; 20s is enough for a human to decide PASS vs. PENG/CHI/GANG/HU on a single discarded tile.)
- **Bot seats keep the short timeout.** CannedAdapter, bot-runner subprocesses, and AutoPassAdapter all decide synchronously (or near-synchronously); a 30s default for them is generous, and CI test suites assume short timeouts. Don't slow tests down.
- **Per-prompt kind, not per-seat kind alone.** DISCARD takes longer to think about than CLAIM (CLAIM is a yes/no on a single tile); the engine knows the prompt kind, so timeouts can differ.
- **Config knobs are explicit and overridable.** `MAHJONG_DECIDE_TIMEOUT_HUMAN_DISCARD_S`, `MAHJONG_DECIDE_TIMEOUT_HUMAN_CLAIM_S`, `MAHJONG_DECIDE_TIMEOUT_BOT_S`. Tests pass the values directly into `TableHandle(...)` / `mgr.run_hand(...)`.
- **Strike system unchanged.** Three timeouts → AutoPassAdapter swap stays the same; just the *clock* is more forgiving for humans.

## Non-goals

- **Per-account customisation.** A future "patient mode" / "blitz mode" per player is out of scope. Server config governs everything in v1.
- **Activity-based deadline reset.** Stretching the deadline when the user clicks around the UI ("they're still here, give them another 30s") is a much bigger UX feature — it requires wire-protocol additions (`PROMPT_HEARTBEAT` from client to server). v1 ships fixed-duration prompts.
- **Variable timeouts mid-hand.** Once a prompt is issued, its deadline is fixed. We don't extend it because a strike was forgiven, or shrink it because the player is on their last strike.

## The schema / interface

### Config additions

[mahjong/server/config.py](../../mahjong/server/config.py) `ServerConfig` gains three fields (defaults shown):

```python
decide_timeout_human_discard_s: int = 60
decide_timeout_human_claim_s: int = 20
decide_timeout_bot_s: int = 30
```

Env-var bindings in `load_config_from_env`:

| Variable                                  | Default | Notes                              |
| ----------------------------------------- | ------- | ---------------------------------- |
| `MAHJONG_DECIDE_TIMEOUT_HUMAN_DISCARD_S`  | 60      | DISCARD prompts on human seats     |
| `MAHJONG_DECIDE_TIMEOUT_HUMAN_CLAIM_S`    | 20      | CLAIM prompts on human seats       |
| `MAHJONG_DECIDE_TIMEOUT_BOT_S`            | 30      | All prompts on bot seats (today's default) |

`cli/serve.py` forwards each new field to `MultiTableOrchestrator(...)`, which forwards them to `TableHandle(...)`. Tests inject directly.

### Table-manager interface

[mahjong/table/manager.py](../../mahjong/table/manager.py) `run_hand` currently takes a single `decide_timeout_seconds: float`. Replace with a `decide_timeouts` dict keyed by `(seat_kind, prompt_kind)`:

```python
@dataclass(frozen=True)
class DecideTimeouts:
    human_discard_s: float
    human_claim_s: float
    bot_s: float                 # used for all prompt kinds on bot seats

    def for_(self, seat_kind: Literal["human", "bot", "canned"], prompt_kind: PromptKind) -> float:
        if seat_kind != "human":
            return self.bot_s
        if prompt_kind == "DISCARD":
            return self.human_discard_s
        return self.human_claim_s   # CLAIM
```

`run_hand` takes a `decide_timeouts: DecideTimeouts` and threads it through `_step_discard` / `_step_claim_window`. The existing single-value `decide_timeout_seconds` parameter is kept as a back-compat shim (one value applies everywhere) so we don't have to rewrite all call sites at once.

Each call to `_build_prompt(state, seat, prompt_kind, deadline_seconds=...)` uses the appropriate timeout looked up via `decide_timeouts.for_(adapter_kind, prompt_kind)`. `adapter_kind` is derived from the seat's adapter: `HumanAdapter → "human"`, `CannedAdapter / AutoPassAdapter → "canned"`, future `BotRunnerAdapter → "bot"`. Today only the human / canned distinction matters.

### Adapter-kind lookup

The seat-port doesn't currently expose a "kind" on adapter instances. Either:

- **A.** Add `SeatAdapter.kind: Literal["human", "bot", "canned"]` to the protocol. Each concrete adapter sets it (`HumanAdapter.kind = "human"`, etc.).
- **B.** Do `isinstance(adapter, HumanAdapter)` at the call site.

Option A is cleaner — bot-runner integration (Layer 9) will want this anyway. Spec it as a one-line protocol addition.

## Worked example

User-visible flow with the new defaults (`MAHJONG_DECIDE_TIMEOUT_HUMAN_DISCARD_S=60`):

1. Server emits `PROMPT { prompt_id, kind: "DISCARD", deadline_ms: now + 60_000, ... }` to alice (seat 0, human).
2. Alice reads her hand, contemplates which tile to play. After 35 seconds she clicks a tile.
3. UI sends `ACTION { prompt_id, action }`. Server applies. No timeout, no strike.

Compare to today's flow: at 30 seconds the server times out, picks alice's default action (the just-drawn tile), strikes her. After three turns of that pattern, alice's adapter is replaced with `AutoPassAdapter` and she literally watches the hand finish without being able to play.

Bot-seat flow is unchanged: CannedAdapter decides synchronously, the 30s `bot_s` budget is effectively unused, no behavioral difference.

## Alternatives considered

- **Bump the single global default to 60s.** Simpler but punishes CI: every test that drives a real hand pays an extra 30s of timeout budget in the worst case. The bot/human split costs nothing in CI.
- **Make the human-claim timeout very short (e.g., 5s) for urgency.** 5s is too tight for a human who has to look at the discarded tile and consider HU.  20s is the working-answer middle ground; revisit after the cardinal-UI lands (a clearer turn arrow may make sub-10s feasible).
- **Per-account override stored in `accounts` table.** Real feature, out of scope. Server-level config is the v1 unit.
- **Heartbeat-based deadline extension.** The proper fix for "the user is engaged, give them more time" but requires wire-protocol changes (`PROMPT_HEARTBEAT`), client UI signals (typing / clicking), and server timer reset. Tracked as a follow-up; not v1.

## Verification fixtures

1. **Human DISCARD timeout uses `human_discard_s`.**  Construct a `TableHandle` with `decide_timeouts.human_discard_s = 0.2`, a `HumanAdapter` whose `decide` never resolves, and a `CannedAdapter` opponent.  The manager auto-defaults at 0.2s ± fudge, not at 30s.
2. **Human CLAIM timeout uses `human_claim_s`.**  Same shape but for CLAIM_WINDOW; setting `human_claim_s = 0.2` causes the human's claim decision to auto-PASS after 0.2s.
3. **Bot seats keep `bot_s` regardless of prompt kind.**  With `bot_s = 0.2, human_discard_s = 60, human_claim_s = 20`, a CannedAdapter that never returns is timed out at 0.2s.  No human-seat timeout bleed.
4. **Default behaviour matches the new env-var defaults.**  `load_config_from_env({})` returns `decide_timeout_human_discard_s == 60`, etc.  Existing tests passing `decide_timeout_seconds=30.0` as a single value continue to work via the back-compat shim.
5. **`HumanAdapter.kind == "human"`, `CannedAdapter.kind == "canned"`, `AutoPassAdapter.kind == "canned"`.**  Property-level pin so the manager's lookup is unambiguous.
6. **Strike system unchanged.**  Three consecutive timeouts on a human seat still trigger `_maybe_swap_to_autopass`; the seat's adapter becomes AutoPassAdapter; subsequent prompts return defaults immediately.  Pin via the existing strike-test pattern but with the new timeouts.

## Open questions

- **Should the wire `PROMPT.deadline_ms` show the *server* deadline or a UI-friendly "fast" deadline?**  Working answer: the real server deadline. Client UI may want to render a less-alarmist countdown (e.g., a soft 30s yellow zone before the 60s hard cutoff), but the truth on the wire is the server's enforcement deadline.
- **How does the per-prompt-kind split interact with future `BotRunnerAdapter` (Layer 9)?**  Working answer: `BotRunnerAdapter.kind = "bot"` uses `bot_s` for everything; per-prompt-kind splits don't apply to bots until we have evidence they need to.
- **Should HU prompts get their own deadline?**  Working answer: no — HU is one of the actions inside the existing DISCARD / CLAIM kinds, not a separate prompt kind. Same budget.

## Cross-spec impact

- [seat-port.md](seat-port.md) — adds `SeatAdapter.kind: Literal["human", "bot", "canned"]` to the protocol.  Existing adapters set the literal; the field is read-only at the manager.
- [server-lifecycle.md](server-lifecycle.md) — three new env vars; updates the §Configuration table.
- [wire-protocol.md](wire-protocol.md) — no schema change; `PROMPT.deadline_ms` semantics are unchanged (server's real deadline).
- [implementation-order.md](implementation-order.md) — schedule alongside or immediately after the cardinal-UI work; together they fix the "I keep losing my turn" UX defect surfaced 2026-05-26.
