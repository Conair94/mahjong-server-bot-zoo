"""Cross-hand cumulative match scoring (Spec 40 — in-game scoreboard).

A table is an open-ended sequence of hands.  Each hand's terminal carries a
per-seat ``score_delta`` (the engine's zero-sum per-hand settlement).  The
running *match* total is just the sum of those deltas across the hands played
since the table was created — a **display** concern, not a game-end rule, and
not persisted.

Deliberately separate from ``engine/scoring.py`` (which converts fan -> the
per-hand delta): this module never computes a delta, it only accumulates the
ones the engine already produced.  It is pure and stateless w.r.t. the engine,
so it is cheap to unit-test and is shared by both hand loops (the multi-table
``TableHandle`` and the single-table ``WebOrchestrator``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SEATS = 4


def hand_deltas(terminal: Mapping[str, Any] | None) -> list[int]:
    """Per-seat point change for one finished hand.

    A draw, an aborted hand (no terminal), or a malformed ``score_delta``
    contributes all zeros: the hand still *counts* as played (it becomes a
    series point), it simply moves no points.
    """
    deltas = terminal.get("score_delta") if terminal else None
    if (
        isinstance(deltas, Sequence)
        and not isinstance(deltas, (str, bytes))
        and len(deltas) == SEATS
    ):
        return [int(d) for d in deltas]
    return [0] * SEATS


class MatchScore:
    """Running per-seat totals plus the standings-after-each-hand series.

    ``series[i]`` is a snapshot of ``cumulative`` after completed hand ``i``
    (0-based); seat ``p``'s line for the score graph is
    ``[series[0][p], series[1][p], …]``.  Both lists are returned as defensive
    copies on the wire so a consumer can never mutate internal state.
    """

    def __init__(self) -> None:
        self.cumulative: list[int] = [0] * SEATS
        self.series: list[list[int]] = []

    def record_hand(self, terminal: Mapping[str, Any] | None) -> None:
        """Fold one hand's terminal into the totals and append a series point."""
        for seat, delta in enumerate(hand_deltas(terminal)):
            self.cumulative[seat] += delta
        self.series.append(list(self.cumulative))  # snapshot, not a reference

    def to_wire(self) -> dict[str, Any]:
        """The ``match_scores`` block carried on each per-seat snapshot."""
        return {
            "cumulative": list(self.cumulative),
            "series": [list(row) for row in self.series],
            "hands_complete": len(self.series),
        }
