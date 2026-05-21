"""Engine transition -> record events.

Spec: docs/specs/record-format.md § Event catalog.

`diff_to_events` is engine-pure: it derives only what the state transition
itself reveals. Runtime metadata (`decision_ms`, identities, server info)
is layered in by the table-manager caller and never derived here.
"""

from __future__ import annotations

from typing import Any, cast

from mahjong.engine.hashing import canonical_hash
from mahjong.engine.legality.claim import claim_actions
from mahjong.engine.types import Action, GameState


def diff_to_events(
    state_before: GameState,
    seat: int,
    action: Action,
    state_after: GameState,
    *,
    ts: str,
) -> list[dict[str, Any]]:
    """Return record event payloads for `(state_before, action) -> state_after`.

    Each returned dict carries `event`, `turn_index`, `phase`, `ts`. `seq` is
    assigned later by the writer. Caller may merge runtime metadata
    (`decision_ms`, etc.) into individual events before writing.

    Emission rules:
    - PLAY    -> DISCARD (+ CLAIM_WINDOW if state_after.phase == CLAIM_WINDOW,
                          + DRAW if engine auto-advanced into the next discard)
    - PASS    -> CLAIM_DECISION (+ resolution / DRAW when window closes)
    - PENG    -> CLAIM_DECISION + CLAIM_RESOLUTION(CLAIMED)
    - CHI     -> CLAIM_DECISION + CLAIM_RESOLUTION(CLAIMED)
    - GANG    -> CLAIM_DECISION + CLAIM_RESOLUTION (claim variants) or single
                 standalone event for CONCEALED / ADDED gangs
    - HU      -> HAND_END
    """
    events: list[dict[str, Any]] = []
    t = action["type"]

    if t == "PLAY":
        events.append(_discard_event(state_after, seat, action, ts))
        _maybe_append_window_or_draw(events, state_before, state_after, ts)
    elif t == "PASS":
        events.append(_claim_decision_event(state_after, seat, action, ts))
        _maybe_append_window_close(events, state_before, state_after, ts)
    elif t in ("PENG", "CHI"):
        events.append(_claim_decision_event(state_after, seat, action, ts))
        events.append(_claim_resolution_claimed(state_after, seat, action, ts))
    elif t == "GANG":
        events.append(_gang_event(state_before, state_after, seat, action, ts))
    elif t == "HU":
        events.append(_hand_end_event(state_after, ts))
    else:  # pragma: no cover — exhaustive
        raise ValueError(f"unknown action type: {t!r}")

    return events


# --- per-event constructors ---


def _discard_event(state_after: GameState, seat: int, action: Action, ts: str) -> dict[str, Any]:
    return {
        "event": "DISCARD",
        "turn_index": state_after["turn_index"],
        "phase": state_after["phase"],
        "ts": ts,
        "seat": seat,
        "tile": action["tile"],  # type: ignore[typeddict-item]
        # from_hand is True for every discard our engine emits: the engine
        # places drawn tiles into concealed before the discard prompt, so it
        # cannot distinguish tsumogiri. When state grows a `last_drawn` field
        # (see project_layer2_self_draw_win_tile memory) this becomes derivable.
        "from_hand": True,
    }


def _claim_decision_event(
    state_after: GameState, seat: int, action: Action, ts: str
) -> dict[str, Any]:
    t = action["type"]
    payload: dict[str, Any] = {
        "event": "CLAIM_DECISION",
        "turn_index": state_after["turn_index"],
        "phase": state_after["phase"],
        "ts": ts,
        "seat": seat,
        "decision": t,
    }
    if t == "CHI":
        payload["chi_tiles"] = list(action["tiles"])  # type: ignore[typeddict-item]
    elif t == "PENG":
        payload["tile"] = action["tile"]  # type: ignore[typeddict-item]
    elif t == "GANG":
        payload["tile"] = action["tile"]  # type: ignore[typeddict-item]
        payload["kind"] = action["kind"]  # type: ignore[typeddict-item]
    return payload


def _claim_resolution_claimed(
    state_after: GameState, seat: int, action: Action, ts: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": "CLAIM_RESOLUTION",
        "turn_index": state_after["turn_index"],
        "phase": state_after["phase"],
        "ts": ts,
        "outcome": "CLAIMED",
        "winning_seat": seat,
        "winning_claim": action["type"],
    }
    if action["type"] == "CHI":
        payload["winning_chi_tiles"] = list(action["tiles"])
    return payload


def _claim_resolution_passed(state_after: GameState, ts: str) -> dict[str, Any]:
    return {
        "event": "CLAIM_RESOLUTION",
        "turn_index": state_after["turn_index"],
        "phase": state_after["phase"],
        "ts": ts,
        "outcome": "PASSED",
    }


def _gang_event(
    state_before: GameState, state_after: GameState, seat: int, action: Action, ts: str
) -> dict[str, Any]:
    """Exposed kong = a claim from CLAIM_WINDOW; concealed/added kongs are
    self-initiated from DISCARD phase. The record format emits a
    CLAIM_DECISION for the claim variant and a primary event (here, also a
    CLAIM_DECISION-shaped event) for self-initiated variants — collapsing
    both onto CLAIM_DECISION keeps the event vocabulary stable; future
    schema work can split them if reads need it."""
    return _claim_decision_event(state_after, seat, action, ts)


def _hand_end_event(state_after: GameState, ts: str) -> dict[str, Any]:
    terminal = state_after["terminal"]
    assert terminal is not None, "HU action must produce a terminal state"
    winner = terminal["winner"]
    winners_list: list[int] = [winner] if winner is not None else []
    return {
        "event": "HAND_END",
        "turn_index": state_after["turn_index"],
        "phase": state_after["phase"],
        "ts": ts,
        "kind": terminal["kind"],
        "winner": winners_list,
        "win_tile": terminal["win_tile"],
        "win_type": terminal["win_type"],
        "deal_in_seat": terminal["deal_in_seat"],
        "fan": [dict(f) for f in terminal["fan"]],
        "fan_total": terminal["fan_total"],
        "score_delta": list(terminal["score_delta"]),
        "final_hands": [
            {
                "seat": s["seat"],
                "concealed": list(s["concealed"]),
                "melds": [dict(m) for m in s["melds"]],
                "flowers": list(s["flowers"]),
            }
            for s in state_after["seats"]
        ],
        "state_hash": canonical_hash(cast(dict[str, Any], state_after)),
    }


# --- helpers for engine-internal follow-on events ---


def _maybe_append_window_or_draw(
    events: list[dict[str, Any]],
    state_before: GameState,
    state_after: GameState,
    ts: str,
) -> None:
    if state_after["phase"] == "CLAIM_WINDOW":
        events.append(_claim_window_event(state_after, ts))
        return
    if (
        state_after["phase"] == "DISCARD"
        and state_after["wall"]["drawn_count"] > state_before["wall"]["drawn_count"]
    ):
        events.append(_draw_event(state_after, ts))
        return
    if state_after["phase"] == "TERMINAL":
        events.append(_hand_end_event(state_after, ts))


def _maybe_append_window_close(
    events: list[dict[str, Any]],
    state_before: GameState,
    state_after: GameState,
    ts: str,
) -> None:
    """If the PASS closed the claim window, emit CLAIM_RESOLUTION(PASSED)
    and a DRAW for the seat the engine advanced to."""
    if state_before["phase"] != "CLAIM_WINDOW":
        return
    if state_after["phase"] == "CLAIM_WINDOW":
        return  # window still open, more PASSes to come
    events.append(_claim_resolution_passed(state_after, ts))
    if (
        state_after["phase"] == "DISCARD"
        and state_after["wall"]["drawn_count"] > state_before["wall"]["drawn_count"]
    ):
        events.append(_draw_event(state_after, ts))
        return
    if state_after["phase"] == "TERMINAL":
        events.append(_hand_end_event(state_after, ts))


def _claim_window_event(state_after: GameState, ts: str) -> dict[str, Any]:
    """Build a CLAIM_WINDOW event from pending_claims, expanding via
    `claim_actions` so the `opportunities` list is the full legal set."""
    opportunities: list[dict[str, Any]] = []
    last = state_after["last_discard"]
    if last is not None:
        tile = last["tile"]
        for seat_idx in range(4):
            if seat_idx == last["seat"]:
                continue
            for action in claim_actions(state_after, seat_idx):
                if action["type"] == "PASS":
                    continue
                opp: dict[str, Any] = {"seat": seat_idx, "claim": action["type"]}
                if action["type"] == "CHI":
                    opp["chi_tiles"] = list(action["tiles"])
                else:
                    opp["tile"] = tile
                opportunities.append(opp)
    return {
        "event": "CLAIM_WINDOW",
        "turn_index": state_after["turn_index"],
        "phase": state_after["phase"],
        "ts": ts,
        "opportunities": opportunities,
    }


def _draw_event(state_after: GameState, ts: str) -> dict[str, Any]:
    actor = state_after["current_actor"]
    concealed = state_after["seats"][actor]["concealed"]
    drawn_tile = concealed[-1] if concealed else None
    return {
        "event": "DRAW",
        "turn_index": state_after["turn_index"],
        "phase": state_after["phase"],
        "ts": ts,
        "seat": actor,
        "tile": drawn_tile,
        "flower_replacements": [],
    }


__all__ = ["diff_to_events"]
