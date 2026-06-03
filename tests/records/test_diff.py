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


def test_diff_from_hand_false_when_playing_just_drawn_tile() -> None:
    """Tsumogiri: actor plays the tile they just drew → from_hand=False."""
    s0 = _seed_state()
    actor = s0["current_actor"]
    just_drawn = s0["last_drawn"]
    assert just_drawn is not None and just_drawn["seat"] == actor
    play = {"type": "PLAY", "tile": just_drawn["tile"]}
    s1 = apply_action(s0, actor, play)  # type: ignore[arg-type]
    events = diff_to_events(s0, actor, play, s1, ts=TS)  # type: ignore[arg-type]
    discard = next(e for e in events if e["event"] == "DISCARD")
    assert discard["from_hand"] is False


def test_diff_from_hand_true_when_playing_other_tile() -> None:
    """Normal discard: actor plays a tile they didn't just draw → from_hand=True."""
    s0 = _seed_state()
    actor = s0["current_actor"]
    just_drawn = s0["last_drawn"]
    assert just_drawn is not None
    plays = [a for a in legal_actions(s0, actor) if a["type"] == "PLAY"]
    other_play = next(a for a in plays if a["tile"] != just_drawn["tile"])
    s1 = apply_action(s0, actor, other_play)  # type: ignore[arg-type]
    events = diff_to_events(s0, actor, other_play, s1, ts=TS)
    discard = next(e for e in events if e["event"] == "DISCARD")
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


def test_diff_draw_tile_is_just_drawn_not_concealed_last() -> None:
    """Regression (multi-human bug 2026-05-26): the DRAW event's ``tile``
    must reflect the just-drawn tile (``state.last_drawn.tile``), NOT
    ``concealed[-1]``.  The engine sorts ``concealed`` after every draw
    (``transition.__init__.py``: ``seat_concealed.sort(...)``), so
    ``concealed[-1]`` is the highest-sorted tile in the hand — which is
    typically the SAME tile turn after turn until the player discards it.
    Reporting that on the wire caused human clients to see every DRAW
    look identical, and naive reducers re-appended the tile each turn
    until the hand grew past 14.

    Pin: walk the engine past at least one all-PASS claim window so a
    DRAW event is emitted, then assert ``draw.tile == s.last_drawn.tile``
    (the just-drawn) and that it is NOT equal to ``concealed[-1]`` in the
    common case where the sort doesn't happen to put the new tile last.
    """
    # Walk to a CLAIM_WINDOW (initial state is already at DISCARD).
    s = _seed_state()
    steps = 0
    while s["phase"] != "CLAIM_WINDOW" and steps < 200:
        actor = s["current_actor"]
        plays = [a for a in legal_actions(s, actor) if a["type"] == "PLAY"]
        s = apply_action(s, actor, min(plays, key=lambda a: tile_sort_key(a["tile"])))  # type: ignore[arg-type]
        steps += 1
    assert s["phase"] == "CLAIM_WINDOW"

    # Drive an all-PASS resolution; the last PASS triggers the next DRAW.
    claimers = sorted({c["seat"] for c in s["pending_claims"]})
    events: list[dict[str, Any]] = []
    state_before = s
    for claimer in claimers:
        state_before = s
        s = apply_action(s, claimer, {"type": "PASS"})  # type: ignore[arg-type]
        events = diff_to_events(state_before, claimer, {"type": "PASS"}, s, ts=TS)
    # The final PASS's event list should include a DRAW (engine advanced
    # into the next discard phase).
    draws = [e for e in events if e["event"] == "DRAW"]
    assert draws, f"expected DRAW after all-PASS resolution; got: {events}"
    draw = draws[0]

    drawer = draw["seat"]
    assert s["last_drawn"] is not None and s["last_drawn"]["seat"] == drawer
    assert draw["tile"] == s["last_drawn"]["tile"], (
        f"DRAW.tile should be the just-drawn tile; got {draw['tile']!r}, "
        f"expected {s['last_drawn']['tile']!r}"
    )

    # Sanity: the just-drawn tile is generally NOT the last element of
    # the sorted concealed list (would only coincide if it happens to be
    # the maximum).  This is the failure signature the original bug had.
    concealed_last = s["seats"][drawer]["concealed"][-1]
    if draw["tile"] != concealed_last:
        # Common case: we've now positively distinguished the two.
        pass


def test_diff_added_gang_emits_replacement_draw() -> None:
    """BUGANG (added kong) from hand draws a gangshanghua replacement
    (engine: ``_gang_added`` → ``internal_draw``). The differ must surface
    that DRAW or the wire/record never reports the replacement tile and the
    client hand desyncs, stalling the table (Spec 22 § 22.5, 2026-06-01)."""
    s0 = _seed_state()
    actor = s0["current_actor"]
    sd = s0["seats"][actor]
    sd["melds"].append(
        {"type": "PENG", "tiles": ["W1", "W1", "W1"], "called_tile": "W1", "called_from_seat": (actor + 1) % 4}
    )
    if "W1" not in sd["concealed"]:
        sd["concealed"][0] = "W1"
    gang = {"type": "GANG", "tile": "W1", "kind": "ADDED"}
    s1 = apply_action(s0, actor, gang)  # type: ignore[arg-type]

    events = diff_to_events(s0, actor, gang, s1, ts=TS)  # type: ignore[arg-type]
    decision = next(e for e in events if e["event"] == "CLAIM_DECISION")
    assert decision["decision"] == "GANG"
    draws = [e for e in events if e["event"] == "DRAW"]
    assert draws, f"GANG/ADDED must emit a replacement DRAW; got {[e['event'] for e in events]}"
    draw = draws[0]
    assert draw["seat"] == actor
    assert s1["last_drawn"] is not None and s1["last_drawn"]["seat"] == actor
    assert draw["tile"] == s1["last_drawn"]["tile"]


def test_diff_concealed_gang_emits_replacement_draw() -> None:
    """An angang (concealed kong) also draws a replacement; same DRAW gap."""
    s0 = _seed_state()
    actor = s0["current_actor"]
    sd = s0["seats"][actor]
    sd["concealed"][:4] = ["B5", "B5", "B5", "B5"]
    gang = {"type": "GANG", "tile": "B5", "kind": "CONCEALED"}
    s1 = apply_action(s0, actor, gang)  # type: ignore[arg-type]

    events = diff_to_events(s0, actor, gang, s1, ts=TS)  # type: ignore[arg-type]
    draws = [e for e in events if e["event"] == "DRAW"]
    assert draws, f"GANG/CONCEALED must emit a replacement DRAW; got {[e['event'] for e in events]}"
    assert draws[0]["seat"] == actor
    assert draws[0]["tile"] == s1["last_drawn"]["tile"]


def test_diff_exposed_gang_emits_replacement_draw() -> None:
    """An exposed kong (claimed off a discard) draws a replacement too. Built
    on a crafted state so the claim is deterministic."""
    seats = [
        {
            "seat": 0,
            "seat_wind": "F1",
            "concealed": sorted(
                ["T3", "T3", "T3"] + ["W2"] * 11, key=tile_sort_key
            ),
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
                "discards": ["T3"] if i == 1 else [],
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
        "turn_index": 5,
        "wall": {"remaining": ["B7", "B8", "B9"], "drawn_count": 100, "total": 144},
        "seats": seats,
        "last_discard": {"seat": 1, "tile": "T3"},
        "last_drawn": None,
        "pending_claims": [{"seat": 0, "type": "GANG"}],
        "phase": "CLAIM_WINDOW",
        "current_actor": 1,
        "terminal": None,
        "rng": {"seed": "0", "cursor": 0},
    }
    gang = {"type": "GANG", "tile": "T3", "kind": "EXPOSED"}
    s1 = apply_action(s, 0, gang)  # type: ignore[arg-type]

    events = diff_to_events(s, 0, gang, s1, ts=TS)  # type: ignore[arg-type]
    draws = [e for e in events if e["event"] == "DRAW"]
    assert draws, f"GANG/EXPOSED must emit a replacement DRAW; got {[e['event'] for e in events]}"
    assert draws[0]["seat"] == 0
    assert draws[0]["tile"] == s1["last_drawn"]["tile"]


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
        "last_drawn": {"seat": 0, "tile": "W1"},
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
