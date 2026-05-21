"""apply_action, decomposed by action type.

Spec: docs/specs/engine-api.md § Internal submodule layout, § Public API.

The dispatcher lives here; per-action transition logic lives in sibling
modules (`play.py`, `claim.py`, `gang.py`, `hu.py`, `pass_.py`, `draw.py`).
Shared helpers — deep-copy, claim-window opening, internal wall draw,
next-seat advancement — also live here, since they are not action-specific
and several transitions need them.
"""

from __future__ import annotations

import copy
from typing import Any, Literal, cast

from mahjong.engine.errors import IllegalAction
from mahjong.engine.hashing import canonical_hash
from mahjong.engine.legality import legal_actions
from mahjong.engine.legality.claim import claim_actions
from mahjong.engine.tiles import Tile, tile_sort_key
from mahjong.engine.types import Action, GameState, PendingClaim


def apply_action(state: GameState, seat: int, action: Action) -> GameState:
    """Return the state after applying `action` for `seat`.

    Raises `IllegalAction` if `action` is not in `legal_actions(state, seat)`.
    Pure: input state is never mutated.
    """
    legal = legal_actions(state, seat)
    if action not in legal:
        raise IllegalAction(
            state_hash=canonical_hash(cast(dict[str, Any], state)),
            seat=seat,
            attempted_action=cast(dict[str, Any], action),
            legal_actions=cast(list[dict[str, Any]], legal),
        )

    # Delayed imports to keep the per-action modules importable without
    # circular references through this dispatcher.
    from mahjong.engine.transition.claim import apply_chi, apply_peng
    from mahjong.engine.transition.gang import apply_gang
    from mahjong.engine.transition.hu import apply_hu
    from mahjong.engine.transition.pass_ import apply_pass
    from mahjong.engine.transition.play import apply_play

    t = action["type"]
    if t == "PLAY":
        return apply_play(state, seat, action)
    if t == "PASS":
        return apply_pass(state, seat)
    if t == "PENG":
        return apply_peng(state, seat, action)
    if t == "CHI":
        return apply_chi(state, seat, action)
    if t == "GANG":
        return apply_gang(state, seat, action)
    if t == "HU":
        return apply_hu(state, seat)
    raise IllegalAction(  # unreachable if legal_actions is honest
        state_hash=canonical_hash(cast(dict[str, Any], state)),
        seat=seat,
        attempted_action=cast(dict[str, Any], action),
        legal_actions=cast(list[dict[str, Any]], legal),
    )


# --- Shared helpers ---


def clone_state(state: GameState) -> GameState:
    """Deep copy. The engine is pure-functional; transitions return new states."""
    return copy.deepcopy(state)


def open_claim_window_or_advance(state: GameState) -> GameState:
    """After a discard, populate `pending_claims` from `last_discard`.

    If any seat has an opportunity, set `phase = CLAIM_WINDOW`. Otherwise
    immediately advance to the next seat's turn (internal draw + DISCARD).
    """
    last = state["last_discard"]
    if last is None:
        return state

    claims: list[PendingClaim] = []
    for seat_idx in range(4):
        if seat_idx == last["seat"]:
            continue
        for action in claim_actions(state, seat_idx):
            if action["type"] == "PASS":
                continue
            entry: PendingClaim = {"seat": seat_idx, "claim": _claim_kind(action)}
            if action["type"] == "CHI":
                entry["chi_tiles"] = list(action["tiles"])
            claims.append(entry)

    if claims:
        state["pending_claims"] = claims
        state["phase"] = "CLAIM_WINDOW"
        state["current_actor"] = (last["seat"] + 1) % 4
        return state

    return advance_to_next_seat_discard(state)


def _claim_kind(action: Action) -> Literal["HU", "PENG", "GANG", "CHI"]:
    t = action["type"]
    if t == "GANG":
        return "GANG"
    assert t in ("HU", "PENG", "CHI")
    return t


def advance_to_next_seat_discard(state: GameState) -> GameState:
    """Resolve a closed claim window: next seat (after discarder) draws + discards."""
    last = state["last_discard"]
    next_seat = (last["seat"] + 1) % 4 if last is not None else (state["current_actor"] + 1) % 4
    state["last_discard"] = None
    state["pending_claims"] = []
    return internal_draw(state, next_seat)


def internal_draw(state: GameState, seat: int) -> GameState:
    """Pop a tile from the wall front, route flowers, append to seat's concealed.

    Sets `phase = DISCARD`, `current_actor = seat`, `last_drawn = {seat, tile}`.
    If the wall is exhausted, transitions to TERMINAL with `kind = DRAW` and
    clears `last_drawn`.
    """
    wall = state["wall"]["remaining"]
    while wall:
        tile = wall.pop(0)
        state["wall"]["drawn_count"] += 1
        if tile.startswith("H"):
            state["seats"][seat]["flowers"].append(tile)
            continue
        seat_concealed = state["seats"][seat]["concealed"]
        seat_concealed.append(tile)
        seat_concealed.sort(key=tile_sort_key)
        state["phase"] = "DISCARD"
        state["current_actor"] = seat
        state["last_drawn"] = {"seat": seat, "tile": tile}
        return state

    # Wall exhausted with no live tile drawn → exhaustive draw.
    return make_exhaustive_draw_terminal(state)


def make_exhaustive_draw_terminal(state: GameState) -> GameState:
    state["phase"] = "TERMINAL"
    state["last_drawn"] = None
    state["terminal"] = {
        "kind": "DRAW",
        "winner": None,
        "win_tile": None,
        "win_type": None,
        "deal_in_seat": None,
        "fan": [],
        "fan_total": 0,
        "score_delta": [0, 0, 0, 0],
    }
    return state


def remove_one(tiles: list[Tile], target: Tile) -> None:
    """In-place remove one occurrence of `target` from `tiles`."""
    tiles.remove(target)


__all__ = ["apply_action"]
