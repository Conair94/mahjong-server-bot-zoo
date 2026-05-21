"""Record replay: reconstruct the canonical GameState sequence.

Spec: docs/specs/record-format.md § Goals (replayable), § Verification fixture 2.

Strategy: re-deal from `HEADER.seed` via `initial_state` (the engine is pure
and deterministic, so the deal is reproducible), then map each player-action
event back to an `Action` and apply via `apply_action`. Engine-internal events
(DRAW, CLAIM_WINDOW, CLAIM_RESOLUTION, DEAL, HAND_END) are *informational* —
they don't drive the replay; they were emitted by `diff_to_events` as a byproduct
of the same transitions the replay is about to compute.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from mahjong.engine import apply_action, initial_state
from mahjong.engine.types import Action, GameState, RuleSetRef

# Events that map back to a player action.
_PLAYER_ACTION_EVENTS = frozenset({"DISCARD", "CLAIM_DECISION"})
# Events that are emitted by engine-internal transitions (informational only).
_INFO_EVENTS = frozenset(
    {"HEADER", "DEAL", "DRAW", "CLAIM_WINDOW", "CLAIM_RESOLUTION", "HAND_END", "FOOTER"}
)


def replay(events: list[dict[str, Any]]) -> Iterator[GameState]:
    """Yield the GameState sequence corresponding to `events`.

    First yielded state is the post-deal state from `initial_state`. Each
    player-action event advances the state by one `apply_action` call.
    """
    header = events[0]
    ruleset: RuleSetRef = header["ruleset"]
    seed = int(header["seed"])
    state = initial_state(ruleset, seed=seed)
    yield state

    for event in events[1:]:
        kind = event["event"]
        if kind in _INFO_EVENTS:
            continue
        if kind not in _PLAYER_ACTION_EVENTS:
            raise ValueError(f"unexpected event during replay: {kind!r}")

        seat, action = _event_to_action(event)
        state = apply_action(state, seat, action)
        yield state


def _event_to_action(event: dict[str, Any]) -> tuple[int, Action]:
    kind = event["event"]
    seat: int = event["seat"]

    if kind == "DISCARD":
        return seat, {"type": "PLAY", "tile": event["tile"]}

    assert kind == "CLAIM_DECISION"
    decision = event["decision"]
    if decision == "PASS":
        return seat, {"type": "PASS"}
    if decision == "CHI":
        return seat, {"type": "CHI", "tiles": list(event["chi_tiles"])}
    if decision == "PENG":
        return seat, {"type": "PENG", "tile": event["tile"]}
    if decision == "GANG":
        return seat, {"type": "GANG", "tile": event["tile"], "kind": event["kind"]}
    if decision == "HU":
        return seat, {"type": "HU"}
    raise ValueError(f"unknown CLAIM_DECISION decision: {decision!r}")


__all__ = ["replay"]
