"""Record replay: reconstruct the canonical GameState sequence.

Spec: docs/specs/record-format.md § Goals (replayable), § Verification fixture 2.

Strategy: re-deal from `HEADER.seed` via `initial_state` (the engine is pure
and deterministic, so the deal is reproducible), then re-apply exactly the
actions the table manager applied at runtime.

The subtlety is **claim windows and terminal wins**: the record's event
vocabulary doesn't map one-to-one onto applied actions, and a window closes via
a *different* event depending on how it resolved:

- **own-turn action** (PLAY; CONCEALED/ADDED kong) — one applied action, recorded
  as DISCARD or a GANG-shaped CLAIM_DECISION. Applied immediately.
- **claim window** — records a CLAIM_DECISION for *every* eligible seat, but the
  manager applied only the resolution. The closer differs by outcome:
    - all-PASS            → `CLAIM_RESOLUTION(PASSED)`; apply each PASS.
    - winning PENG / CHI  → `CLAIM_RESOLUTION(CLAIMED, winning_seat)`; apply it.
    - winning EXPOSED kong → *no* CLAIM_RESOLUTION (gangs don't emit one); the
      replacement `DRAW` is the closer. The kong is the winner.
    - winning HU (a ron)  → `HAND_END`; apply the HU.
- **self-draw win** — `HAND_END` with no preceding window. A **DRAW terminal**
  carries an empty `HAND_END.winner` and was already reached inside the last
  applied PLAY/PASS, so there is nothing to apply.

Replaying every recorded CLAIM_DECISION — the naive reading — double-applies
losers and walks into a closed window, so we buffer a window's decisions and
apply only what the manager applied at the matching closer.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from mahjong.engine import apply_action, initial_state
from mahjong.engine.types import Action, GameState, RuleSetRef

# Purely informational events that never drive an apply (DRAW is handled
# explicitly below — a post-kong replacement draw closes a claim window).
_INFO_EVENTS = frozenset({"HEADER", "DEAL", "CLAIM_WINDOW", "FOOTER"})
# Gang kinds that are own-turn (applied immediately) vs. EXPOSED (a claim).
_OWN_TURN_GANG_KINDS = frozenset({"CONCEALED", "ADDED"})


def replay(events: list[dict[str, Any]]) -> Iterator[GameState]:
    """Yield the GameState sequence corresponding to `events`.

    First yielded state is the post-deal state from `initial_state`. Each
    *applied* action advances the state by one `apply_action` call and yields.
    """
    header = events[0]
    ruleset: RuleSetRef = header["ruleset"]
    seed = int(header["seed"])
    state = initial_state(ruleset, seed=seed)
    yield state

    window: list[dict[str, Any]] = []  # buffered CLAIM_DECISIONs for the open window

    for event in events[1:]:
        kind = event["event"]

        if kind == "DISCARD":
            seat, action = _event_to_action(event)
            state = apply_action(state, seat, action)
            yield state

        elif kind == "CLAIM_DECISION":
            if event.get("decision") == "GANG" and event.get("kind") in _OWN_TURN_GANG_KINDS:
                # Concealed / added kong: own-turn, applied immediately (it draws
                # a replacement internally and returns to the same seat's discard).
                seat, action = _event_to_action(event)
                state = apply_action(state, seat, action)
                yield state
            else:
                window.append(event)  # claim-window decision: resolved at the closer

        elif kind == "DRAW":
            # A replacement draw with an open window closes an EXPOSED-kong claim
            # (kongs emit no CLAIM_RESOLUTION); the kong is the winner. A normal
            # start-of-turn draw has an empty window and is informational.
            if window:
                winner = _only(d for d in window if d.get("decision") == "GANG")
                seat, action = _event_to_action(winner)
                state = apply_action(state, seat, action)
                yield state
                window = []

        elif kind == "CLAIM_RESOLUTION":
            if event["outcome"] == "PASSED":
                for decision in window:  # all-pass: the manager applied each PASS
                    seat, action = _event_to_action(decision)
                    state = apply_action(state, seat, action)
                    yield state
            else:  # CLAIMED — apply only the winning PENG/CHI.
                winner = _only(d for d in window if d["seat"] == event["winning_seat"])
                seat, action = _event_to_action(winner)
                state = apply_action(state, seat, action)
                yield state
            window = []

        elif kind == "HAND_END":
            winners = event["winner"]
            if winners:
                # A win (HU): off a discard it closes the buffered window; a
                # self-draw has no window. Either way apply the winner's HU and
                # drop any buffered losing decisions.
                state = apply_action(state, winners[0], {"type": "HU"})
                yield state
            window = []  # DRAW terminal: empty winner, terminal already reached

        elif kind in _INFO_EVENTS:
            continue

        else:  # pragma: no cover — exhaustive over the record vocabulary
            raise ValueError(f"unexpected event during replay: {kind!r}")


def _only(matches: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """The single matching buffered decision (the window's winner)."""
    it = iter(matches)
    try:
        return next(it)
    except StopIteration:  # pragma: no cover — record/manager invariant
        raise ValueError("claim window closed with no matching winning decision") from None


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
