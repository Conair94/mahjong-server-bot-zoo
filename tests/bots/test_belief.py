"""Tests for belief Stage A — hard accounting (`mahjong.bots.belief`).

Spec: docs/specs/v1-rule-bot.md § Component 1A. Each fixture pins the
unseen-pool arithmetic for one visibility source (own hand, discards, exposed
melds, masked concealed kong), per the AI-plan Stage A verification artifacts.
Pure dict arithmetic — no pymj, no async.
"""

from __future__ import annotations

from typing import Any

from mahjong.bots.belief import ALL_TILE_TYPES, remaining_counts

RULESET: dict[str, Any] = {"id": "mcr-house-3fan", "version": 1, "config_hash": "x"}


def _view(
    *,
    seat: int = 0,
    concealed: list[str] | None = None,
    seats_overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Minimal SeatView: self seat holds `concealed`, opponents are stubs that
    `seats_overrides` can extend with discards/melds."""
    seats: list[dict[str, Any]] = []
    for i in range(4):
        base: dict[str, Any] = {
            "seat": i,
            "seat_wind": f"F{i + 1}",
            "concealed": list(concealed or []) if i == seat else {"count": 13},
            "melds": [],
            "discards": [],
            "flowers": [],
            "score": 0,
        }
        if seats_overrides and i in seats_overrides:
            base.update(seats_overrides[i])
        seats.append(base)
    return {
        "ruleset": RULESET,
        "round_wind": "F1",
        "dealer_seat": 0,
        "hand_index": 0,
        "turn_index": 0,
        "wall": {"remaining_count": 50, "drawn_count": 94, "total": 144},
        "seats": seats,
        "last_discard": None,
        "pending_claims": [],
        "phase": "DISCARD",
        "current_actor": seat,
        "terminal": None,
    }


def test_all_34_types_present() -> None:
    counts = remaining_counts(_view(concealed=[]), 0)
    assert set(counts) == set(ALL_TILE_TYPES)
    assert len(counts) == 34


def test_fresh_deal_only_own_hand_visible() -> None:
    hand = ["B1", "B1", "B2", "T4", "T5", "T6", "W7", "W8", "W9", "F1", "F1", "F1", "J3"]
    counts = remaining_counts(_view(concealed=hand), 0)
    assert counts["B1"] == 2  # we hold two
    assert counts["B2"] == 3
    assert counts["F1"] == 1  # we hold three
    assert counts["J3"] == 3
    assert counts["W5"] == 4  # untouched type
    assert sum(counts.values()) == 4 * 34 - len(hand)


def test_discards_count_including_last_discard_in_pond() -> None:
    # last_discard duplicates a tile already in the discarder's pond — the
    # pond is the single source; the tile must not be double-counted.
    view = _view(
        concealed=[],
        seats_overrides={2: {"discards": ["W5", "W5", "J1"]}},
    )
    view["last_discard"] = {"tile": "W5", "seat": 2, "turn_index": 7}
    counts = remaining_counts(view, 0)
    assert counts["W5"] == 2
    assert counts["J1"] == 3


def test_exposed_melds_count() -> None:
    view = _view(
        concealed=["B9"],
        seats_overrides={
            1: {
                "melds": [
                    {"type": "PENG", "tiles": ["T2", "T2", "T2"], "called_from_seat": 0},
                    {
                        "type": "GANG_EXPOSED",
                        "tiles": ["J2", "J2", "J2", "J2"],
                        "called_from_seat": 3,
                    },
                ]
            },
            3: {"melds": [{"type": "CHI", "tiles": ["B7", "B8", "B9"], "called_from_seat": 2}]},
        },
    )
    counts = remaining_counts(view, 0)
    assert counts["T2"] == 1
    assert counts["J2"] == 0
    assert counts["B8"] == 3
    assert counts["B9"] == 2  # one in the CHI, one in our hand


def test_own_melds_count() -> None:
    view = _view(
        concealed=["W1"],
        seats_overrides={
            0: {
                "melds": [
                    {
                        "type": "GANG_CONCEALED",
                        "tiles": ["T9", "T9", "T9", "T9"],
                        "called_from_seat": 0,
                    }
                ]
            }
        },
    )
    counts = remaining_counts(view, 0)
    assert counts["T9"] == 0  # own concealed kong is visible to self
    assert counts["W1"] == 3


def test_opponent_concealed_kong_is_unknown() -> None:
    # Projection masks the tiles (Spec 29 Bug D): no `tiles`, `hidden: True`.
    # Stage A treats the four tiles as unseen — a documented overcount.
    view = _view(
        concealed=[],
        seats_overrides={
            2: {"melds": [{"type": "GANG_CONCEALED", "called_from_seat": 2, "hidden": True}]}
        },
    )
    counts = remaining_counts(view, 0)
    assert all(counts[t] == 4 for t in ALL_TILE_TYPES)


def test_counts_clamp_at_zero() -> None:
    # Contradictory (bug-grade) view: 5 copies visible. Clamp, don't go negative.
    view = _view(
        concealed=["W5"],
        seats_overrides={1: {"discards": ["W5", "W5", "W5", "W5"]}},
    )
    counts = remaining_counts(view, 0)
    assert counts["W5"] == 0
