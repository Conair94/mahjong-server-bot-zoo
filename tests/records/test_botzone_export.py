"""Botzone export tests.

Spec: docs/specs/record-format.md § Botzone export.

Gate per CHECKLIST Step 3.3 is "well-formed Botzone logs"; judge acceptance is
deferred to S1 (bot-runner). These tests pin the mapping rules from the spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mahjong.engine import apply_action, initial_state, is_terminal, legal_actions
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.tiles import tile_sort_key
from mahjong.records.botzone_export import export_to_botzone
from mahjong.records.reader import read_record
from mahjong.records.writer import RecordWriter

TS = "2026-05-20T22:00:00.000Z"
MCR_REF: dict[str, Any] = {
    "id": "mcr-2006",
    "version": 1,
    "config_hash": MANIFEST["mcr-2006"],
}


def _build_smoke_record(path: Path) -> None:
    """Drive a 4-PASS exhaustive draw from seed 12345 and record it."""
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
                {
                    "seat": i,
                    "wind": f"F{i + 1}",
                    "identity": {"kind": "bot", "bot_id": f"b{i}", "version": "0.0.0"},
                }
                for i in range(4)
            ],
            "server": {"version": "test", "git_sha": "test", "host": "test"},
        }
    )
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
        else:
            assert phase == "CLAIM_WINDOW"
            for seat in sorted({c["seat"] for c in s["pending_claims"]}):
                s_after = apply_action(s, seat, {"type": "PASS"})  # type: ignore[arg-type]
                for event in diff_to_events(s, seat, {"type": "PASS"}, s_after, ts=TS):  # type: ignore[arg-type]
                    w.write_event(event)
                s = s_after  # type: ignore[assignment]
                if s["phase"] != "CLAIM_WINDOW":
                    break

    w.close_with_footer(
        turn_index=s["turn_index"],
        phase=s["phase"],
        ts=TS,
        rng_cursor_final=s["rng"]["cursor"],
        state_hash_final="sha256:final",
        corrects=None,
    )


def test_export_header_emits_per_seat_init_messages(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _build_smoke_record(path)
    events = read_record(path)
    log = export_to_botzone(events)

    init = [m for m in log if m["kind"] == "init"]
    assert len(init) == 4
    for i, msg in enumerate(sorted(init, key=lambda m: m["seat"])):
        assert msg["seat"] == i
        # Botzone init request: "0 <round_wind_idx> <seat>"
        assert msg["tokens"][0] == "0"
        assert msg["tokens"][-1] == str(i)


def test_export_discard_emits_play_broadcast(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _build_smoke_record(path)
    events = read_record(path)
    log = export_to_botzone(events)

    discards = [m for m in log if m["kind"] == "discard"]
    first = discards[0]
    # Botzone "3 <seat> PLAY <tile>"
    assert first["tokens"][0] == "3"
    assert first["tokens"][2] == "PLAY"
    # Tile token survives our spec's encoding directly into Botzone CHINESEOFFICIAL.
    assert first["tokens"][3] in {f"W{i}" for i in range(1, 10)} | {
        f"B{i}" for i in range(1, 10)
    } | {f"T{i}" for i in range(1, 10)} | {f"F{i}" for i in range(1, 5)} | {
        f"J{i}" for i in range(1, 4)
    }


def test_export_claim_window_and_resolution_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _build_smoke_record(path)
    events = read_record(path)
    log = export_to_botzone(events)
    kinds = {m["kind"] for m in log}
    assert "claim_window" not in kinds
    assert "claim_resolution" not in kinds


def test_export_pass_decision_yields_pass_response(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _build_smoke_record(path)
    events = read_record(path)
    log = export_to_botzone(events)
    pass_responses = [m for m in log if m["kind"] == "claim_response"]
    assert pass_responses, "smoke record contains PASS claim decisions"
    assert any(m["tokens"] == ["PASS"] for m in pass_responses)


def test_export_hand_end_emits_terminal_message(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    _build_smoke_record(path)
    events = read_record(path)
    log = export_to_botzone(events)
    ends = [m for m in log if m["kind"] == "hand_end"]
    assert len(ends) == 1
    # Either HU <winner> <fan_total> or DRAW
    assert ends[0]["tokens"][0] in {"HU", "DRAW"}


def test_export_preserves_event_order(tmp_path: Path) -> None:
    """Botzone log is positional; messages must appear in the same chronological
    order as the source events (modulo dropped event types)."""
    path = tmp_path / "rec.jsonl"
    _build_smoke_record(path)
    events = read_record(path)
    log = export_to_botzone(events)
    # Each message carries a `source_seq` pointing back at the source event.
    seqs = [m["source_seq"] for m in log if "source_seq" in m]
    assert seqs == sorted(seqs)
