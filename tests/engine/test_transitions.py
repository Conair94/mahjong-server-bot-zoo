"""Tests for `apply_action(state, seat, action) -> state`.

Spec: docs/specs/state-schema.md, docs/specs/engine-api.md.

Step 2.3 of CHECKLIST.md. Covers per-action-type transitions, IllegalAction
payload completeness, and determinism. Hand-traced fixtures inline so the
shape under test is visible next to the assertion.

Scope note: this step verifies *individual* transitions. Multi-seat claim-
window priority resolution (HU > PENG/GANG > CHI when several seats have
opportunities on the same discard) is exercised by Step 2.4's smoke tests.
For now each claim action resolves immediately on submission, with PASS
removing only the submitting seat's opportunities.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.engine import apply_action
from mahjong.engine.errors import IllegalAction
from mahjong.engine.hashing import canonical_hash
from mahjong.engine.legality import legal_actions
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import Tile, tile_sort_key

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _sorted(tiles: list[Tile]) -> list[Tile]:
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
    turn_index: int = 0,
) -> dict[str, Any]:
    if concealed is None:
        concealed = [_sorted(["W1"] * 14)] + [_sorted(["W1"] * 13) for _ in range(3)]
    if melds is None:
        melds = [[], [], [], []]
    if discards is None:
        discards = [[], [], [], []]
    if flowers is None:
        flowers = [[], [], [], []]
    if wall_remaining is None:
        wall_remaining = ["B1", "B2", "B3", "B4", "B5"]
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
        "turn_index": turn_index,
        "wall": {
            "remaining": wall_remaining,
            "drawn_count": 144 - len(wall_remaining),
            "total": 144,
        },
        "seats": seats,
        "last_discard": last_discard,
        "last_drawn": None,
        "pending_claims": pending_claims or [],
        "phase": phase,
        "current_actor": current_actor,
        "terminal": None,
        "rng": {"seed": "0", "cursor": 0},
    }


# --- PLAY ---


def test_play_moves_tile_from_concealed_to_discards() -> None:
    """No-claim discard: tile lands in discards, next seat draws, phase=DISCARD."""
    # Use an isolated tile no one else can claim (no duplicates anywhere).
    hand0 = _sorted(["B7"] + ["W1"] * 13)
    others = [_sorted(["W1"] * 13) for _ in range(3)]
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[hand0, *others],
        wall_remaining=["B1", "B2", "B3", "B4", "B5"],
    )
    new = apply_action(s, 0, {"type": "PLAY", "tile": "B7"})  # type: ignore[arg-type]
    assert "B7" not in new["seats"][0]["concealed"]
    assert new["seats"][0]["discards"] == ["B7"]
    assert new["turn_index"] == 1
    # No one else has B7-claimable tiles → claim window is empty → next seat draws.
    # Next seat takes wall[0] == B1; phase returns to DISCARD with current_actor=1.
    assert new["phase"] == "DISCARD"
    assert new["current_actor"] == 1
    assert "B1" in new["seats"][1]["concealed"]
    assert len(new["seats"][1]["concealed"]) == 14  # was 13, drew one
    assert new["wall"]["remaining"] == ["B2", "B3", "B4", "B5"]
    assert new["last_discard"] is None  # consumed by claim-window resolution


def test_play_opens_claim_window_when_someone_can_claim() -> None:
    """A discard that another seat could PENG opens CLAIM_WINDOW."""
    hand0 = _sorted(["B5"] + ["W1"] * 13)
    seat2_hand = _sorted(["B5", "B5"] + ["T1"] * 11)
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[hand0, _sorted(["W1"] * 13), seat2_hand, _sorted(["W1"] * 13)],
    )
    new = apply_action(s, 0, {"type": "PLAY", "tile": "B5"})  # type: ignore[arg-type]
    assert new["phase"] == "CLAIM_WINDOW"
    assert new["last_discard"] == {"tile": "B5", "seat": 0, "turn_index": 1}
    # Seat 2 has a PENG opportunity.
    assert any(c["seat"] == 2 and c["claim"] == "PENG" for c in new["pending_claims"])


def test_play_immutability_input_state_unchanged() -> None:
    hand0 = _sorted(["B7"] + ["W1"] * 13)
    s = _make_state(phase="DISCARD", current_actor=0, concealed=[hand0] + [["W1"] * 13] * 3)
    snapshot = canonical_hash(s)
    apply_action(s, 0, {"type": "PLAY", "tile": "B7"})  # type: ignore[arg-type]
    assert canonical_hash(s) == snapshot, "apply_action must not mutate input"


def test_play_illegal_when_tile_not_in_concealed() -> None:
    s = _make_state(phase="DISCARD", current_actor=0)
    with pytest.raises(IllegalAction) as excinfo:
        apply_action(s, 0, {"type": "PLAY", "tile": "J3"})  # type: ignore[arg-type]
    err = excinfo.value
    assert err.seat == 0
    assert err.attempted_action == {"type": "PLAY", "tile": "J3"}
    assert err.state_hash == canonical_hash(s)
    assert isinstance(err.legal_actions, list)
    assert len(err.legal_actions) > 0


def test_play_illegal_when_not_actors_turn() -> None:
    s = _make_state(phase="DISCARD", current_actor=0)
    with pytest.raises(IllegalAction):
        apply_action(s, 1, {"type": "PLAY", "tile": "W1"})  # type: ignore[arg-type]


def test_play_determinism_same_input_same_output() -> None:
    hand0 = _sorted(["B7"] + ["W1"] * 13)
    s = _make_state(phase="DISCARD", current_actor=0, concealed=[hand0] + [["W1"] * 13] * 3)
    h1 = canonical_hash(apply_action(s, 0, {"type": "PLAY", "tile": "B7"}))  # type: ignore[arg-type]
    h2 = canonical_hash(apply_action(s, 0, {"type": "PLAY", "tile": "B7"}))  # type: ignore[arg-type]
    assert h1 == h2


# --- PASS ---


def test_pass_clears_claim_window_to_next_seat_draw() -> None:
    """All seats PASSing on a discard advances to next-seat DISCARD."""
    s = _make_state(
        phase="CLAIM_WINDOW",
        current_actor=1,
        concealed=[
            _sorted(["W1"] * 13),
            _sorted(["B7", "B7"] + ["W2"] * 11),
            _sorted(["W1"] * 13),
            _sorted(["W1"] * 13),
        ],
        discards=[["B7"], [], [], []],
        last_discard={"tile": "B7", "seat": 0, "turn_index": 1},
        pending_claims=[{"seat": 1, "claim": "PENG"}],
        turn_index=1,
        wall_remaining=["T1", "T2", "T3"],
    )
    new = apply_action(s, 1, {"type": "PASS"})  # type: ignore[arg-type]
    # All pending claims resolved → next seat (after discarder=0) draws.
    assert new["phase"] == "DISCARD"
    assert new["current_actor"] == 1  # next seat after discarder 0
    assert new["last_discard"] is None
    assert new["pending_claims"] == []
    assert new["seats"][0]["discards"] == ["B7"]  # discard persists in pile
    assert "T1" in new["seats"][1]["concealed"]


def test_pass_illegal_outside_claim_window() -> None:
    s = _make_state(phase="DISCARD", current_actor=0)
    with pytest.raises(IllegalAction):
        apply_action(s, 0, {"type": "PASS"})  # type: ignore[arg-type]


# --- PENG ---


def test_peng_forms_meld_and_advances_to_claimers_discard() -> None:
    hand2 = _sorted(["B5", "B5"] + ["W2"] * 11)
    s = _make_state(
        phase="CLAIM_WINDOW",
        current_actor=1,
        concealed=[_sorted(["W1"] * 13), _sorted(["W1"] * 13), hand2, _sorted(["W1"] * 13)],
        discards=[["B5"], [], [], []],
        last_discard={"tile": "B5", "seat": 0, "turn_index": 1},
        pending_claims=[{"seat": 2, "claim": "PENG"}],
    )
    new = apply_action(s, 2, {"type": "PENG", "tile": "B5"})  # type: ignore[arg-type]
    # Claimer melded; discarder's discards no longer hold the claimed tile.
    assert new["seats"][2]["concealed"].count("B5") == 0
    assert any(
        m["type"] == "PENG" and m["tiles"] == ["B5", "B5", "B5"] and m["called_from_seat"] == 0
        for m in new["seats"][2]["melds"]
    )
    assert new["seats"][0]["discards"] == []
    # Claimer must now discard.
    assert new["phase"] == "DISCARD"
    assert new["current_actor"] == 2
    assert new["last_discard"] is None
    assert new["pending_claims"] == []


# --- CHI ---


def test_chi_forms_run_meld() -> None:
    hand1 = _sorted(["B4", "B6"] + ["W2"] * 11)
    s = _make_state(
        phase="CLAIM_WINDOW",
        current_actor=1,
        concealed=[_sorted(["W1"] * 13), hand1, _sorted(["W1"] * 13), _sorted(["W1"] * 13)],
        discards=[["B5"], [], [], []],
        last_discard={"tile": "B5", "seat": 0, "turn_index": 1},
        pending_claims=[{"seat": 1, "claim": "CHI", "chi_tiles": ["B4", "B5", "B6"]}],
    )
    new = apply_action(s, 1, {"type": "CHI", "tiles": ["B4", "B5", "B6"]})  # type: ignore[arg-type]
    assert new["seats"][1]["concealed"].count("B4") == 0
    assert new["seats"][1]["concealed"].count("B6") == 0
    melds = new["seats"][1]["melds"]
    assert len(melds) == 1 and melds[0]["type"] == "CHI"
    assert melds[0]["tiles"] == ["B4", "B5", "B6"]
    assert melds[0]["called_from_seat"] == 0
    assert new["phase"] == "DISCARD"
    assert new["current_actor"] == 1


# --- GANG ---


def test_gang_exposed_from_discard_triggers_replacement_draw() -> None:
    hand2 = _sorted(["B5", "B5", "B5"] + ["W2"] * 10)
    s = _make_state(
        phase="CLAIM_WINDOW",
        current_actor=1,
        concealed=[_sorted(["W1"] * 13), _sorted(["W1"] * 13), hand2, _sorted(["W1"] * 13)],
        discards=[["B5"], [], [], []],
        last_discard={"tile": "B5", "seat": 0, "turn_index": 1},
        pending_claims=[{"seat": 2, "claim": "GANG"}],
        wall_remaining=["T1", "T2", "T3"],
    )
    new = apply_action(
        s, 2, {"type": "GANG", "tile": "B5", "kind": "EXPOSED"}  # type: ignore[arg-type]
    )
    melds = new["seats"][2]["melds"]
    assert any(m["type"] == "GANG_EXPOSED" and m["tiles"] == ["B5"] * 4 for m in melds)
    assert "T1" in new["seats"][2]["concealed"]  # replacement draw
    assert new["wall"]["remaining"] == ["T2", "T3"]
    assert new["phase"] == "DISCARD"
    assert new["current_actor"] == 2


def test_gang_concealed_own_turn() -> None:
    hand = _sorted(["B5"] * 4 + ["W2"] * 10)
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[hand, _sorted(["W1"] * 13), _sorted(["W1"] * 13), _sorted(["W1"] * 13)],
        wall_remaining=["T1", "T2", "T3"],
    )
    new = apply_action(
        s, 0, {"type": "GANG", "tile": "B5", "kind": "CONCEALED"}  # type: ignore[arg-type]
    )
    assert new["seats"][0]["concealed"].count("B5") == 0
    assert any(m["type"] == "GANG_CONCEALED" and m["tiles"] == ["B5"] * 4 for m in new["seats"][0]["melds"])
    assert "T1" in new["seats"][0]["concealed"]
    assert new["phase"] == "DISCARD"
    assert new["current_actor"] == 0  # same player continues


def test_gang_added_promotes_existing_peng() -> None:
    hand = _sorted(["B5"] + ["W2"] * 10)  # 11 in hand + 3 in PENG = 14
    melds0 = [
        {"type": "PENG", "tiles": ["B5", "B5", "B5"], "called_tile": "B5", "called_from_seat": 2}
    ]
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[hand, _sorted(["W1"] * 13), _sorted(["W1"] * 13), _sorted(["W1"] * 13)],
        melds=[melds0, [], [], []],
        wall_remaining=["T1"],
    )
    new = apply_action(
        s, 0, {"type": "GANG", "tile": "B5", "kind": "ADDED"}  # type: ignore[arg-type]
    )
    new_melds = new["seats"][0]["melds"]
    assert len(new_melds) == 1
    assert new_melds[0]["type"] == "GANG_ADDED"
    assert new_melds[0]["tiles"] == ["B5"] * 4
    # Preserved provenance: still called from seat 2 originally.
    assert new_melds[0]["called_from_seat"] == 2
    assert new["seats"][0]["concealed"].count("B5") == 0
    assert "T1" in new["seats"][0]["concealed"]


# --- HU ---


def _big_three_dragons_14() -> list[Tile]:
    return _sorted(
        ["J1", "J1", "J1", "J2", "J2", "J2", "J3", "J3", "J3", "F1", "F1", "F1", "W1", "W1"]
    )


def _big_three_dragons_13() -> list[Tile]:
    """13-tile tenpai waiting on W1 (the pair)."""
    return _sorted(
        ["J1", "J1", "J1", "J2", "J2", "J2", "J3", "J3", "J3", "F1", "F1", "F1", "W1"]
    )


def test_hu_self_draw_transitions_to_terminal() -> None:
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[_big_three_dragons_14(), _sorted(["W2"] * 13), _sorted(["W2"] * 13), _sorted(["W2"] * 13)],
    )
    new = apply_action(s, 0, {"type": "HU"})  # type: ignore[arg-type]
    assert new["phase"] == "TERMINAL"
    assert new["last_drawn"] is None  # cleared at terminal
    t = new["terminal"]
    assert t is not None
    assert t["kind"] == "HU"
    assert t["winner"] == 0
    assert t["win_type"] == "SELF_DRAW"
    assert t["deal_in_seat"] is None
    assert t["fan_total"] >= 8
    assert len(t["fan"]) > 0
    assert len(t["score_delta"]) == 4
    assert sum(t["score_delta"]) == 0  # zero-sum


def test_hu_self_draw_prefers_last_drawn_as_win_tile() -> None:
    """When `last_drawn` is set, win_tile equals the just-drawn tile (the
    physically correct answer) rather than the canonical-sort fallback."""
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[_big_three_dragons_14(), _sorted(["W2"] * 13), _sorted(["W2"] * 13), _sorted(["W2"] * 13)],
    )
    s["last_drawn"] = {"seat": 0, "tile": "W1"}  # the pair tile
    new = apply_action(s, 0, {"type": "HU"})  # type: ignore[arg-type]
    t = new["terminal"]
    assert t is not None
    assert t["win_tile"] == "W1"


def test_hu_on_discard_transitions_to_terminal_with_deal_in_seat() -> None:
    s = _make_state(
        phase="CLAIM_WINDOW",
        current_actor=1,
        concealed=[_sorted(["W2"] * 13), _sorted(["W2"] * 13), _big_three_dragons_13(), _sorted(["W2"] * 13)],
        discards=[["W1"], [], [], []],
        last_discard={"tile": "W1", "seat": 0, "turn_index": 1},
        pending_claims=[{"seat": 2, "claim": "HU"}],
    )
    new = apply_action(s, 2, {"type": "HU"})  # type: ignore[arg-type]
    assert new["phase"] == "TERMINAL"
    t = new["terminal"]
    assert t is not None
    assert t["winner"] == 2
    assert t["win_tile"] == "W1"
    assert t["win_type"] == "DISCARD"
    assert t["deal_in_seat"] == 0
    assert t["fan_total"] >= 8


def test_hu_illegal_on_non_winning_hand() -> None:
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[_sorted(["W1"] * 14)] + [_sorted(["W2"] * 13) for _ in range(3)],
    )
    with pytest.raises(IllegalAction):
        apply_action(s, 0, {"type": "HU"})  # type: ignore[arg-type]


# --- IllegalAction payload completeness (engine-api.md fixture 5) ---


def test_illegal_action_payload_fields() -> None:
    s = _make_state(phase="DISCARD", current_actor=0)
    with pytest.raises(IllegalAction) as excinfo:
        apply_action(s, 0, {"type": "PENG", "tile": "B5"})  # type: ignore[arg-type]
    err = excinfo.value
    assert err.state_hash and err.state_hash.startswith("sha256:")
    assert err.seat == 0
    assert err.attempted_action == {"type": "PENG", "tile": "B5"}
    assert isinstance(err.legal_actions, list)


# --- legal_actions ∩ apply_action consistency (engine-api.md fixture 6) ---


def test_legal_actions_all_succeed_under_apply_action() -> None:
    """Every action returned by legal_actions must be accepted by apply_action."""
    hand0 = _sorted(["B5"] * 4 + ["B7"] + ["W2"] * 9)
    s = _make_state(
        phase="DISCARD",
        current_actor=0,
        concealed=[hand0, _sorted(["W1"] * 13), _sorted(["W1"] * 13), _sorted(["W1"] * 13)],
        wall_remaining=["T1", "T2", "T3"],
    )
    for action in legal_actions(s, 0):  # type: ignore[arg-type]
        # Should not raise.
        _ = apply_action(s, 0, action)  # type: ignore[arg-type]
