"""Achievements (Spec 39): derive-at-read over hand_index + hand_participants.

Spec: docs/specs/achievements.md § Verification fixtures 1–6. Reuses the
seeding helper from test_account_stats (same finalized/live filter is the
contract under test).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mahjong.persistence import Persistence
from tests.persistence.test_account_stats import _seed_hand


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
        username="connor", display_name="Connor", kind="human", role="user", password_hash="x"
    )


def _by_id(achievements: list[dict]) -> dict[str, dict]:
    return {a["id"]: a for a in achievements}


def _win(p: Persistence, account_id: int, *, ts: int, fan: int = 8) -> None:
    _seed_hand(
        p, account_id=account_id, seat=0, winner_seat=0,
        score_deltas={0: 24, 1: -8, 2: -8, 3: -8}, fan_total=fan, started_at_ms=ts,
    )


def _loss(p: Persistence, account_id: int, *, ts: int, fan: int = 8, delta: int = -8) -> None:
    _seed_hand(
        p, account_id=account_id, seat=0, winner_seat=1,
        score_deltas={0: delta, 1: 24, 2: -8, 3: -8}, fan_total=fan, started_at_ms=ts,
    )


def _draw(p: Persistence, account_id: int, *, ts: int) -> None:
    _seed_hand(
        p, account_id=account_id, seat=0, winner_seat=None,
        score_deltas={0: 0, 1: 0, 2: 0, 3: 0}, fan_total=None, started_at_ms=ts,
        terminal_kind="EXHAUSTIVE_DRAW",
    )


def test_empty_account_all_unearned_zero_progress(p: Persistence, account_id: int) -> None:
    """Fixture 1."""
    achievements = p.account_achievements(account_id)
    assert achievements, "catalog must not be empty"
    for a in achievements:
        assert a["earned"] is False
        assert a["progress"] == 0
        assert a["target"] > 0
        assert set(a) >= {"id", "name", "desc", "earned", "progress", "target"}


def test_win_threshold_boundary(p: Persistence, account_id: int) -> None:
    """Fixture 2: 9 wins -> wins-10 unearned 9/10; the 10th flips it."""
    for i in range(9):
        _win(p, account_id, ts=1000 + i * 10)
        _loss(p, account_id, ts=1005 + i * 10)  # break streaks; keep wins the metric

    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["first-win"]["earned"] is True
    assert by_id["first-win"]["progress"] == 1  # clamped at target
    assert by_id["wins-10"] == {**by_id["wins-10"], "earned": False, "progress": 9, "target": 10}

    _win(p, account_id, ts=5000)
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["wins-10"]["earned"] is True
    assert by_id["wins-10"]["progress"] == 10


def test_streak_is_longest_run_and_draw_breaks_it(p: Persistence, account_id: int) -> None:
    """Fixture 3: W W L W W W -> streak-3 earned, streak-5 at 3/5. Then a
    draw between wins breaks a would-be run the same as a loss."""
    pattern = ["W", "W", "L", "W", "W", "W"]
    for i, r in enumerate(pattern):
        (_win if r == "W" else _loss)(p, account_id, ts=1000 + i * 10)

    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["streak-3"]["earned"] is True
    assert by_id["streak-5"]["earned"] is False
    assert by_id["streak-5"]["progress"] == 3  # longest, not current

    # Append W D W: full sequence is W W L W W W W D W. The draw breaks the
    # run exactly like a loss, so the runs are 2, 4, 1 → longest 4.
    for i, r in enumerate(["W", "D", "W"], start=10):
        if r == "D":
            _draw(p, account_id, ts=1000 + i * 10)
        else:
            _win(p, account_id, ts=1000 + i * 10)
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["streak-5"]["progress"] == 4
    assert by_id["streak-5"]["earned"] is False


def test_fan_tiers_use_best_winning_fan_only(p: Persistence, account_id: int) -> None:
    """Fixture 4: a 24-fan LOSS earns nothing; a 16-fan win earns fan-8 and
    fan-16 but not fan-24."""
    _loss(p, account_id, ts=1000, fan=24)
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["fan-8"]["earned"] is False
    assert by_id["fan-8"]["progress"] == 0

    _win(p, account_id, ts=2000, fan=16)
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["fan-8"]["earned"] is True
    assert by_id["fan-16"]["earned"] is True
    assert by_id["fan-24"]["earned"] is False
    assert by_id["fan-24"]["progress"] == 16


def test_in_the_black_needs_both_legs(p: Persistence, account_id: int) -> None:
    """Fixture 5: positive total at 19 hands -> unearned; at 20 -> earned;
    then drive the total negative -> unearned again."""
    _win(p, account_id, ts=500)  # +24 cushion
    for i in range(18):
        _draw(p, account_id, ts=1000 + i * 10)  # 19 hands, total +24
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["in-the-black"]["earned"] is False
    assert by_id["in-the-black"]["progress"] == 19

    _draw(p, account_id, ts=2000)  # 20th hand, total still +24
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["in-the-black"]["earned"] is True

    _loss(p, account_id, ts=3000, delta=-100)  # total -76
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["in-the-black"]["earned"] is False


def test_draws_count_toward_wall_warrior(p: Persistence, account_id: int) -> None:
    for i in range(10):
        _draw(p, account_id, ts=1000 + i)
    by_id = _by_id(p.account_achievements(account_id))
    assert by_id["draws-10"]["earned"] is True
    assert by_id["draws-10"]["progress"] == 10


def test_unfinalized_and_selfplay_hands_count_toward_nothing(
    p: Persistence, account_id: int
) -> None:
    """Fixture 6: same exclusions as account_stats."""
    _seed_hand(
        p, account_id=account_id, seat=0, winner_seat=None,
        score_deltas={}, fan_total=None, started_at_ms=1000, finalize=False,
    )
    _seed_hand(
        p, account_id=account_id, seat=0, winner_seat=0,
        score_deltas={0: 24, 1: -8, 2: -8, 3: -8}, fan_total=24, started_at_ms=2000,
        source="selfplay",
    )
    by_id = _by_id(p.account_achievements(account_id))
    assert all(a["progress"] == 0 and not a["earned"] for a in by_id.values())


def test_catalog_order_is_stable(p: Persistence, account_id: int) -> None:
    """The wire order is the catalog order — the client renders verbatim."""
    a1 = [a["id"] for a in p.account_achievements(account_id)]
    _win(p, account_id, ts=1000)
    a2 = [a["id"] for a in p.account_achievements(account_id)]
    assert a1 == a2
