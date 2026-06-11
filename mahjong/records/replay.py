"""Record replay: reconstruct the canonical GameState sequence.

Spec: docs/specs/record-format.md § Goals (replayable), § Verification fixture 2.

Strategy: re-deal from `HEADER.seed` **and the recorded dealer** via
`initial_state` (the engine is pure and deterministic, so the deal is
reproducible), then re-apply exactly the actions the table manager applied at
runtime. The dealer matters: every hand after a match's first rotates it, which
rotates the seat winds and the starting actor — replaying with the default
``dealer_seat=0`` made the first recorded discard illegal (DEF-11).

Caveat — records are only replayable against the *rule engine* that wrote them.
A record produced before a scoring/legality change (e.g. the FB-09 concealed-fan
fix) can carry a HU that the corrected engine refuses (it now scores below the
ruleset's fan floor); that is an engine-version mismatch, not a replay defect.

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
    # The deal depends on the dealer, not just the seed: any hand after the
    # match's first rotates the dealer, which rotates the seat winds and the
    # starting actor. ``initial_state``'s default ``dealer_seat=0`` only
    # reconstructs the first hand; for the rest we recover the dealer (the seat
    # assigned East / F1) from the HEADER, or the first recorded discard lands
    # on the wrong actor and raises IllegalAction.
    #
    # We mirror `run_hand`'s exact deal call (`manager.py`:
    # ``initial_state(ruleset, seed, dealer_seat=...)``) — note it does *not*
    # pass ``hand_index``, so every recorded state carries ``hand_index=0`` even
    # though the HEADER's ``hand_index_in_match`` is non-zero for later hands.
    # That field is orchestrator metadata that never reaches the engine state;
    # trusting it here would make the reconstructed state diverge from the one
    # that produced the record.
    state = initial_state(ruleset, seed=seed, dealer_seat=_dealer_seat_from_header(header))
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


def _dealer_seat_from_header(header: dict[str, Any]) -> int:
    """The dealer is the seat assigned East (F1); seat winds rotate with it.

    Mirrors the live writer (`registry.py`: ``wind = F{(seat-dealer)%4+1}``),
    so the dealer is the unique seat with wind ``"F1"``. Falls back to 0 (the
    hand-0 default) for legacy headers that predate per-hand wind recording.
    """
    for seat in header.get("seats", []):
        if seat.get("wind") == "F1":
            return int(seat["seat"])
    return 0


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
