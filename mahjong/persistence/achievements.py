"""Achievements: derive-at-read over hand_index + hand_participants.

Spec: docs/specs/achievements.md.

No new tables and no settlement-path writes — every badge is recomputed from
the same finalized/live-source rows `account_stats` aggregates, so the set
is idempotent and retroactive over all recorded history. Fan *names* are not
persisted, so the catalog is numeric-metric only (spec § Non-goals).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from mahjong.persistence.hands import account_stats


@dataclass(frozen=True)
class _Def:
    id: str
    name: str
    desc: str
    target: int
    metric: str  # key into the metrics dict built per account


# Catalog order is the wire order (spec § Shapes) — keep stable.
_CATALOG: tuple[_Def, ...] = (
    _Def("first-win", "First Blood", "Win a hand", 1, "wins"),
    _Def("wins-10", "Seasoned", "Win 10 hands", 10, "wins"),
    _Def("wins-50", "Master", "Win 50 hands", 50, "wins"),
    _Def("wins-100", "Legend", "Win 100 hands", 100, "wins"),
    _Def("hands-50", "Regular", "Play 50 hands", 50, "hands"),
    _Def("hands-200", "Resident", "Play 200 hands", 200, "hands"),
    _Def("hands-500", "Lifer", "Play 500 hands", 500, "hands"),
    _Def("fan-8", "Big Hand", "Win with 8+ fan", 8, "best_fan"),
    _Def("fan-16", "Monster Hand", "Win with 16+ fan", 16, "best_fan"),
    _Def("fan-24", "Limit Break", "Win with 24+ fan", 24, "best_fan"),
    _Def("streak-3", "Hot Streak", "Win 3 hands in a row", 3, "streak"),
    _Def("streak-5", "Unstoppable", "Win 5 hands in a row", 5, "streak"),
    _Def("in-the-black", "In the Black", "20+ hands played with a positive lifetime score", 20, "in_black"),
    _Def("draws-10", "Wall Warrior", "Survive 10 exhaustive draws", 10, "draws"),
)


def account_achievements(conn: sqlite3.Connection, account_id: int) -> list[dict[str, Any]]:
    """The full catalog with earned/progress for *account_id* (spec § Shapes).

    `progress` is clamped to `target` once earned; order is catalog order.
    """
    s = account_stats(conn, account_id)
    streak = _longest_win_streak(conn, account_id)
    metrics: dict[str, int] = {
        "wins": s.hands_won,
        "hands": s.hands_played,
        "best_fan": s.best_win_fan or 0,
        "streak": streak,
        "draws": s.draws,
        # in-the-black: progress counts hands toward the 20-hand leg; the
        # positive-total leg gates `earned` (an account can regress out).
        "in_black": s.hands_played,
    }
    extra_gate: dict[str, Callable[[], bool]] = {
        "in_black": lambda: s.total_score > 0,
    }

    out: list[dict[str, Any]] = []
    for d in _CATALOG:
        value = metrics[d.metric]
        earned = value >= d.target and extra_gate.get(d.metric, lambda: True)()
        out.append(
            {
                "id": d.id,
                "name": d.name,
                "desc": d.desc,
                "earned": earned,
                "progress": min(value, d.target),
                "target": d.target,
            }
        )
    return out


def _longest_win_streak(conn: sqlite3.Connection, account_id: int) -> int:
    """Longest consecutive-win run over the account's finalized live hands in
    `started_at_ms` order. Draws and losses both break the run (spec
    § Catalog). Linear scan in Python — a friends-server account has at most
    a few thousand rows, and SQL window gymnastics aren't worth it."""
    rows = conn.execute(
        """
        SELECT (hi.winner_seat = hp.seat) AS won
        FROM hand_participants hp
        JOIN hand_index hi ON hi.hand_id = hp.hand_id
        WHERE hp.account_id = ?
          AND hi.ended_at_ms IS NOT NULL
          AND hp.final_score_delta IS NOT NULL
          AND hi.source = 'live'
        ORDER BY hi.started_at_ms ASC
        """,
        (account_id,),
    ).fetchall()
    best = run = 0
    for row in rows:
        run = run + 1 if row["won"] else 0
        best = max(best, run)
    return best


__all__ = ["account_achievements"]
