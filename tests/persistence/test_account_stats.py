"""Profile stat aggregation — account_stats + account_score_series.

Spec: docs/specs/profile-and-settings.md § B.1, B.2 (verification fixtures 1-6).

These pin the read-only aggregation over hand_index + hand_participants that
the profile home page renders.  A silent stat bug (wrong join, counting
in-progress / selfplay hands, wrong winner comparison) is exactly the
"plausible-but-wrong number" failure the project's verification discipline
exists to catch — so every count/sum is pinned against hand-computed expected
values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mahjong.persistence import Persistence
from mahjong.persistence.models import Participant

# ---------------------------------------------------------------------------
# Fixtures + seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "records").mkdir()
    return tmp_path


@pytest.fixture()
def p(data_dir: Path) -> Persistence:
    return Persistence(":memory:", data_dir)


@pytest.fixture()
def account_id(p: Persistence) -> int:
    return p.insert_account(
        username="connor",
        display_name="Connor",
        kind="human",
        role="user",
        password_hash="x",
    )


_counter = 0


def _seed_hand(
    p: Persistence,
    *,
    account_id: int,
    seat: int = 0,
    winner_seat: int | None,
    score_deltas: dict[int, int],
    fan_total: int | None,
    started_at_ms: int,
    terminal_kind: str = "HU",
    source: str = "live",
    finalize: bool = True,
) -> str:
    """Reserve + (optionally) finalize one hand with *account_id* at *seat*.

    The other three seats are bot/canned with no account.  Returns the hand_id.
    """
    global _counter
    _counter += 1
    hand_id = f"hand-{_counter:04d}"
    participants = [
        Participant(
            seat=s,
            account_id=account_id if s == seat else None,
            seat_kind="human" if s == seat else "bot",
            wind=f"F{s + 1}",
            final_score_delta=None,
        )
        for s in range(4)
    ]
    p.reserve_hand(
        hand_id=hand_id,
        match_id=None,
        hand_index_in_match=0,
        ruleset_id="mcr-2006",
        ruleset_config_hash="abc123",
        started_at_ms=started_at_ms,
        master_seed="0x1",
        record_path=f"records/{hand_id}.jsonl",
        server_version="0.0.1",
        source=source,
        participants=participants,
    )
    if finalize:
        p.finalize_hand(
            hand_id,
            ended_at_ms=started_at_ms + 60_000,
            terminal_kind=terminal_kind,
            winner_seat=winner_seat,
            fan_total=fan_total,
            record_checksum="cs",
            participants_scores=score_deltas,
        )
    return hand_id


# ---------------------------------------------------------------------------
# account_stats
# ---------------------------------------------------------------------------


def test_empty_account_zeroed(p: Persistence, account_id: int) -> None:
    """Fixture 1: no hands → zeros + None timestamps, no crash."""
    s = p.account_stats(account_id)
    assert s.hands_played == 0
    assert s.hands_won == 0
    assert s.draws == 0
    assert s.total_score == 0
    assert s.total_win_points == 0
    assert s.best_win_fan is None
    assert s.first_played_ms is None
    assert s.last_played_ms is None


def test_win_loss_draw_counts(p: Persistence, account_id: int) -> None:
    """Fixture 2: one win, one loss, one draw → counts + total_score."""
    # Win: account at seat 0 is the winner.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=0,
        score_deltas={0: 24, 1: -8, 2: -8, 3: -8},
        fan_total=8,
        started_at_ms=1000,
    )
    # Loss: account at seat 0, seat 1 wins.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=1,
        score_deltas={0: -8, 1: 24, 2: -8, 3: -8},
        fan_total=8,
        started_at_ms=2000,
    )
    # Draw.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=None,
        score_deltas={0: 0, 1: 0, 2: 0, 3: 0},
        fan_total=None,
        started_at_ms=3000,
        terminal_kind="EXHAUSTIVE_DRAW",
    )
    s = p.account_stats(account_id)
    assert s.hands_played == 3
    assert s.hands_won == 1
    assert s.draws == 1
    assert s.total_score == 24 + (-8) + 0
    assert s.first_played_ms == 1000
    assert s.last_played_ms == 3000


def test_win_points_and_best_fan_only_count_wins(p: Persistence, account_id: int) -> None:
    """Fixture 3: total_win_points / best_win_fan ignore losing hands."""
    # Win with fan 12.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=0,
        score_deltas={0: 40, 1: -16, 2: -16, 3: -8},
        fan_total=12,
        started_at_ms=1000,
    )
    # Win with fan 6.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=0,
        score_deltas={0: 20, 1: -8, 2: -8, 3: -4},
        fan_total=6,
        started_at_ms=2000,
    )
    # Big loss — must not pollute total_win_points or best_win_fan.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=2,
        score_deltas={0: -32, 1: -8, 2: 48, 3: -8},
        fan_total=24,
        started_at_ms=3000,
    )
    s = p.account_stats(account_id)
    assert s.hands_won == 2
    assert s.total_win_points == 40 + 20
    assert s.best_win_fan == 12


def test_in_progress_and_nonlive_excluded(p: Persistence, account_id: int) -> None:
    """Fixture 4: unfinalized + non-live hands are not counted."""
    # Counted: a normal live finalized win.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=0,
        score_deltas={0: 24, 1: -8, 2: -8, 3: -8},
        fan_total=8,
        started_at_ms=1000,
    )
    # Not counted: reserved but never finalized (ended_at_ms NULL).
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=None,
        score_deltas={},
        fan_total=None,
        started_at_ms=2000,
        finalize=False,
    )
    # Not counted: selfplay source.
    _seed_hand(
        p,
        account_id=account_id,
        seat=0,
        winner_seat=0,
        score_deltas={0: 24, 1: -8, 2: -8, 3: -8},
        fan_total=8,
        started_at_ms=3000,
        source="selfplay",
    )
    s = p.account_stats(account_id)
    assert s.hands_played == 1
    assert s.total_score == 24


# ---------------------------------------------------------------------------
# account_score_series
# ---------------------------------------------------------------------------


def test_score_series_cumulative_ascending(p: Persistence, account_id: int) -> None:
    """Fixture 5: series is running-sum, ordered oldest→newest."""
    for ts, delta in ((1000, -24), (2000, 48), (3000, 10)):
        won = delta > 0
        _seed_hand(
            p,
            account_id=account_id,
            seat=0,
            winner_seat=0 if won else 1,
            score_deltas={0: delta, 1: -delta, 2: 0, 3: 0},
            fan_total=8 if won else None,
            started_at_ms=ts,
        )
    series = p.account_score_series(account_id)
    assert [pt.cumulative for pt in series] == [-24, 24, 34]
    assert [pt.ended_at_ms for pt in series] == [61000, 62000, 63000]


def test_score_series_window_cap(p: Persistence, account_id: int) -> None:
    """Fixture 6: limit returns the most recent `limit` points, oldest first."""
    for i in range(250):
        _seed_hand(
            p,
            account_id=account_id,
            seat=0,
            winner_seat=0,
            score_deltas={0: 1, 1: -1, 2: 0, 3: 0},
            fan_total=8,
            started_at_ms=1000 + i,
        )
    series = p.account_score_series(account_id, limit=200)
    assert len(series) == 200
    # Oldest of the 200 first; ended_at_ms strictly ascending.
    ts = [pt.ended_at_ms for pt in series]
    assert ts == sorted(ts)
    # The window is the last 200 of 250 hands → starts at hand index 50.
    assert ts[0] == (1000 + 50) + 60_000
