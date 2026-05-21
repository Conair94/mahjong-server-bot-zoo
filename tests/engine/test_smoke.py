"""End-to-end smoke tests: the engine plays a complete hand.

Spec: CHECKLIST.md § Step 2.4. Two scripted scenarios:
    - Four-seat-always-PASS exhaustive draw from `initial_state(seed=12345)`.
    - A toy dealer-HU on a hand-crafted Big Three Dragons opening.

These are *integration* fixtures — they catch silent regressions across the
whole rules engine (deal + legality + transitions + terminal scoring) that
unit-level fixtures would miss.
"""

from __future__ import annotations

from typing import Any

import pytest

from mahjong.engine import apply_action, initial_state, is_terminal, legal_actions, state_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import tile_sort_key
from tests.conftest import load_golden

MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _always_pass_play_lowest_step(s: dict[str, Any]) -> dict[str, Any]:
    """Scripted policy: in DISCARD play the lowest tile (canonical sort);
    in CLAIM_WINDOW every eligible seat PASSes.

    HU and gang opportunities are deliberately skipped — this is the policy
    that drives the exhaustive-draw smoke fixture.
    """
    phase = s["phase"]
    if phase == "DISCARD":
        actor = s["current_actor"]
        plays = [a for a in legal_actions(s, actor) if a["type"] == "PLAY"]
        assert plays, "DISCARD phase with no PLAY action — engine bug"
        lowest = min(plays, key=lambda a: tile_sort_key(a["tile"]))
        return apply_action(s, actor, lowest)
    if phase == "CLAIM_WINDOW":
        # Resolve every seat with an opportunity by PASSing.
        seats_with_claims = {c["seat"] for c in s["pending_claims"]}
        for seat in sorted(seats_with_claims):
            s = apply_action(s, seat, {"type": "PASS"})
            if s["phase"] != "CLAIM_WINDOW":
                break
        return s
    raise AssertionError(f"unexpected phase in smoke driver: {phase!r}")


def test_smoke_four_pass_exhaustive_draw() -> None:
    """Scripted always-PASS game from seed 12345 reaches an exhaustive draw.

    Final state's hash is checked-in as a golden; a change to that hash is
    a determinism contract break (see determinism.md refactor protocol).
    """
    s = initial_state(MCR_REF, seed=12345)
    steps = 0
    while not is_terminal(s):
        s = _always_pass_play_lowest_step(s)  # type: ignore[arg-type]
        steps += 1
        if steps > 1000:
            pytest.fail("smoke game did not terminate in 1000 steps")

    assert s["phase"] == "TERMINAL"
    assert s["terminal"] is not None
    assert s["terminal"]["kind"] == "DRAW"
    assert s["terminal"]["score_delta"] == [0, 0, 0, 0]

    golden = load_golden("smoke_exhaustive_draw_seed_12345.json")
    assert state_hash(s) == golden["state_hash"]  # type: ignore[arg-type]


def test_smoke_dealer_hu_toy_game() -> None:
    """A hand-crafted Big Three Dragons dealer wins on self-draw turn 0.

    Verifies: terminal.fan includes 'Big Three Dragons'; fan_total >= 88;
    score_delta is zero-sum; winner == dealer.
    """
    # Hand-crafted state: dealer holds a complete Big Three Dragons hand.
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

    new = apply_action(s, 0, {"type": "HU"})  # type: ignore[arg-type]

    assert new["phase"] == "TERMINAL"
    t = new["terminal"]
    assert t is not None
    assert t["kind"] == "HU"
    assert t["winner"] == 0
    assert t["win_type"] == "SELF_DRAW"
    fan_names = {f["name"] for f in t["fan"]}
    assert "Big Three Dragons" in fan_names, f"got fan names: {fan_names!r}"
    assert t["fan_total"] >= 88
    assert sum(t["score_delta"]) == 0
    assert t["score_delta"][0] > 0
    for i in (1, 2, 3):
        assert t["score_delta"][i] < 0
