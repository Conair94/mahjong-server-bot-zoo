"""Table manager: drives one hand with four SeatAdapters.

Spec: docs/specs/seat-port.md § Lifecycle and concurrency model, § Error model.

The manager owns the asyncio loop, the record writer, and the per-seat strike
counter. The engine owns the rules; the adapters own decisions. The manager
is the seam.

Public surface is `run_hand(...)`. Helpers are private.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mahjong.adapters.autopass import AutoPassAdapter
from mahjong.adapters.base import (
    AdapterKind,
    LeaveReason,
    Prompt,
    PromptKind,
    SeatAdapter,
    SeatContext,
)
from mahjong.engine import apply_action, initial_state, is_terminal, legal_actions
from mahjong.engine import state as state_module
from mahjong.engine.types import Action, GameState, RuleSetRef, SeatView
from mahjong.records.diff import diff_to_events
from mahjong.records.writer import RecordWriter

# Failure-mode marker returned alongside the chosen Action from a decide call.
# Empty dict means the adapter's response was accepted as-is.
FailureMeta = dict[str, Any]


@dataclasses.dataclass(frozen=True)
class DecideTimeouts:
    """Per-(seat-kind, prompt-kind) decide deadline table.

    Spec: docs/specs/human-decide-timeout.md § The schema / interface.

    Non-human seats use ``bot_s`` for every prompt kind. Human seats split
    on prompt kind: DISCARD prompts get the longer ``human_discard_s``;
    CLAIM prompts get the shorter ``human_claim_s`` (claim windows block
    all four seats, so a generous per-claim deadline stalls the table).
    """

    human_discard_s: float
    human_claim_s: float
    bot_s: float

    def for_(self, seat_kind: AdapterKind, prompt_kind: PromptKind) -> float:
        if seat_kind != "human":
            return self.bot_s
        if prompt_kind == "DISCARD":
            return self.human_discard_s
        return self.human_claim_s

    @classmethod
    def uniform(cls, seconds: float) -> DecideTimeouts:
        """Back-compat shim: one value applies everywhere (mirrors the
        pre-spec-19 single ``decide_timeout_seconds`` parameter)."""
        return cls(human_discard_s=seconds, human_claim_s=seconds, bot_s=seconds)

# Optional per-event hook the orchestrator can pass to fan record events to
# additional consumers (e.g. spectators) without going through the adapter
# list. Fires once per event, AFTER the adapter fanout completes. Errors and
# timeouts are swallowed — same independence guarantee as adapter observe.
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _now_ts() -> str:
    return (
        datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{datetime.now(UTC).microsecond // 1000:03d}Z"
    )


def _default_action(state: GameState, seat: int, prompt_kind: PromptKind) -> Action:
    """Centralized default-action selection (seat-port.md § Prompt).

    DISCARD: tsumogiri (play the just-drawn tile).
    CLAIM:   PASS.
    """
    if prompt_kind == "CLAIM":
        return {"type": "PASS"}
    last_drawn = state["last_drawn"]
    if last_drawn is not None and last_drawn["seat"] == seat:
        return {"type": "PLAY", "tile": last_drawn["tile"]}
    # No last_drawn (shouldn't happen in DISCARD phase, but pick a deterministic
    # fallback rather than crashing): the first legal PLAY.
    plays = [a for a in legal_actions(state, seat) if a["type"] == "PLAY"]
    assert plays, "DISCARD phase with no legal PLAY — engine bug"
    return cast(Action, plays[0])


def _build_prompt(
    state: GameState, seat: int, kind: PromptKind, *, deadline_seconds: float
) -> Prompt:
    now = asyncio.get_event_loop().time()
    return {
        "kind": kind,
        "view": state_module.project(state, seat),
        "legal_actions": legal_actions(state, seat),
        "default_action": _default_action(state, seat, kind),
        "deadline": now + deadline_seconds,
        "issued_at": now,
        "context": {"turn_index": state["turn_index"], "phase": state["phase"]},
    }


async def _seated_with_timeout(adapter: SeatAdapter, ctx: SeatContext, seconds: float) -> None:
    try:
        await asyncio.wait_for(adapter.seated(ctx), timeout=seconds)
    except (TimeoutError, Exception):
        # Per seat-port.md, a failing `seated` causes the seat to be replaced
        # by AutoPassAdapter. The replacement is handled by the caller; here
        # we just swallow so gather doesn't fail-fast.
        return


async def _fanout_observe(
    adapters: list[SeatAdapter],
    state: GameState,
    event: dict[str, Any],
    *,
    per_observe_seconds: float,
    event_callback: EventCallback | None = None,
) -> None:
    """Push `event` to every seat's observe with a short per-call deadline,
    then optionally fire `event_callback(event)` for non-adapter consumers
    (spectators, audit log, etc.).

    Independence: one slow adapter doesn't block the others — each await is
    its own task with its own timeout (seat-port.md fixture 6). The
    `event_callback` runs after the adapter gather completes and is bounded
    by the same per-call deadline; errors are swallowed.
    """

    async def one(seat: int) -> None:
        try:
            view = (
                state_module.project(state, seat)
                if state["phase"] != "TERMINAL"
                else cast(SeatView, {})
            )
            await asyncio.wait_for(adapters[seat].observe(event, view), timeout=per_observe_seconds)
        except (TimeoutError, Exception):
            return

    await asyncio.gather(*(one(s) for s in range(4)))
    if event_callback is not None:
        try:
            await asyncio.wait_for(event_callback(event), timeout=per_observe_seconds)
        except (TimeoutError, Exception):
            return


async def run_hand(
    *,
    adapters: list[SeatAdapter],
    ruleset: RuleSetRef,
    seed: int,
    hand_id: str,
    record_path: Path,
    server_info: dict[str, Any],
    decide_timeout_seconds: float = 30.0,
    decide_timeouts: DecideTimeouts | None = None,
    observe_timeout_seconds: float = 0.5,
    seated_timeout_seconds: float = 1.0,
    strike_limit: int = 3,
    meta: dict[str, Any] | None = None,
    event_callback: EventCallback | None = None,
    dealer_seat: int = 0,
    hand_index_in_match: int = 0,
) -> GameState:
    """Drive one hand from initial deal to TERMINAL.

    Side effects: writes a complete record to `record_path`. Returns the
    final canonical GameState (for tests; live callers can re-read the
    record if they need it).

    Layer-8 params:
    - ``dealer_seat``: which seat is the current dealer (0-3). Seat winds
      rotate so ``dealer_seat`` is East (F1). Also sets the engine's starting
      actor to ``dealer_seat``.  Default 0 preserves backwards compatibility.
    - ``hand_index_in_match``: zero-based index of this hand within its match.
      Written to the HEADER record. Default 0 for standalone hands.
    - ``decide_timeouts``: per-(seat-kind, prompt-kind) deadline table (spec
      19). When ``None``, falls back to a uniform ``decide_timeout_seconds``
      across every adapter / prompt — the pre-spec-19 behaviour.
    """
    if len(adapters) != 4:
        raise ValueError(f"expected 4 adapters, got {len(adapters)}")

    if decide_timeouts is None:
        decide_timeouts = DecideTimeouts.uniform(decide_timeout_seconds)

    state = initial_state(ruleset, seed=seed, dealer_seat=dealer_seat)
    writer = RecordWriter(record_path)

    # --- HEADER ---
    # Seat winds rotate with the dealer: dealer_seat = East (F1),
    # the next seat clockwise = South (F2), etc.
    # Wind index for seat s: (s - dealer_seat) % 4, 1-based → F1..F4.
    header: dict[str, Any] = {
        "event": "HEADER",
        "turn_index": 0,
        "phase": "DEAL",
        "ts": _now_ts(),
        "format_version": 1,
        "hand_id": hand_id,
        "match_id": None,
        "hand_index_in_match": hand_index_in_match,
        "ruleset": dict(ruleset),
        "seed": str(seed),
        "seats": [
            {
                "seat": i,
                "wind": f"F{(i - dealer_seat) % 4 + 1}",
                "identity": dict(adapters[i].identity),
            }
            for i in range(4)
        ],
        "server": dict(server_info),
    }
    if meta is not None:
        header["meta"] = dict(meta)
    writer.write_event(header)

    # --- seated ---
    contexts: list[SeatContext] = [
        {
            "seat": i,
            "hand_id": hand_id,
            "ruleset": ruleset,
            "seat_deadline_ms": int(seated_timeout_seconds * 1000),
            "initial_view": state_module.project(state, i),
        }
        for i in range(4)
    ]
    await asyncio.gather(
        *(_seated_with_timeout(adapters[i], contexts[i], seated_timeout_seconds) for i in range(4))
    )

    strikes = [0] * 4

    # --- main loop ---
    while not is_terminal(state):
        if state["phase"] == "DISCARD":
            state = await _step_discard(
                state,
                adapters,
                writer,
                decide_timeouts,
                observe_timeout_seconds,
                strikes,
                strike_limit,
                event_callback,
            )
        elif state["phase"] == "CLAIM_WINDOW":
            state = await _step_claim_window(
                state,
                adapters,
                writer,
                decide_timeouts,
                observe_timeout_seconds,
                strikes,
                strike_limit,
                event_callback,
            )
        else:
            raise AssertionError(f"unexpected phase in run_hand: {state['phase']!r}")

    # --- left ---
    await asyncio.gather(
        *(_safe_left(a, "HAND_ENDED") for a in adapters),
        return_exceptions=True,
    )

    # --- FOOTER ---
    writer.close_with_footer(
        turn_index=state["turn_index"],
        phase=state["phase"],
        ts=_now_ts(),
        rng_cursor_final=state["rng"]["cursor"],
        state_hash_final=state_module.state_hash(state),
        corrects=None,
    )
    return state


async def _safe_left(adapter: SeatAdapter, reason: LeaveReason) -> None:
    try:
        await adapter.left(reason)
    except Exception:
        return


# --- Per-phase steppers ---


async def _step_discard(
    state: GameState,
    adapters: list[SeatAdapter],
    writer: RecordWriter,
    decide_timeouts: DecideTimeouts,
    observe_timeout_seconds: float,
    strikes: list[int],
    strike_limit: int,
    event_callback: EventCallback | None,
) -> GameState:
    actor = state["current_actor"]
    deadline_seconds = decide_timeouts.for_(adapters[actor].kind, "DISCARD")
    prompt = _build_prompt(state, actor, "DISCARD", deadline_seconds=deadline_seconds)
    action, failure = await _decide_or_default(adapters[actor], prompt)
    if failure:
        strikes[actor] += 1
    if isinstance(adapters[actor], AutoPassAdapter):
        failure = {**failure, "auto_pass": True}

    state_before = state
    state = apply_action(state, actor, action)
    events = diff_to_events(state_before, actor, action, state, ts=_now_ts())
    if failure and events:
        events[0].update(failure)
    for event in events:
        writer.write_event(event)
        await _fanout_observe(
            adapters,
            state,
            event,
            per_observe_seconds=observe_timeout_seconds,
            event_callback=event_callback,
        )

    _maybe_swap_to_autopass(adapters, actor, strikes, strike_limit)
    return state


async def _step_claim_window(
    state: GameState,
    adapters: list[SeatAdapter],
    writer: RecordWriter,
    decide_timeouts: DecideTimeouts,
    observe_timeout_seconds: float,
    strikes: list[int],
    strike_limit: int,
    event_callback: EventCallback | None,
) -> GameState:
    """Resolve one CLAIM_WINDOW with MCR priority (HU > PENG/GANG > CHI).

    Adapters are prompted in parallel via asyncio.gather (fixture 7: the
    outcome is invariant to which adapter returns first). Decisions are
    resolved by claim-type priority, with seat order as the tiebreak.
    Every claimer's submission is recorded as a CLAIM_DECISION event, even
    losers' — that's the defense-training signal that bare action logs
    throw away (record-format.md § CLAIM_DECISION).
    """
    claimers = sorted({c["seat"] for c in state["pending_claims"]})
    prompts = {
        seat: _build_prompt(
            state,
            seat,
            "CLAIM",
            deadline_seconds=decide_timeouts.for_(adapters[seat].kind, "CLAIM"),
        )
        for seat in claimers
    }
    results: list[tuple[Action, FailureMeta]] = list(
        await asyncio.gather(
            *(_decide_or_default(adapters[seat], prompts[seat]) for seat in claimers)
        )
    )
    seat_results: dict[int, tuple[Action, FailureMeta]] = dict(zip(claimers, results, strict=True))

    for seat, (_, failure) in seat_results.items():
        if failure:
            strikes[seat] += 1

    winner = _resolve_claim_priority(claimers, seat_results)

    if winner is None:
        # All PASS path: apply sequentially. Engine clears each entry; events
        # come through diff_to_events normally.
        state = await _apply_all_pass(
            state,
            claimers,
            seat_results,
            adapters,
            writer,
            observe_timeout_seconds,
            event_callback,
        )
    else:
        winner_seat, winner_action = winner
        # Emit a CLAIM_DECISION for every claimer in seat order — the
        # engine will only see the winner, but the record captures all.
        for seat in claimers:
            action, failure = seat_results[seat]
            event = _make_decision_event(state, seat, action, failure, adapters[seat])
            writer.write_event(event)
            await _fanout_observe(
                adapters,
                state,
                event,
                per_observe_seconds=observe_timeout_seconds,
                event_callback=event_callback,
            )

        # Apply the winner. PENG/CHI/GANG re-emit the winner's CLAIM_DECISION as
        # events[0]; we already wrote it in the loop above, so drop that
        # duplicate. A winning HU, however, emits *only* HAND_END (no leading
        # CLAIM_DECISION) — slicing it off would silently drop the terminal
        # event from the record and the fanout, leaving every client waiting
        # forever (the table stalls on any discard-win / ron).
        winner_failure = seat_results[winner_seat][1]
        state_before = state
        state = apply_action(state, winner_seat, winner_action)
        events = diff_to_events(state_before, winner_seat, winner_action, state, ts=_now_ts())
        if events and events[0]["event"] == "CLAIM_DECISION":
            events = events[1:]
        if winner_failure and events:
            events[0].update(winner_failure)
        for event in events:
            writer.write_event(event)
            await _fanout_observe(
                adapters,
                state,
                event,
                per_observe_seconds=observe_timeout_seconds,
                event_callback=event_callback,
            )

    for seat in claimers:
        _maybe_swap_to_autopass(adapters, seat, strikes, strike_limit)
    return state


_PRIORITY_ORDER: tuple[str, ...] = ("HU", "PENG", "GANG", "CHI")


def _resolve_claim_priority(
    claimers: list[int], seat_results: dict[int, tuple[Action, FailureMeta]]
) -> tuple[int, Action] | None:
    """Return `(seat, action)` of the highest-priority non-PASS claim, or
    None if every claimer PASSed. Tiebreak: lower seat number."""
    for kind in _PRIORITY_ORDER:
        for seat in claimers:
            action, _ = seat_results[seat]
            if action["type"] == kind:
                return seat, action
    return None


def _make_decision_event(
    state: GameState,
    seat: int,
    action: Action,
    failure: FailureMeta,
    adapter: SeatAdapter,
) -> dict[str, Any]:
    """Construct a CLAIM_DECISION payload directly (without going through the
    engine). Used to record losers' submissions when a higher-priority claim
    takes the discard."""
    payload: dict[str, Any] = {
        "event": "CLAIM_DECISION",
        "turn_index": state["turn_index"],
        "phase": state["phase"],
        "ts": _now_ts(),
        "seat": seat,
        "decision": action["type"],
    }
    t = action["type"]
    if t == "CHI":
        payload["chi_tiles"] = list(cast(Any, action)["tiles"])
    elif t == "PENG":
        payload["tile"] = cast(Any, action)["tile"]
    elif t == "GANG":
        payload["tile"] = cast(Any, action)["tile"]
        payload["kind"] = cast(Any, action)["kind"]
    if failure:
        payload.update(failure)
    if isinstance(adapter, AutoPassAdapter):
        payload["auto_pass"] = True
    return payload


async def _apply_all_pass(
    state: GameState,
    claimers: list[int],
    seat_results: dict[int, tuple[Action, FailureMeta]],
    adapters: list[SeatAdapter],
    writer: RecordWriter,
    observe_timeout_seconds: float,
    event_callback: EventCallback | None,
) -> GameState:
    """All claimers PASSed — apply each in seat order; engine handles the
    advance. Per-event diff emission picks up CLAIM_RESOLUTION(PASSED) and
    the subsequent DRAW."""
    for seat in claimers:
        if state["phase"] != "CLAIM_WINDOW":
            break
        action, failure = seat_results[seat]
        if isinstance(adapters[seat], AutoPassAdapter):
            failure = {**failure, "auto_pass": True}
        state_before = state
        state = apply_action(state, seat, action)
        events = diff_to_events(state_before, seat, action, state, ts=_now_ts())
        if failure and events:
            events[0].update(failure)
        for event in events:
            writer.write_event(event)
            await _fanout_observe(
                adapters,
                state,
                event,
                per_observe_seconds=observe_timeout_seconds,
                event_callback=event_callback,
            )
    return state


def _maybe_swap_to_autopass(
    adapters: list[SeatAdapter], seat: int, strikes: list[int], strike_limit: int
) -> None:
    """Per seat-port.md § Error model: after `strike_limit` failures, the
    seat is replaced by an AutoPassAdapter for the remainder of the hand."""
    if strikes[seat] >= strike_limit and not isinstance(adapters[seat], AutoPassAdapter):
        adapters[seat] = cast(SeatAdapter, AutoPassAdapter())


async def _decide_or_default(adapter: SeatAdapter, prompt: Prompt) -> tuple[Action, FailureMeta]:
    """Centralized failure handler (seat-port.md `coerce_to_action_or_default`).

    Returns `(action, failure_meta)`. `failure_meta` is empty on success,
    or one of:
      - `{"timeout": True}`        — decide didn't return by the deadline
      - `{"illegal": True, "attempted_action": <action>}` — not in legal set
      - `{"crashed": True}`        — adapter raised
    """
    deadline = prompt["deadline"]
    remaining = max(0.0, deadline - asyncio.get_event_loop().time())
    try:
        action = await asyncio.wait_for(adapter.decide(prompt), timeout=remaining)
    except TimeoutError:
        return prompt["default_action"], {"timeout": True}
    except Exception:
        return prompt["default_action"], {"crashed": True}
    if action not in prompt["legal_actions"]:
        return prompt["default_action"], {"illegal": True, "attempted_action": dict(action)}
    return action, {}


__all__ = ["DecideTimeouts", "EventCallback", "run_hand"]
