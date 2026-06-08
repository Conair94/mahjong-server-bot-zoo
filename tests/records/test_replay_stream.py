"""FB-04 replay stream projection (account-records-replay.md, Spec 32).

Pins ``records/replay_stream.py``: a recorded hand becomes the per-seat wire
EVENT stream a replay viewer folds through the live reducer. Two contracts:
HEADER/FOOTER are dropped, and the per-seat privacy projection matches the live
session-mux (own draws revealed, other seats' draws hidden; public view hides
all draws).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mahjong.records.reader import read_record
from mahjong.records.replay_stream import (
    initial_snapshot_for_seat,
    projected_events_for_seat,
)

_FIXTURE = Path("tests/_fixtures/s2_e2e_record.jsonl")


@pytest.fixture(scope="module")
def record_events() -> list[dict[str, Any]]:
    return read_record(_FIXTURE)


def test_drops_header_and_footer(record_events: list[dict[str, Any]]) -> None:
    proj = projected_events_for_seat(record_events, seat=0)
    assert all(e.get("event") not in ("HEADER", "FOOTER") for e in proj)
    # Exactly the two wrappers are removed.
    assert len(proj) == len(record_events) - 2


def test_own_draws_revealed_other_draws_hidden(record_events: list[dict[str, Any]]) -> None:
    # In the fixture seat 0 is the only human; every seat draws. Project for
    # seat 1: seat 1's own DRAWs keep their tile, other seats' DRAWs don't.
    proj = projected_events_for_seat(record_events, seat=1)
    draws = [e for e in proj if e.get("event") == "DRAW"]
    assert draws, "fixture should contain DRAW events"
    own = [d for d in draws if d.get("seat") == 1]
    others = [d for d in draws if d.get("seat") != 1]
    assert own and all("tile" in d for d in own)
    assert others and all("tile" not in d for d in others)


def test_public_view_hides_all_draws(record_events: list[dict[str, Any]]) -> None:
    proj = projected_events_for_seat(record_events, seat=None)
    draws = [e for e in proj if e.get("event") == "DRAW"]
    assert draws
    assert all("tile" not in d for d in draws)


def test_seat_projection_matches_live_mux(record_events: list[dict[str, Any]]) -> None:
    # The replay stream must be byte-identical to what session-mux would have
    # projected for the seat live — same project_event path. Compare directly.
    from mahjong.engine.state import project_event

    expected = [
        project_event(e, seat=2)
        for e in record_events
        if e.get("event") not in ("HEADER", "FOOTER")
    ]
    assert projected_events_for_seat(record_events, seat=2) == expected


def test_initial_snapshot_is_a_board(record_events: list[dict[str, Any]]) -> None:
    snap = initial_snapshot_for_seat(record_events, seat=0)
    assert isinstance(snap, dict)
    assert snap.get("phase")  # a real projected board, not empty
    # Public view is still a board (admin replay).
    pub = initial_snapshot_for_seat(record_events, seat=None)
    assert isinstance(pub, dict)
