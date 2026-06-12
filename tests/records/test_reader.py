"""Record reader + replay tests.

Spec: docs/specs/record-format.md § Verification fixtures (1-5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mahjong.engine import apply_action, initial_state, is_terminal, legal_actions, state_hash
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import tile_sort_key
from mahjong.records.reader import RecordCorruptError, read_record
from mahjong.records.replay import replay
from mahjong.records.writer import RecordWriter, canonical_jsonl_line

TS = "2026-05-20T22:00:00.000Z"
MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _write_smoke_record(path: Path) -> dict[str, Any]:
    """Drive the engine through a four-PASS exhaustive draw, recording every
    player action. Returns the final GameState (for cross-checking)."""
    from mahjong.records.diff import diff_to_events

    s = initial_state(MCR_REF, seed=12345)
    w = RecordWriter(path)
    w.write_event(
        {
            "event": "HEADER",
            "turn_index": 0,
            "phase": "DEAL",
            "ts": TS,
            "format_version": 1,
            "hand_id": "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
            "match_id": None,
            "hand_index_in_match": 0,
            "ruleset": MCR_REF,
            "seed": "12345",
            "seats": [
                {"seat": i, "wind": f"F{i + 1}", "identity": {"kind": "canned", "script": "pass"}}
                for i in range(4)
            ],
            "server": {"version": "test", "git_sha": "test", "host": "test"},
        }
    )
    # Skip a DEAL event for now — the spec allows it, but replay reconstructs
    # the deal from HEADER.seed, which is sufficient for the verification
    # fixtures in scope (reader can still skip past unknown events).

    while not is_terminal(s):  # type: ignore[arg-type]
        phase = s["phase"]
        if phase == "DISCARD":
            actor = s["current_actor"]
            plays = [a for a in legal_actions(s, actor) if a["type"] == "PLAY"]
            chosen = min(plays, key=lambda a: tile_sort_key(a["tile"]))
            s_after = apply_action(s, actor, chosen)  # type: ignore[arg-type]
            for event in diff_to_events(s, actor, chosen, s_after, ts=TS):  # type: ignore[arg-type]
                w.write_event(event)
            s = s_after  # type: ignore[assignment]
        elif phase == "CLAIM_WINDOW":
            seats_with_claims = sorted({c["seat"] for c in s["pending_claims"]})
            for seat in seats_with_claims:
                s_after = apply_action(s, seat, {"type": "PASS"})  # type: ignore[arg-type]
                for event in diff_to_events(s, seat, {"type": "PASS"}, s_after, ts=TS):  # type: ignore[arg-type]
                    w.write_event(event)
                s = s_after  # type: ignore[assignment]
                if s["phase"] != "CLAIM_WINDOW":
                    break
        else:  # pragma: no cover
            raise AssertionError(f"unexpected phase: {phase!r}")

    final_hash = state_hash(s)  # type: ignore[arg-type]
    w.close_with_footer(
        turn_index=s["turn_index"],
        phase=s["phase"],
        ts=TS,
        rng_cursor_final=s["rng"]["cursor"],
        state_hash_final=final_hash,
        corrects=None,
    )
    return s


def test_reader_parses_valid_record(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _write_smoke_record(path)
    events = read_record(path)
    assert events[0]["event"] == "HEADER"
    assert events[-1]["event"] == "FOOTER"
    assert [e["seq"] for e in events] == list(range(len(events)))


def test_reader_validates_checksum(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _write_smoke_record(path)
    # Corrupt one byte in the middle of the file.
    raw = bytearray(path.read_bytes())
    mid = len(raw) // 2
    raw[mid] = (raw[mid] + 1) % 256
    path.write_bytes(bytes(raw))
    with pytest.raises(RecordCorruptError):
        read_record(path)


def test_reader_validates_event_count(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _write_smoke_record(path)
    # Drop the second-to-last line (one non-footer event).
    lines = path.read_bytes().splitlines(keepends=True)
    truncated = b"".join(lines[:-2]) + lines[-1]
    path.write_bytes(truncated)
    with pytest.raises(RecordCorruptError):
        read_record(path)


def test_reader_rejects_seq_gap(tmp_path: Path) -> None:
    """A non-monotonic seq is a corrupted record."""
    path = tmp_path / "bad.jsonl"
    # Hand-craft a record with seq 0, 2 (gap).
    parts: list[bytes] = []
    parts.append(
        canonical_jsonl_line(
            {
                "event": "HEADER",
                "seq": 0,
                "turn_index": 0,
                "phase": "DEAL",
                "ts": TS,
                "format_version": 1,
            }
        )
    )
    parts.append(
        canonical_jsonl_line(
            {"event": "HAND_END", "seq": 2, "turn_index": 0, "phase": "TERMINAL", "ts": TS}
        )
    )
    # Footer with bogus checksum (the seq check should fire first).
    parts.append(
        canonical_jsonl_line(
            {
                "event": "FOOTER",
                "seq": 3,
                "turn_index": 0,
                "phase": "TERMINAL",
                "ts": TS,
                "event_count": 3,
                "rng_cursor_final": 0,
                "state_hash_final": "sha256:x",
                "checksum": "sha256:bad",
                "corrects": None,
            }
        )
    )
    path.write_bytes(b"".join(parts))
    with pytest.raises(RecordCorruptError, match="seq"):
        read_record(path)


def test_round_trip_identity(tmp_path: Path) -> None:
    """read(write(events)) -> events; re-writing yields the same bytes."""
    path = tmp_path / "rec.jsonl"
    _write_smoke_record(path)
    original_bytes = path.read_bytes()

    events = read_record(path)
    # Re-serialize each event back to canonical lines.
    rewritten = b"".join(canonical_jsonl_line(e) for e in events)
    assert rewritten == original_bytes


def test_replay_final_state_hash_matches_footer(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    final_state = _write_smoke_record(path)
    events = read_record(path)
    footer = events[-1]

    states = list(replay(events))
    assert states, "replay must yield at least the initial state"
    assert state_hash(states[-1]) == footer["state_hash_final"]  # type: ignore[arg-type]
    assert state_hash(states[-1]) == state_hash(final_state)  # type: ignore[arg-type]


def test_replay_per_seat_projection_has_no_foreign_concealed(tmp_path: Path) -> None:
    """Privacy contract: replay(record, seat=S) shows no foreign concealed tokens."""
    from mahjong.engine import project

    path = tmp_path / "rec.jsonl"
    _write_smoke_record(path)
    events = read_record(path)

    for s in replay(events):
        for viewer in range(4):
            view = project(s, viewer)  # type: ignore[arg-type]
            for opp_seat in view["seats"]:
                if opp_seat["seat"] == viewer:
                    continue
                # SeatViewOpponent.concealed is a count dict, never a list.
                assert isinstance(
                    opp_seat["concealed"], dict
                ), f"replay leaked seat {opp_seat['seat']}'s concealed to viewer {viewer}"


def test_reader_rejects_unsupported_format_version(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    parts = [
        canonical_jsonl_line(
            {
                "event": "HEADER",
                "seq": 0,
                "turn_index": 0,
                "phase": "DEAL",
                "ts": TS,
                "format_version": 99,
            }
        ),
        canonical_jsonl_line(
            {
                "event": "FOOTER",
                "seq": 1,
                "turn_index": 0,
                "phase": "TERMINAL",
                "ts": TS,
                "event_count": 2,
                "rng_cursor_final": 0,
                "state_hash_final": "sha256:x",
                "checksum": "sha256:bad",
                "corrects": None,
            }
        ),
    ]
    path.write_bytes(b"".join(parts))
    with pytest.raises(RecordCorruptError, match="format_version"):
        read_record(path)


def test_reader_emits_dicts_with_seq_preserved(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _write_smoke_record(path)
    events = read_record(path)
    # Spot-check: at least one DISCARD event present, seq is int, fields preserved.
    discards = [e for e in events if e["event"] == "DISCARD"]
    assert discards
    assert all(isinstance(e["seq"], int) for e in events)
    # event_count consistency
    footer = events[-1]
    assert footer["event_count"] == len(events)
    # Try loading raw too
    raw = path.read_text().splitlines()
    assert json.loads(raw[0])["event"] == "HEADER"
