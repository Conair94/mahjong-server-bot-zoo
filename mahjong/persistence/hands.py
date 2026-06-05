"""Hand index + participants CRUD — SQL primitives.

These functions take a raw ``sqlite3.Connection``.  ``reserve_hand`` and
``finalize_hand`` use ``with conn:`` to guarantee atomicity; read helpers do
not commit (reads never need to).

Spec: docs/specs/persistence-api.md § Public API (hands).
"""

from __future__ import annotations

import sqlite3

from mahjong.persistence.models import AccountStats, HandRow, Participant, ScorePoint

# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def reserve_hand(
    conn: sqlite3.Connection,
    *,
    hand_id: str,
    match_id: str | None,
    hand_index_in_match: int,
    ruleset_id: str,
    ruleset_config_hash: str,
    started_at_ms: int,
    master_seed: str,
    record_path: str,
    server_version: str,
    source: str,
    participants: list[Participant],
) -> None:
    """Atomic INSERT: one ``hand_index`` row + one ``hand_participants`` row
    per participant.

    Called at HEADER write, before any actions.  Either all rows land or none
    do (single transaction).
    """
    with conn:
        conn.execute(
            """
            INSERT INTO hand_index
                (hand_id, match_id, hand_index_in_match, ruleset_id,
                 ruleset_config_hash, started_at_ms, master_seed,
                 record_path, server_version, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hand_id,
                match_id,
                hand_index_in_match,
                ruleset_id,
                ruleset_config_hash,
                started_at_ms,
                master_seed,
                record_path,
                server_version,
                source,
            ),
        )
        for p in participants:
            conn.execute(
                """
                INSERT INTO hand_participants
                    (hand_id, seat, account_id, seat_kind, wind, final_score_delta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    hand_id,
                    p.seat,
                    p.account_id,
                    p.seat_kind,
                    p.wind,
                    p.final_score_delta,
                ),
            )


def finalize_hand(
    conn: sqlite3.Connection,
    hand_id: str,
    *,
    ended_at_ms: int,
    terminal_kind: str,
    winner_seat: int | None,
    fan_total: int | None,
    record_checksum: str,
    participants_scores: dict[int, int],
) -> None:
    """Atomic UPDATE: ``hand_index`` terminals + per-seat ``final_score_delta``.

    Called at FOOTER write, after the hand terminates.
    """
    with conn:
        conn.execute(
            """
            UPDATE hand_index
            SET ended_at_ms     = ?,
                terminal_kind   = ?,
                winner_seat     = ?,
                fan_total       = ?,
                record_checksum = ?
            WHERE hand_id = ?
            """,
            (ended_at_ms, terminal_kind, winner_seat, fan_total, record_checksum, hand_id),
        )
        for seat, delta in participants_scores.items():
            conn.execute(
                """
                UPDATE hand_participants
                SET final_score_delta = ?
                WHERE hand_id = ? AND seat = ?
                """,
                (delta, hand_id, seat),
            )


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_hand(conn: sqlite3.Connection, hand_id: str) -> HandRow | None:
    """Return the HandRow for *hand_id* with participants populated, or None."""
    row = conn.execute(
        """
        SELECT hand_id, match_id, hand_index_in_match, ruleset_id,
               ruleset_config_hash, started_at_ms, ended_at_ms, terminal_kind,
               winner_seat, fan_total, master_seed, record_path,
               record_checksum, server_version, source
        FROM hand_index
        WHERE hand_id = ?
        """,
        (hand_id,),
    ).fetchone()
    if row is None:
        return None
    participants = _fetch_participants(conn, hand_id)
    return _row_to_hand(row, participants)


def find_hands_by_account(
    conn: sqlite3.Connection,
    account_id: int,
    *,
    limit: int = 50,
    before_hand_id: str | None = None,
) -> list[HandRow]:
    """Return hands where *account_id* participated, ordered by started_at_ms DESC.

    Keyset-pagination: *before_hand_id* restricts to hands started before
    that hand's ``started_at_ms``.  Participants list is NOT populated (list
    query — callers call ``get_hand`` on individual rows if they need it).
    """
    if before_hand_id is None:
        rows = conn.execute(
            """
            SELECT hi.hand_id, hi.match_id, hi.hand_index_in_match, hi.ruleset_id,
                   hi.ruleset_config_hash, hi.started_at_ms, hi.ended_at_ms,
                   hi.terminal_kind, hi.winner_seat, hi.fan_total, hi.master_seed,
                   hi.record_path, hi.record_checksum, hi.server_version, hi.source
            FROM hand_index hi
            WHERE hi.hand_id IN (
                SELECT hp.hand_id FROM hand_participants hp WHERE hp.account_id = ?
            )
            ORDER BY hi.started_at_ms DESC
            LIMIT ?
            """,
            (account_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT hi.hand_id, hi.match_id, hi.hand_index_in_match, hi.ruleset_id,
                   hi.ruleset_config_hash, hi.started_at_ms, hi.ended_at_ms,
                   hi.terminal_kind, hi.winner_seat, hi.fan_total, hi.master_seed,
                   hi.record_path, hi.record_checksum, hi.server_version, hi.source
            FROM hand_index hi
            WHERE hi.hand_id IN (
                SELECT hp.hand_id FROM hand_participants hp WHERE hp.account_id = ?
            )
            AND hi.started_at_ms < (
                SELECT started_at_ms FROM hand_index WHERE hand_id = ?
            )
            ORDER BY hi.started_at_ms DESC
            LIMIT ?
            """,
            (account_id, before_hand_id, limit),
        ).fetchall()
    return [_row_to_hand(r) for r in rows]


def find_hands_by_match(conn: sqlite3.Connection, match_id: str) -> list[HandRow]:
    """Return all hands for *match_id* ordered by hand_index_in_match ASC."""
    rows = conn.execute(
        """
        SELECT hand_id, match_id, hand_index_in_match, ruleset_id,
               ruleset_config_hash, started_at_ms, ended_at_ms, terminal_kind,
               winner_seat, fan_total, master_seed, record_path,
               record_checksum, server_version, source
        FROM hand_index
        WHERE match_id = ?
        ORDER BY hand_index_in_match ASC
        """,
        (match_id,),
    ).fetchall()
    return [_row_to_hand(r) for r in rows]


def find_recent_hands(conn: sqlite3.Connection, limit: int = 50) -> list[HandRow]:
    """Return the *limit* most recently started hands, DESC by started_at_ms."""
    rows = conn.execute(
        """
        SELECT hand_id, match_id, hand_index_in_match, ruleset_id,
               ruleset_config_hash, started_at_ms, ended_at_ms, terminal_kind,
               winner_seat, fan_total, master_seed, record_path,
               record_checksum, server_version, source
        FROM hand_index
        ORDER BY started_at_ms DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_row_to_hand(r) for r in rows]


def find_in_progress_hands(conn: sqlite3.Connection) -> list[HandRow]:
    """Return hands where ended_at_ms IS NULL (reserved but not yet finalized)."""
    rows = conn.execute(
        """
        SELECT hand_id, match_id, hand_index_in_match, ruleset_id,
               ruleset_config_hash, started_at_ms, ended_at_ms, terminal_kind,
               winner_seat, fan_total, master_seed, record_path,
               record_checksum, server_version, source
        FROM hand_index
        WHERE ended_at_ms IS NULL
        ORDER BY started_at_ms ASC
        """,
    ).fetchall()
    return [_row_to_hand(r) for r in rows]


# ---------------------------------------------------------------------------
# Profile stats  (profile-and-settings.md § B.1, B.2)
# ---------------------------------------------------------------------------


def account_stats(conn: sqlite3.Connection, account_id: int) -> AccountStats:
    """Aggregate an account's finalized live hands into an AccountStats.

    Counts only finalized (``ended_at_ms`` non-NULL) live-source hands the
    account participated in.  Win-rate / average-win-size are left to the
    caller (raw counts + sums only).
    """
    row = conn.execute(
        """
        SELECT
          COUNT(*)                                                       AS hands_played,
          COALESCE(SUM(hp.final_score_delta), 0)                         AS total_score,
          COALESCE(SUM(hi.winner_seat = hp.seat), 0)                     AS hands_won,
          COALESCE(SUM(hi.terminal_kind = 'EXHAUSTIVE_DRAW'), 0)         AS draws,
          COALESCE(SUM(CASE WHEN hi.winner_seat = hp.seat
                            THEN hp.final_score_delta ELSE 0 END), 0)    AS total_win_points,
          MAX(CASE WHEN hi.winner_seat = hp.seat THEN hi.fan_total END)  AS best_win_fan,
          MIN(hi.started_at_ms)                                          AS first_played_ms,
          MAX(hi.started_at_ms)                                          AS last_played_ms
        FROM hand_participants hp
        JOIN hand_index hi ON hi.hand_id = hp.hand_id
        WHERE hp.account_id = ?
          AND hi.ended_at_ms IS NOT NULL
          AND hp.final_score_delta IS NOT NULL
          AND hi.source = 'live'
        """,
        (account_id,),
    ).fetchone()
    return AccountStats(
        account_id=account_id,
        hands_played=row["hands_played"],
        hands_won=row["hands_won"],
        draws=row["draws"],
        total_score=row["total_score"],
        total_win_points=row["total_win_points"],
        best_win_fan=row["best_win_fan"],
        first_played_ms=row["first_played_ms"],
        last_played_ms=row["last_played_ms"],
    )


def account_score_series(
    conn: sqlite3.Connection, account_id: int, *, limit: int = 200
) -> list[ScorePoint]:
    """Cumulative score after each of the account's most-recent *limit* hands.

    Returns points oldest→newest.  The running total is relative to the start
    of the returned window (v1; absolute-lifetime baseline is an open question
    in the spec).
    """
    rows = conn.execute(
        """
        SELECT hi.ended_at_ms AS ended_at_ms, hp.final_score_delta AS delta
        FROM hand_participants hp
        JOIN hand_index hi ON hi.hand_id = hp.hand_id
        WHERE hp.account_id = ?
          AND hi.ended_at_ms IS NOT NULL
          AND hp.final_score_delta IS NOT NULL
          AND hi.source = 'live'
        ORDER BY hi.ended_at_ms DESC
        LIMIT ?
        """,
        (account_id, limit),
    ).fetchall()
    # Fetched newest-first for the cap; reverse to oldest-first and accumulate.
    series: list[ScorePoint] = []
    running = 0
    for r in reversed(rows):
        running += r["delta"]
        series.append(ScorePoint(ended_at_ms=r["ended_at_ms"], cumulative=running))
    return series


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_participants(conn: sqlite3.Connection, hand_id: str) -> list[Participant]:
    rows = conn.execute(
        """
        SELECT seat, account_id, seat_kind, wind, final_score_delta
        FROM hand_participants
        WHERE hand_id = ?
        ORDER BY seat ASC
        """,
        (hand_id,),
    ).fetchall()
    return [
        Participant(
            seat=r["seat"],
            account_id=r["account_id"],
            seat_kind=r["seat_kind"],
            wind=r["wind"],
            final_score_delta=r["final_score_delta"],
        )
        for r in rows
    ]


def _row_to_hand(
    row: sqlite3.Row,
    participants: list[Participant] | None = None,
) -> HandRow:
    return HandRow(
        hand_id=row["hand_id"],
        match_id=row["match_id"],
        hand_index_in_match=row["hand_index_in_match"],
        ruleset_id=row["ruleset_id"],
        ruleset_config_hash=row["ruleset_config_hash"],
        started_at_ms=row["started_at_ms"],
        ended_at_ms=row["ended_at_ms"],
        terminal_kind=row["terminal_kind"],
        winner_seat=row["winner_seat"],
        fan_total=row["fan_total"],
        master_seed=row["master_seed"],
        record_path=row["record_path"],
        record_checksum=row["record_checksum"],
        server_version=row["server_version"],
        source=row["source"],
        participants=participants if participants is not None else [],
    )


__all__ = [
    "account_score_series",
    "account_stats",
    "finalize_hand",
    "find_hands_by_account",
    "find_hands_by_match",
    "find_in_progress_hands",
    "find_recent_hands",
    "get_hand",
    "reserve_hand",
]
