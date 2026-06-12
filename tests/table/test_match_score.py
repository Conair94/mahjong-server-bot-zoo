"""Cross-hand cumulative match scoring (Spec 40).

Pins the reward-adjacent contract: a table's running per-seat total is the sum
of each hand's zero-sum ``score_delta``, and the standings series gains exactly
one point per completed hand — including draws (which move no points but still
count as a hand played).
"""

from __future__ import annotations

from mahjong.table.match_score import MatchScore, hand_deltas


def _terminal(deltas: list[int]) -> dict[str, object]:
    return {"kind": "HAND_END", "score_delta": deltas}


def test_hand_deltas_reads_terminal() -> None:
    assert hand_deltas(_terminal([24, -8, -8, -8])) == [24, -8, -8, -8]


def test_hand_deltas_draw_and_malformed_are_zero() -> None:
    # No terminal (hand task crashed / aborted), a draw with explicit zeros,
    # and a malformed delta all contribute nothing.
    assert hand_deltas(None) == [0, 0, 0, 0]
    assert hand_deltas(_terminal([0, 0, 0, 0])) == [0, 0, 0, 0]
    assert hand_deltas({"kind": "HAND_END"}) == [0, 0, 0, 0]  # missing
    assert hand_deltas({"score_delta": [1, 2, 3]}) == [0, 0, 0, 0]  # wrong length


def test_accumulates_across_hands() -> None:
    ms = MatchScore()
    assert ms.cumulative == [0, 0, 0, 0]
    assert ms.series == []

    ms.record_hand(_terminal([24, -8, -8, -8]))
    assert ms.cumulative == [24, -8, -8, -8]
    assert ms.series == [[24, -8, -8, -8]]

    ms.record_hand(_terminal([-8, 24, -8, -8]))
    assert ms.cumulative == [16, 16, -16, -16]
    assert ms.series == [[24, -8, -8, -8], [16, 16, -16, -16]]

    # Every hand's deltas sum to zero, so the running total does too.
    assert sum(ms.cumulative) == 0


def test_draw_still_appends_a_series_point() -> None:
    ms = MatchScore()
    ms.record_hand(_terminal([24, -8, -8, -8]))
    ms.record_hand(None)  # drawn / aborted hand
    assert ms.cumulative == [24, -8, -8, -8]  # unchanged
    assert ms.series == [[24, -8, -8, -8], [24, -8, -8, -8]]  # but counted


def test_series_rows_are_independent_snapshots() -> None:
    # A later mutation must not retroactively change an earlier series row.
    ms = MatchScore()
    ms.record_hand(_terminal([8, -8, 0, 0]))
    first = ms.series[0]
    ms.record_hand(_terminal([8, -8, 0, 0]))
    assert first == [8, -8, 0, 0]


def test_to_wire_shape() -> None:
    ms = MatchScore()
    ms.record_hand(_terminal([24, -8, -8, -8]))
    ms.record_hand(_terminal([-8, 24, -8, -8]))
    wire = ms.to_wire()
    assert wire == {
        "cumulative": [16, 16, -16, -16],
        "series": [[24, -8, -8, -8], [16, 16, -16, -16]],
        "hands_complete": 2,
    }
    # Defensive copy: mutating the wire dict must not corrupt internal state.
    wire["cumulative"][0] = 999
    assert ms.cumulative[0] == 16
