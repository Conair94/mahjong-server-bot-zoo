"""Record writer tests.

Spec: docs/specs/record-format.md § File layout, § FOOTER, § Verification fixtures.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mahjong.records.writer import RecordWriter, canonical_jsonl_line

TS = "2026-05-20T22:00:00.000Z"


def _header_payload() -> dict[str, object]:
    return {
        "event": "HEADER",
        "turn_index": 0,
        "phase": "DEAL",
        "ts": TS,
        "format_version": 1,
        "hand_id": "01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        "match_id": None,
        "hand_index_in_match": 0,
        "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "sha256:abc"},
        "seed": "12345",
        "seats": [
            {"seat": s, "wind": f"F{s + 1}", "identity": {"kind": "canned", "script": "pass"}}
            for s in range(4)
        ],
        "server": {"version": "0.0.1", "git_sha": "dev", "host": "test"},
    }


def test_canonical_line_sorts_keys_and_appends_lf() -> None:
    line = canonical_jsonl_line({"b": 1, "a": 2})
    assert line == b'{"a":2,"b":1}\n'


def test_canonical_line_is_byte_identical_across_dict_order() -> None:
    a = canonical_jsonl_line({"x": 1, "y": [3, 2, 1]})
    b = canonical_jsonl_line({"y": [3, 2, 1], "x": 1})
    assert a == b


def test_writer_assigns_monotonic_seq_starting_at_zero(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    w = RecordWriter(path)
    w.write_event(_header_payload())
    w.write_event(
        {
            "event": "DEAL",
            "turn_index": 0,
            "phase": "DISCARD",
            "ts": TS,
            "concealed": [[], [], [], []],
            "flowers_drawn": [],
            "wall_remaining_after_deal": 84,
            "state_hash": "sha256:111",
        }
    )
    w.close_with_footer(
        turn_index=0,
        phase="TERMINAL",
        ts=TS,
        rng_cursor_final=56,
        state_hash_final="sha256:222",
        corrects=None,
    )

    lines = path.read_bytes().splitlines()
    objs = [json.loads(line) for line in lines]
    assert [o["seq"] for o in objs] == [0, 1, 2]
    assert objs[-1]["event"] == "FOOTER"
    assert objs[-1]["event_count"] == 3


def test_writer_footer_checksum_matches_recompute(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    w = RecordWriter(path)
    w.write_event(_header_payload())
    w.write_event(
        {
            "event": "HAND_END",
            "turn_index": 0,
            "phase": "TERMINAL",
            "ts": TS,
            "kind": "DRAW",
            "winner": None,
            "win_tile": None,
            "win_type": None,
            "deal_in_seat": None,
            "fan": [],
            "fan_total": 0,
            "score_delta": [0, 0, 0, 0],
            "final_hands": [],
            "state_hash": "sha256:222",
        }
    )
    w.close_with_footer(
        turn_index=0,
        phase="TERMINAL",
        ts=TS,
        rng_cursor_final=1284,
        state_hash_final="sha256:222",
        corrects=None,
    )

    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    # Recompute checksum over everything except the footer line.
    h = hashlib.sha256()
    for line in lines[:-1]:
        h.update(line)
    expected = "sha256:" + h.hexdigest()

    footer = json.loads(lines[-1])
    assert footer["checksum"] == expected
    assert footer["event_count"] == len(lines)


def test_writer_byte_identical_to_fixture(tmp_path: Path) -> None:
    """Fixture 1 write half. The same writer inputs must produce the same bytes
    on every platform (sorted keys, compact separators, LF newlines)."""
    path = tmp_path / "rec.jsonl"
    w = RecordWriter(path)
    w.write_event(_header_payload())
    w.write_event(
        {
            "event": "HAND_END",
            "turn_index": 0,
            "phase": "TERMINAL",
            "ts": TS,
            "kind": "DRAW",
            "winner": None,
            "win_tile": None,
            "win_type": None,
            "deal_in_seat": None,
            "fan": [],
            "fan_total": 0,
            "score_delta": [0, 0, 0, 0],
            "final_hands": [],
            "state_hash": "sha256:fixed_state",
        }
    )
    w.close_with_footer(
        turn_index=0,
        phase="TERMINAL",
        ts=TS,
        rng_cursor_final=42,
        state_hash_final="sha256:fixed_state",
        corrects=None,
    )

    fixture = Path("tests/_fixtures/record_minimal.jsonl").read_bytes()
    assert path.read_bytes() == fixture


def test_writer_rejects_double_close(tmp_path: Path) -> None:
    w = RecordWriter(tmp_path / "rec.jsonl")
    w.write_event(_header_payload())
    w.close_with_footer(
        turn_index=0,
        phase="TERMINAL",
        ts=TS,
        rng_cursor_final=0,
        state_hash_final="sha256:x",
        corrects=None,
    )
    with pytest.raises(RuntimeError):
        w.write_event(_header_payload())


def test_writer_rejects_missing_required_event_fields(tmp_path: Path) -> None:
    w = RecordWriter(tmp_path / "rec.jsonl")
    with pytest.raises(ValueError, match="event"):
        w.write_event({"turn_index": 0, "phase": "DEAL", "ts": TS})
