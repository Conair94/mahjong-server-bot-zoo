"""Typed return types for the persistence layer.

All query helpers materialise SQLite rows into one of these frozen dataclasses
so callers never depend on column order and mypy can check attribute names.

Spec: docs/specs/persistence-api.md § Return types.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

# ---------------------------------------------------------------------------
# Account / session types  (consumed by auth.md)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Account:
    account_id: int
    username: str
    display_name: str
    kind: Literal["human", "bot"]
    role: Literal["user", "admin"]
    password_hash: str
    disabled: bool
    created_at_ms: int
    last_login_ms: int | None


@dataclasses.dataclass(frozen=True)
class SessionRow:
    session_id: str
    account_id: int
    issued_at_ms: int
    expires_at_ms: int
    last_seen_ms: int
    revoked: bool
    user_agent: str | None


@dataclasses.dataclass(frozen=True)
class InviteRow:
    """An invite-code row (public-deployment.md § 24.2)."""

    code: str
    created_by: int
    created_at_ms: int
    expires_at_ms: int | None
    max_uses: int
    used_count: int
    disabled: bool


# ---------------------------------------------------------------------------
# Hand / participant types  (consumed by table manager + queries)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Participant:
    seat: int
    account_id: int | None
    seat_kind: Literal["human", "bot", "canned"]
    wind: str  # "F1" | "F2" | "F3" | "F4"
    final_score_delta: int | None


@dataclasses.dataclass(frozen=True)
class HandRow:
    hand_id: str
    match_id: str | None
    hand_index_in_match: int
    ruleset_id: str
    ruleset_config_hash: str
    started_at_ms: int
    ended_at_ms: int | None
    terminal_kind: Literal["HU", "EXHAUSTIVE_DRAW", "ABORTED"] | None
    winner_seat: int | None
    fan_total: int | None
    master_seed: str
    record_path: str
    record_checksum: str | None
    server_version: str
    source: Literal["live", "selfplay", "replay-import"]
    # Populated only by get_hand(); list queries leave this empty.
    participants: list[Participant] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Profile stat types  (consumed by profile-and-settings.md § B.1, B.2)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AccountStats:
    """Per-account play aggregate over finalized live hands.

    Raw counts/sums only; win-rate and average-win-size are derived by the
    caller (the wire contract sends these and the client formats the ratios)
    so there is one source of truth and no float-rounding in the protocol.
    """

    account_id: int
    hands_played: int
    hands_won: int
    draws: int
    total_score: int
    total_win_points: int
    best_win_fan: int | None
    first_played_ms: int | None
    last_played_ms: int | None


@dataclasses.dataclass(frozen=True)
class ScorePoint:
    """One point on the cumulative point-performance graph."""

    ended_at_ms: int
    cumulative: int


# ---------------------------------------------------------------------------
# Integrity / rebuild report types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class IntegrityReport:
    """Result of Persistence.integrity_check()."""

    pragma_ok: bool
    checked_db: int           # rows examined in hand_index
    ok_files: int             # record files present + checksum OK
    missing_files: int        # DB rows whose record file is absent
    checksum_mismatches: int  # finalized rows where recomputed hash ≠ stored
    orphaned_files: int       # record files with no hand_index row
    in_progress_hands: int    # rows without ended_at_ms (not yet finalized)

    @property
    def ok(self) -> bool:
        """True iff the DB is clean and no critical problems were found.

        Warnings (orphaned / in-progress hands) are not fatal.
        Critical failures: pragma not ok, missing files, checksum mismatches.
        """
        return (
            self.pragma_ok
            and self.missing_files == 0
            and self.checksum_mismatches == 0
        )


@dataclasses.dataclass(frozen=True)
class RebuildReport:
    """Result of Persistence.rebuild_index_from_records()."""

    processed_files: int
    inserted: int
    updated: int   # rows that already existed and were refreshed
    errors: int    # files that could not be parsed


__all__ = [
    "Account",
    "AccountStats",
    "HandRow",
    "IntegrityReport",
    "InviteRow",
    "Participant",
    "RebuildReport",
    "ScorePoint",
    "SessionRow",
]
