"""Settlement (hand-end) tenpai/shanten reveal: `analysis.settlement_hand_stats`.

Spec: docs/specs/hand-stats.md § Settlement / hand-end stats.

Fan literals are calculator-anchored (the FB-09/FB-15 lesson: never hand-derive
MCR arithmetic). The tenpai waits below reuse the exact figures independently
probed in test_hand_stats.py (TENPAI_A → B6 4f/6f, B9 6f/8f), which doubles as a
cross-check that the settlement path agrees with the prompt-stats path.
"""

from __future__ import annotations

import json
from typing import Any

from mahjong.analysis import settlement_hand_stats
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import tile_sort_key

RS: dict[str, Any] = {
    "id": "mcr-house-3fan",
    "version": 1,
    "config_hash": MANIFEST["mcr-house-3fan"],
}

# Probed fixtures (all evaluated with seat_wind/round_wind = F1, the same winds
# test_hand_stats.py used so the fan figures line up).
TENPAI = ["W1", "W1", "W1", "W7", "W8", "W9", "B1", "B2", "B3", "T5", "T5", "B7", "B8"]
ONE_SHANTEN = ["W1", "W1", "W1", "T5", "T6", "T7", "W2", "W3", "B2", "B2", "J3", "J3", "T1"]
TWO_SHANTEN = ["W1", "W2", "W3", "W5", "W6", "B1", "B2", "T4", "T5", "T6", "J1", "J2", "J3"]


def _seat(seat: int, concealed: list[str], melds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "seat": seat,
        "seat_wind": "F1",
        "concealed": sorted(concealed, key=tile_sort_key),
        "melds": melds or [],
        "discards": [],
        "flowers": [],
        "score": 0,
    }


def _by_seat(out: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {e["seat"]: e for e in out["seats"]}


def test_tenpai_seat_lists_waits_with_raw_fan() -> None:
    """A tenpai non-winner reports shanten 0 and per-wait raw fan, matching the
    independently-probed prompt-stats figures."""
    out = settlement_hand_stats([_seat(0, TENPAI)], "F1", RS)
    assert out["floor"] == 3
    entry = _by_seat(out)[0]
    assert entry["shanten"] == 0
    assert entry["waits"] == [
        {"tile": "B6", "fan_discard": 4, "fan_self_draw": 6},
        {"tile": "B9", "fan_discard": 6, "fan_self_draw": 8},
    ]
    assert "accepts" not in entry


def test_one_shanten_seat_lists_top3_accepts_ranked_by_fan() -> None:
    """A 1-shanten non-winner reports the top-3 tiles that reach tenpai, ranked
    by best reachable fan (desc), tie-broken by tile order, capped at 3 — the
    full acceptance set here is W1/W4/B2/J3, so the lowest-fan tile is dropped."""
    out = settlement_hand_stats([_seat(0, ONE_SHANTEN)], "F1", RS)
    entry = _by_seat(out)[0]
    assert entry["shanten"] == 1
    assert entry["accepts"] == [
        {"tile": "W1", "best_fan": 11},
        {"tile": "J3", "best_fan": 11},
        {"tile": "W4", "best_fan": 9},
    ]
    # Ranking invariant, recomputed from the payload itself.
    keys = [(-a["best_fan"], tile_sort_key(a["tile"])) for a in entry["accepts"]]
    assert keys == sorted(keys)
    assert "waits" not in entry


def test_two_shanten_seat_has_shanten_only_no_fan() -> None:
    out = settlement_hand_stats([_seat(0, TWO_SHANTEN)], "F1", RS)
    entry = _by_seat(out)[0]
    assert entry["shanten"] == 2
    assert "waits" not in entry and "accepts" not in entry


def test_winner_is_excluded() -> None:
    seats = [_seat(0, TENPAI), _seat(1, ONE_SHANTEN), _seat(2, TWO_SHANTEN), _seat(3, TENPAI)]
    out = settlement_hand_stats(seats, "F1", RS, exclude_seats=[2])
    assert set(_by_seat(out)) == {0, 1, 3}


def test_draw_reveals_all_four_seats() -> None:
    """On an exhausted draw nobody won, so every seat appears (no exclusions)."""
    seats = [_seat(0, TENPAI), _seat(1, ONE_SHANTEN), _seat(2, TWO_SHANTEN), _seat(3, ONE_SHANTEN)]
    out = settlement_hand_stats(seats, "F1", RS)
    assert set(_by_seat(out)) == {0, 1, 2, 3}
    assert out["seats"] == sorted(out["seats"], key=lambda e: e["seat"])


def test_non_standing_hand_is_skipped() -> None:
    """A seat not in 3k+1 form (e.g. a 14-tile self-draw winner, were it not
    excluded) is dropped rather than emitting garbage shanten."""
    out = settlement_hand_stats([_seat(0, [*TENPAI, "B6"])], "F1", RS)
    assert out["seats"] == []


def test_payload_is_deterministic_and_json_safe() -> None:
    seats = [_seat(0, TENPAI), _seat(1, ONE_SHANTEN), _seat(2, TWO_SHANTEN)]
    a = settlement_hand_stats(seats, "F1", RS)
    b = settlement_hand_stats(seats, "F1", RS)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
