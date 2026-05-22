"""Tests for `mahjong.selfplay.eval` — eval-summary aggregator.

Spec: docs/specs/selfplay-harness.md § Eval-summary output, fixture 6.

TDD approach: write the failing tests first, then implement eval.py to pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mahjong.selfplay.eval import (
    EvalSummary,
    HandOutcome,
    SeatSummary,
    aggregate,
    format_summary,
    parse_record,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_record(
    path: Path,
    *,
    bot_ids: list[str],
    kind: str = "HU",
    winner: list[int] | None = None,
    deal_in_seat: int | None = None,
    fan_total: int = 0,
    score_delta: list[int] | None = None,
    master_seed: str | None = "0xdeadbeef",
    hand_index: int = 0,
    ruleset: str = "mcr-2006",
) -> None:
    """Write a minimal JSONL record for eval testing (HEADER + HAND_END + FOOTER)."""
    if winner is None:
        winner = []
    if score_delta is None:
        score_delta = [0, 0, 0, 0]

    header: dict = {
        "event": "HEADER",
        "seq": 0,
        "hand_id": f"test-{hand_index:04d}",
        "ruleset": {"id": ruleset},
        "seats": [
            {
                "seat": i,
                "wind": f"F{i + 1}",
                "identity": {"kind": "bot", "bot_id": bot_ids[i], "version": "0.0.0"},
            }
            for i in range(4)
        ],
        "meta": {
            "master_seed": master_seed,
            "hand_index": hand_index,
            "source": "selfplay",
        },
    }
    hand_end: dict = {
        "event": "HAND_END",
        "seq": 2,
        "kind": kind,
        "winner": winner,
        "deal_in_seat": deal_in_seat,
        "fan_total": fan_total,
        "score_delta": score_delta,
    }
    footer: dict = {"event": "FOOTER", "seq": 3}

    with path.open("w") as fh:
        fh.write(json.dumps(header) + "\n")
        fh.write(json.dumps(hand_end) + "\n")
        fh.write(json.dumps(footer) + "\n")


# ---------------------------------------------------------------------------
# parse_record
# ---------------------------------------------------------------------------


def test_parse_record_hu_win(tmp_path: Path) -> None:
    p = tmp_path / "hand.jsonl"
    _write_record(
        p,
        bot_ids=["b_rule_v1", "b_random", "b_rule_v1", "b_random"],
        kind="HU",
        winner=[0],
        deal_in_seat=1,
        fan_total=14,
        score_delta=[38, -22, -8, -8],
    )
    outcome = parse_record(p)
    assert outcome is not None
    assert outcome.bot_ids == ["b_rule_v1", "b_random", "b_rule_v1", "b_random"]
    assert outcome.kind == "HU"
    assert outcome.winners == [0]
    assert outcome.deal_in_seat == 1
    assert outcome.fan_total == 14
    assert outcome.score_delta == [38, -22, -8, -8]


def test_parse_record_wall_exhausted(tmp_path: Path) -> None:
    p = tmp_path / "draw.jsonl"
    _write_record(
        p,
        bot_ids=["b_rule_v1", "b_random", "b_rule_v1", "b_random"],
        kind="DRAW",
        winner=[],
        deal_in_seat=None,
        fan_total=0,
        score_delta=[0, 0, 0, 0],
    )
    outcome = parse_record(p)
    assert outcome is not None
    assert outcome.kind == "DRAW"
    assert outcome.winners == []
    assert outcome.deal_in_seat is None
    assert outcome.fan_total == 0


def test_parse_record_missing_hand_end_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "partial.jsonl"
    # Write only HEADER + FOOTER (no HAND_END) — should return None
    header = {
        "event": "HEADER",
        "seq": 0,
        "hand_id": "test",
        "ruleset": {"id": "mcr-2006"},
        "seats": [
            {"seat": i, "wind": f"F{i+1}", "identity": {"kind": "bot", "bot_id": "b_x"}}
            for i in range(4)
        ],
        "meta": None,
    }
    footer = {"event": "FOOTER", "seq": 1}
    with p.open("w") as fh:
        fh.write(json.dumps(header) + "\n")
        fh.write(json.dumps(footer) + "\n")
    assert parse_record(p) is None


def test_parse_record_empty_file_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert parse_record(p) is None


# ---------------------------------------------------------------------------
# aggregate — spec fixture 6: hand-computed reference
# ---------------------------------------------------------------------------

# Two hands, same bot assignment each hand:
#   Hand 0: seat 0 wins by DISCARD from seat 1, fan=14, delta=[+38, -22, -8, -8]
#   Hand 1: seat 2 wins by DISCARD from seat 3, fan=8,  delta=[-6, -6, +18, -6]
#
# Per-seat ground truth:
#   seat 0 (b_rule_v1): 1 win / 2 hands, 0 deal-ins, score=38-6=32, fan_won=14
#   seat 1 (b_random):  0 wins / 2 hands, 1 deal-in,  score=-22-6=-28
#   seat 2 (b_rule_v1): 1 win / 2 hands, 0 deal-ins, score=-8+18=10, fan_won=8
#   seat 3 (b_random):  0 wins / 2 hands, 1 deal-in,  score=-8-6=-14
#
# Per-bot (b_rule_v1 at seats 0 & 2; b_random at seats 1 & 3):
#   b_rule_v1: 4 appearances, 2 wins, 0 deal-ins, score=32+10=42, fan_won=14+8=22
#   b_random:  4 appearances, 0 wins, 2 deal-ins, score=-28-14=-42


@pytest.fixture()
def two_hand_dir(tmp_path: Path) -> Path:
    _write_record(
        tmp_path / "hand-0.jsonl",
        bot_ids=["b_rule_v1", "b_random", "b_rule_v1", "b_random"],
        kind="HU",
        winner=[0],
        deal_in_seat=1,
        fan_total=14,
        score_delta=[38, -22, -8, -8],
        hand_index=0,
    )
    _write_record(
        tmp_path / "hand-1.jsonl",
        bot_ids=["b_rule_v1", "b_random", "b_rule_v1", "b_random"],
        kind="HU",
        winner=[2],
        deal_in_seat=3,
        fan_total=8,
        score_delta=[-6, -6, 18, -6],
        hand_index=1,
    )
    return tmp_path


def test_aggregate_total_hands(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.total_hands == 2


def test_aggregate_per_seat_win_rate(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.per_seat[0].win_rate == pytest.approx(0.5)
    assert summary.per_seat[1].win_rate == pytest.approx(0.0)
    assert summary.per_seat[2].win_rate == pytest.approx(0.5)
    assert summary.per_seat[3].win_rate == pytest.approx(0.0)


def test_aggregate_per_seat_avg_score(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.per_seat[0].avg_score == pytest.approx(32 / 2)
    assert summary.per_seat[1].avg_score == pytest.approx(-28 / 2)
    assert summary.per_seat[2].avg_score == pytest.approx(10 / 2)
    assert summary.per_seat[3].avg_score == pytest.approx(-14 / 2)


def test_aggregate_per_seat_deal_in_rate(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.per_seat[0].deal_in_rate == pytest.approx(0.0)
    assert summary.per_seat[1].deal_in_rate == pytest.approx(0.5)
    assert summary.per_seat[2].deal_in_rate == pytest.approx(0.0)
    assert summary.per_seat[3].deal_in_rate == pytest.approx(0.5)


def test_aggregate_per_seat_avg_fan_when_won(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.per_seat[0].avg_fan_when_won == pytest.approx(14.0)
    assert summary.per_seat[1].avg_fan_when_won == pytest.approx(0.0)  # no wins
    assert summary.per_seat[2].avg_fan_when_won == pytest.approx(8.0)
    assert summary.per_seat[3].avg_fan_when_won == pytest.approx(0.0)


def test_aggregate_per_bot_win_rate(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    # b_rule_v1 occupies 2 seats × 2 hands = 4 appearances, 2 wins
    assert summary.per_bot["b_rule_v1"].win_rate == pytest.approx(2 / 4)
    assert summary.per_bot["b_random"].win_rate == pytest.approx(0.0)


def test_aggregate_per_bot_avg_score(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.per_bot["b_rule_v1"].avg_score == pytest.approx(42 / 4)
    assert summary.per_bot["b_random"].avg_score == pytest.approx(-42 / 4)


def test_aggregate_per_bot_deal_in_rate(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.per_bot["b_rule_v1"].deal_in_rate == pytest.approx(0.0)
    assert summary.per_bot["b_random"].deal_in_rate == pytest.approx(2 / 4)


def test_aggregate_per_bot_avg_fan_when_won(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.per_bot["b_rule_v1"].avg_fan_when_won == pytest.approx(22 / 2)
    assert summary.per_bot["b_random"].avg_fan_when_won == pytest.approx(0.0)


def test_aggregate_wall_exhausted_hand(tmp_path: Path) -> None:
    """DRAW hands contribute to score/deal-in counts but not win or fan."""
    _write_record(
        tmp_path / "draw.jsonl",
        bot_ids=["b_x", "b_x", "b_x", "b_x"],
        kind="DRAW",
        winner=[],
        deal_in_seat=None,
        fan_total=0,
        score_delta=[0, 0, 0, 0],
    )
    summary = aggregate([tmp_path / "draw.jsonl"])
    assert summary.total_hands == 1
    for seat in range(4):
        assert summary.per_seat[seat].win_rate == pytest.approx(0.0)
        assert summary.per_seat[seat].deal_in_rate == pytest.approx(0.0)
        assert summary.per_seat[seat].avg_score == pytest.approx(0.0)
    assert summary.per_bot["b_x"].wins == 0
    assert summary.per_bot["b_x"].deal_ins == 0


def test_aggregate_empty_paths() -> None:
    """No records → zero totals, empty per_bot."""
    summary = aggregate([])
    assert summary.total_hands == 0
    for seat in range(4):
        assert summary.per_seat[seat].hands == 0
    assert summary.per_bot == {}


def test_aggregate_skips_malformed_record(tmp_path: Path) -> None:
    """A record with no HAND_END is silently skipped."""
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"event":"HEADER","seq":0,"seats":[]}\n{"event":"FOOTER"}\n')
    good = tmp_path / "good.jsonl"
    _write_record(
        good,
        bot_ids=["b_a", "b_b", "b_a", "b_b"],
        kind="HU",
        winner=[0],
        deal_in_seat=1,
        fan_total=10,
        score_delta=[20, -10, -5, -5],
    )
    summary = aggregate([bad, good])
    assert summary.total_hands == 1


def test_aggregate_extracts_meta_from_first_record(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    assert summary.master_seed == "0xdeadbeef"
    assert summary.ruleset == "mcr-2006"
    assert summary.bot_ids_config == ["b_rule_v1", "b_random", "b_rule_v1", "b_random"]


# ---------------------------------------------------------------------------
# format_summary — smoke test: expected substrings present
# ---------------------------------------------------------------------------


def test_format_summary_contains_key_fields(two_hand_dir: Path) -> None:
    summary = aggregate(sorted(two_hand_dir.glob("*.jsonl")))
    text = format_summary(summary)
    assert "2 hands" in text
    assert "b_rule_v1" in text
    assert "b_random" in text
    # Per-seat column headers
    assert "seat 0" in text
    assert "seat 3" in text
    # Metric rows
    assert "Win rate" in text
    assert "Avg score" in text
    assert "Deal-in rate" in text
    assert "Avg fan" in text


def test_format_summary_empty_summary() -> None:
    summary = aggregate([])
    text = format_summary(summary)
    assert "0 hands" in text
