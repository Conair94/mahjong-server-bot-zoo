"""Tests for `mahjong.engine.legality` — `legal_actions(state, seat)`.

Spec: docs/specs/state-schema.md § Action grammar,
      docs/specs/engine-api.md § Public API.

Step 2.2 of CHECKLIST.md. Hand-traced fixtures, one per action type.
All states are built inline via `_make_state` so the property under
test is visible in the same place as the assertion.
"""

from __future__ import annotations

from typing import Any

from mahjong.engine.legality import legal_actions
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import Tile, tile_sort_key

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _sorted_concealed(tiles: list[Tile]) -> list[Tile]:
    return sorted(tiles, key=tile_sort_key)


def _make_state(
    *,
    phase: str = "DISCARD",
    current_actor: int = 0,
    concealed: list[list[Tile]] | None = None,
    melds: list[list[dict[str, Any]]] | None = None,
    discards: list[list[Tile]] | None = None,
    flowers: list[list[Tile]] | None = None,
    last_discard: dict[str, Any] | None = None,
    pending_claims: list[dict[str, Any]] | None = None,
    wall_remaining: list[Tile] | None = None,
) -> dict[str, Any]:
    """Build a minimal GameState dict for legality testing. Defaults give
    a fresh-hand-shape state with dealer-14 / others-13 placeholder hands
    (filled with `W1`); per-test overrides supply the meaningful fields."""
    if concealed is None:
        concealed = [
            _sorted_concealed(["W1"] * 14),
            _sorted_concealed(["W1"] * 13),
            _sorted_concealed(["W1"] * 13),
            _sorted_concealed(["W1"] * 13),
        ]
    if melds is None:
        melds = [[], [], [], []]
    if discards is None:
        discards = [[], [], [], []]
    if flowers is None:
        flowers = [[], [], [], []]
    if wall_remaining is None:
        wall_remaining = []

    seats = [
        {
            "seat": i,
            "seat_wind": f"F{i + 1}",
            "concealed": concealed[i],
            "melds": melds[i],
            "discards": discards[i],
            "flowers": flowers[i],
            "score": 0,
        }
        for i in range(4)
    ]
    return {
        "ruleset": MCR_REF,
        "round_wind": "F1",
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 0,
        "wall": {
            "remaining": wall_remaining,
            "drawn_count": 144 - len(wall_remaining),
            "total": 144,
        },
        "seats": seats,
        "last_discard": last_discard,
        "pending_claims": pending_claims or [],
        "phase": phase,
        "current_actor": current_actor,
        "terminal": None,
        "rng": {"seed": "0", "cursor": 0},
    }


# --- TERMINAL / DEAL / DRAW phases return [] ---


def test_terminal_returns_empty() -> None:
    s = _make_state(phase="TERMINAL")
    for seat in range(4):
        assert legal_actions(s, seat) == []  # type: ignore[arg-type]


def test_deal_phase_returns_empty() -> None:
    s = _make_state(phase="DEAL")
    for seat in range(4):
        assert legal_actions(s, seat) == []  # type: ignore[arg-type]


def test_draw_phase_returns_empty() -> None:
    s = _make_state(phase="DRAW")
    for seat in range(4):
        assert legal_actions(s, seat) == []  # type: ignore[arg-type]


# --- DISCARD phase ---


def test_discard_non_actor_returns_empty() -> None:
    s = _make_state(phase="DISCARD", current_actor=0)
    for seat in (1, 2, 3):
        assert legal_actions(s, seat) == []  # type: ignore[arg-type]


def test_discard_play_one_action_per_distinct_tile() -> None:
    """PLAY actions: one per distinct tile the actor holds (not per copy)."""
    hand = _sorted_concealed(["W1", "W1", "W2", "B3", "F2", "F2", "F2"] + ["T1"] * 7)
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[hand, ["W1"] * 13, ["W1"] * 13, ["W1"] * 13],
    )
    actions = legal_actions(s, 0)  # type: ignore[arg-type]
    plays = [a for a in actions if a["type"] == "PLAY"]
    play_tiles = sorted(a["tile"] for a in plays)
    assert play_tiles == sorted({"W1", "W2", "B3", "F2", "T1"})


def test_discard_gang_concealed_when_four_of_a_kind_in_hand() -> None:
    hand = _sorted_concealed(["W1"] * 4 + ["B2"] * 4 + ["T3", "T4", "T5"] + ["F1", "F2", "J1"])
    s = _make_state(phase="DISCARD", current_actor=0, concealed=[hand] + [["W1"] * 13] * 3)
    actions = legal_actions(s, 0)  # type: ignore[arg-type]
    gangs = [a for a in actions if a["type"] == "GANG" and a["kind"] == "CONCEALED"]
    assert sorted(a["tile"] for a in gangs) == ["B2", "W1"]


def test_discard_gang_added_when_tile_matches_existing_peng() -> None:
    """An existing PENG of B5 + a B5 in concealed -> GANG (ADDED)."""
    hand = _sorted_concealed(["B5"] + ["W1"] * 10)  # 11 concealed + 3 in meld = 14
    melds_seat0 = [
        {"type": "PENG", "tiles": ["B5", "B5", "B5"], "called_tile": "B5", "called_from_seat": 2}
    ]
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[hand, ["W1"] * 13, ["W1"] * 13, ["W1"] * 13],
        melds=[melds_seat0, [], [], []],
    )
    actions = legal_actions(s, 0)  # type: ignore[arg-type]
    added = [a for a in actions if a["type"] == "GANG" and a["kind"] == "ADDED"]
    assert len(added) == 1
    assert added[0]["tile"] == "B5"


def test_discard_no_gang_added_when_no_matching_peng() -> None:
    hand = _sorted_concealed(["B5"] + ["W1"] * 13)
    s = _make_state(phase="DISCARD", current_actor=0, concealed=[hand] + [["W1"] * 13] * 3)
    actions = legal_actions(s, 0)  # type: ignore[arg-type]
    added = [a for a in actions if a["type"] == "GANG" and a["kind"] == "ADDED"]
    assert added == []


def test_discard_hu_self_draw_on_big_three_dragons() -> None:
    """A 14-tile Big Three Dragons hand (88 fan) → HU is legal on self-draw."""
    # 3 dragon triplets + a wind triplet + a character pair = 4 sets + 1 pair = winning shape.
    hand = _sorted_concealed(
        ["J1", "J1", "J1", "J2", "J2", "J2", "J3", "J3", "J3", "F1", "F1", "F1", "W1", "W1"]
    )
    s = _make_state(phase="DISCARD", current_actor=0, concealed=[hand] + [["W1"] * 13] * 3)
    actions = legal_actions(s, 0)  # type: ignore[arg-type]
    assert any(a["type"] == "HU" for a in actions), (
        f"HU should be legal for a winning self-draw hand; got {actions!r}"
    )


def test_discard_no_hu_for_non_winning_hand() -> None:
    hand = _sorted_concealed(["W1"] * 14)
    s = _make_state(phase="DISCARD", current_actor=0, concealed=[hand] + [["W1"] * 13] * 3)
    actions = legal_actions(s, 0)  # type: ignore[arg-type]
    assert not any(a["type"] == "HU" for a in actions)


# --- CLAIM_WINDOW phase ---


def _claim_window_state(
    *,
    discarder: int,
    discard_tile: Tile,
    claimer_hand: list[Tile],
    claimer_seat: int,
    claimer_melds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    concealed = [["W1"] * 13 for _ in range(4)]
    concealed[claimer_seat] = _sorted_concealed(claimer_hand)
    concealed[discarder] = _sorted_concealed(["W1"] * 13)
    melds = [[], [], [], []]
    if claimer_melds is not None:
        melds[claimer_seat] = claimer_melds
    return _make_state(
        phase="CLAIM_WINDOW",
        current_actor=(discarder + 1) % 4,
        concealed=concealed,
        melds=melds,
        last_discard={"tile": discard_tile, "seat": discarder, "turn_index": 1},
    )


def test_claim_pass_always_available_for_non_discarder() -> None:
    s = _claim_window_state(
        discarder=0, discard_tile="B5", claimer_hand=["W2"] * 13, claimer_seat=2
    )
    actions = legal_actions(s, 2)  # type: ignore[arg-type]
    assert any(a["type"] == "PASS" for a in actions)


def test_claim_discarder_has_no_actions() -> None:
    s = _claim_window_state(
        discarder=0, discard_tile="B5", claimer_hand=["B5", "B5"] + ["W2"] * 11, claimer_seat=2
    )
    assert legal_actions(s, 0) == []  # type: ignore[arg-type]


def test_claim_peng_when_two_matching() -> None:
    s = _claim_window_state(
        discarder=0, discard_tile="B5", claimer_hand=["B5", "B5"] + ["W2"] * 11, claimer_seat=2
    )
    actions = legal_actions(s, 2)  # type: ignore[arg-type]
    pengs = [a for a in actions if a["type"] == "PENG"]
    assert [p["tile"] for p in pengs] == ["B5"]


def test_claim_no_peng_with_only_one_matching() -> None:
    s = _claim_window_state(
        discarder=0, discard_tile="B5", claimer_hand=["B5"] + ["W2"] * 12, claimer_seat=2
    )
    actions = legal_actions(s, 2)  # type: ignore[arg-type]
    assert not any(a["type"] == "PENG" for a in actions)


def test_claim_gang_exposed_when_three_matching() -> None:
    s = _claim_window_state(
        discarder=0, discard_tile="B5", claimer_hand=["B5"] * 3 + ["W2"] * 10, claimer_seat=2
    )
    actions = legal_actions(s, 2)  # type: ignore[arg-type]
    gangs = [a for a in actions if a["type"] == "GANG" and a["kind"] == "EXPOSED"]
    assert [g["tile"] for g in gangs] == ["B5"]


def test_claim_chi_only_for_next_seat() -> None:
    """CHI requires `seat == (discarder + 1) % 4` — no exceptions."""
    s = _claim_window_state(
        discarder=0,
        discard_tile="B5",
        claimer_hand=["B3", "B4", "B6", "B7"] + ["W2"] * 9,
        claimer_seat=2,  # NOT next-seat (next-seat of 0 is 1)
    )
    actions = legal_actions(s, 2)  # type: ignore[arg-type]
    assert not any(a["type"] == "CHI" for a in actions)


def test_claim_chi_enumerates_all_three_runs() -> None:
    """Next-seat with B3,B4,B6,B7 + discard B5 → CHI {B3B4B5, B4B5B6, B5B6B7}."""
    s = _claim_window_state(
        discarder=0,
        discard_tile="B5",
        claimer_hand=["B3", "B4", "B6", "B7"] + ["W2"] * 9,
        claimer_seat=1,
    )
    actions = legal_actions(s, 1)  # type: ignore[arg-type]
    chis = sorted([tuple(a["tiles"]) for a in actions if a["type"] == "CHI"])
    assert chis == sorted(
        [("B3", "B4", "B5"), ("B4", "B5", "B6"), ("B5", "B6", "B7")]
    )


def test_claim_chi_only_suited_tiles() -> None:
    """Honors (F*, J*) and bonus (H*) tiles never form CHI."""
    s = _claim_window_state(
        discarder=0,
        discard_tile="F2",
        claimer_hand=["F1", "F3"] + ["W2"] * 11,
        claimer_seat=1,
    )
    actions = legal_actions(s, 1)  # type: ignore[arg-type]
    assert not any(a["type"] == "CHI" for a in actions)


def test_claim_chi_respects_rank_boundaries() -> None:
    """B1 discard: only {B1,B2,B3} is possible; no {B-1,B0,B1} run."""
    s = _claim_window_state(
        discarder=0,
        discard_tile="B1",
        claimer_hand=["B2", "B3", "B4"] + ["W2"] * 10,
        claimer_seat=1,
    )
    actions = legal_actions(s, 1)  # type: ignore[arg-type]
    chis = sorted([tuple(a["tiles"]) for a in actions if a["type"] == "CHI"])
    assert chis == [("B1", "B2", "B3")]


def test_claim_hu_on_discard_big_three_dragons() -> None:
    """13-tile hand waiting on W1; discarder throws W1 → HU is legal."""
    hand13 = _sorted_concealed(
        ["J1", "J1", "J1", "J2", "J2", "J2", "J3", "J3", "J3", "F1", "F1", "F1", "W1"]
    )
    s = _claim_window_state(
        discarder=0,
        discard_tile="W1",
        claimer_hand=hand13,
        claimer_seat=2,
    )
    actions = legal_actions(s, 2)  # type: ignore[arg-type]
    assert any(a["type"] == "HU" for a in actions)


def test_claim_no_hu_when_discard_does_not_complete() -> None:
    hand13 = _sorted_concealed(
        ["J1", "J1", "J1", "J2", "J2", "J2", "J3", "J3", "J3", "F1", "F1", "F1", "W1"]
    )
    s = _claim_window_state(
        discarder=0,
        discard_tile="T9",  # doesn't complete
        claimer_hand=hand13,
        claimer_seat=2,
    )
    actions = legal_actions(s, 2)  # type: ignore[arg-type]
    assert not any(a["type"] == "HU" for a in actions)
