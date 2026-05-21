"""diff_to_events: derive record events from an engine transition.

Spec: docs/specs/record-format.md § Event catalog. One test per action type.
"""

from __future__ import annotations

from typing import Any

from mahjong.engine import apply_action, initial_state, legal_actions
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import tile_sort_key
from mahjong.records.diff import diff_to_events

TS = "2026-05-20T22:00:00.000Z"
MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _seed_state() -> dict[str, Any]:
    return initial_state(MCR_REF, seed=12345)  # type: ignore[return-value]


def test_diff_play_emits_discard() -> None:
    s0 = _seed_state()
    actor = s0["current_actor"]
    play = next(a for a in legal_actions(s0, actor) if a["type"] == "PLAY")
    s1 = apply_action(s0, actor, play)  # type: ignore[arg-type]

    events = diff_to_events(s0, actor, play, s1, ts=TS)
    discard = next(e for e in events if e["event"] == "DISCARD")
    assert discard["seat"] == actor
    assert discard["tile"] == play["tile"]
    assert discard["ts"] == TS
    assert discard["turn_index"] == s1["turn_index"]
    assert discard["phase"] == s1["phase"]
    assert discard["from_hand"] is True


def test_diff_play_emits_claim_window_when_opportunities_exist() -> None:
    """If apply_action put state into CLAIM_WINDOW, emit a CLAIM_WINDOW event
    with the full opportunities list (defense-training-signal contract)."""
    s = _seed_state()
    steps = 0
    while s["phase"] == "DISCARD" and steps < 200:
        actor = s["current_actor"]
        plays = [a for a in legal_actions(s, actor) if a["type"] == "PLAY"]
        chosen = min(plays, key=lambda a: tile_sort_key(a["tile"]))
        s_before = s
        s_after = apply_action(s, actor, chosen)  # type: ignore[arg-type]
        if s_after["phase"] == "CLAIM_WINDOW":
            events = diff_to_events(s_before, actor, chosen, s_after, ts=TS)
            window = next(e for e in events if e["event"] == "CLAIM_WINDOW")
            assert window["opportunities"], "CLAIM_WINDOW must list opportunities"
            for opp in window["opportunities"]:
                assert opp["claim"] in {"HU", "PENG", "GANG", "CHI"}
                assert opp["seat"] in {0, 1, 2, 3}
            assert window["turn_index"] == s_after["turn_index"]
            return
        s = s_after
        steps += 1
    # Fallback: 4-PASS smoke from this seed eventually opens a claim window;
    # if it didn't in 200 steps, the seed semantics shifted and we want to know.
    raise AssertionError("seed 12345 did not open a CLAIM_WINDOW within 200 plays")


def test_diff_pass_emits_claim_decision() -> None:
    s = _seed_state()
    steps = 0
    while s["phase"] != "CLAIM_WINDOW" and steps < 200:
        actor = s["current_actor"]
        plays = [a for a in legal_actions(s, actor) if a["type"] == "PLAY"]
        s = apply_action(s, actor, min(plays, key=lambda a: tile_sort_key(a["tile"])))  # type: ignore[arg-type]
        steps += 1
    assert s["phase"] == "CLAIM_WINDOW"

    claimer = s["pending_claims"][0]["seat"]
    s_after = apply_action(s, claimer, {"type": "PASS"})  # type: ignore[arg-type]
    events = diff_to_events(s, claimer, {"type": "PASS"}, s_after, ts=TS)
    decision = next(e for e in events if e["event"] == "CLAIM_DECISION")
    assert decision["seat"] == claimer
    assert decision["decision"] == "PASS"


def test_diff_hu_emits_hand_end() -> None:
    """Dealer self-draws HU on a hand-crafted Big Three Dragons hand."""
    concealed_dealer = sorted(
        ["J1", "J1", "J1", "J2", "J2", "J2", "J3", "J3", "J3", "F1", "F1", "F1", "W1", "W1"],
        key=tile_sort_key,
    )
    seats = [
        {
            "seat": 0,
            "seat_wind": "F1",
            "concealed": concealed_dealer,
            "melds": [],
            "discards": [],
            "flowers": [],
            "score": 0,
        },
        *(
            {
                "seat": i,
                "seat_wind": f"F{i + 1}",
                "concealed": ["W2"] * 13,
                "melds": [],
                "discards": [],
                "flowers": [],
                "score": 0,
            }
            for i in (1, 2, 3)
        ),
    ]
    s: dict[str, Any] = {
        "ruleset": MCR_REF,
        "round_wind": "F1",
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 0,
        "wall": {"remaining": [], "drawn_count": 144, "total": 144},
        "seats": seats,
        "last_discard": None,
        "pending_claims": [],
        "phase": "DISCARD",
        "current_actor": 0,
        "terminal": None,
        "rng": {"seed": "0", "cursor": 0},
    }
    hu: dict[str, Any] = {"type": "HU"}
    s_after = apply_action(s, 0, hu)  # type: ignore[arg-type]

    events = diff_to_events(s, 0, hu, s_after, ts=TS)
    hand_end = next(e for e in events if e["event"] == "HAND_END")
    assert hand_end["kind"] == "HU"
    assert hand_end["winner"] == [0]
    assert hand_end["win_type"] == "SELF_DRAW"
    assert hand_end["win_tile"] is not None
    assert hand_end["fan_total"] >= 88
    assert sum(hand_end["score_delta"]) == 0
    assert hand_end["state_hash"].startswith("sha256:")


def test_diff_per_action_payloads_have_required_common_fields() -> None:
    """Every emitted event has event, turn_index, phase, ts (seq filled by writer)."""
    s0 = _seed_state()
    actor = s0["current_actor"]
    play = next(a for a in legal_actions(s0, actor) if a["type"] == "PLAY")
    s1 = apply_action(s0, actor, play)  # type: ignore[arg-type]

    for event in diff_to_events(s0, actor, play, s1, ts=TS):
        assert set(event.keys()) >= {"event", "turn_index", "phase", "ts"}
        assert "seq" not in event, "seq is assigned by the writer, not the differ"
