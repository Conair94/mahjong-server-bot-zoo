"""TableHandle splices the running match score onto every per-seat snapshot.

Spec 40: the cumulative standings ride the existing snapshot (no new wire
frame), so a late-joiner / reconnecting client / spectator gets authoritative
totals + the full series in its first frame, with no client-side accumulation.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import RuleSetRef
from mahjong.server.registry import TableHandle

_MCR: RuleSetRef = cast(
    RuleSetRef, {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
)


def _handle(tmp_path: Path) -> TableHandle:
    return TableHandle(
        table_id="40",
        ruleset=_MCR,
        seed=1,
        hand_id="t40-h0",
        record_path=tmp_path / "hand_0000.jsonl",
        server_info={"version": "test", "git_sha": "test", "host": "test"},
    )


def test_fresh_table_snapshot_has_zero_standings(tmp_path: Path) -> None:
    handle = _handle(tmp_path)
    snap = handle._snapshot_provider(0)
    assert snap["match_scores"] == {"cumulative": [0, 0, 0, 0], "series": [], "hands_complete": 0}
    for seat_view in snap["seats"]:
        assert seat_view["match_score"] == 0


def test_snapshot_reflects_completed_hands(tmp_path: Path) -> None:
    handle = _handle(tmp_path)
    # Simulate two finished hands folding into the running total (what the hand
    # loop does after each run_hand).
    handle._match_score.record_hand({"score_delta": [24, -8, -8, -8]})
    handle._match_score.record_hand({"score_delta": [-8, 24, -8, -8]})

    snap = handle._snapshot_provider(None)  # spectator view
    assert snap["match_scores"] == {
        "cumulative": [16, 16, -16, -16],
        "series": [[24, -8, -8, -8], [16, 16, -16, -16]],
        "hands_complete": 2,
    }
    # Per-seat inline total matches cumulative[seat].
    by_seat = {sv["seat"]: sv["match_score"] for sv in snap["seats"]}
    assert by_seat == {0: 16, 1: 16, 2: -16, 3: -16}


def test_snapshot_match_scores_is_a_copy(tmp_path: Path) -> None:
    # A consumer mutating the wire dict must not corrupt the handle's totals.
    handle = _handle(tmp_path)
    handle._match_score.record_hand({"score_delta": [8, -8, 0, 0]})
    snap = handle._snapshot_provider(0)
    snap["match_scores"]["cumulative"][0] = 999
    assert handle._match_score.cumulative[0] == 8
