"""Layer 5 end-to-end: four BotRunnerAdapters playing a hand.

Spec: docs/specs/bot-runner-protocol.md fixture 1 (reference-bot round-trip),
      docs/specs/implementation-order.md Step 5.3.

This is the Step 5.3a integration: four in-tree Python reference bots
(`bots/python-reference/bot.py`) speak the Botzone CSM JSON envelope via
`BotzoneCsmSerializer`, the table manager drives a hand, and the resulting
record exports cleanly to Botzone log shape.

Step 5.3b — running four C++ sample bots and feeding the export to the
official judge — is deferred (see [bots/README.md](../../bots/README.md)).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mahjong.adapters.bot_runner import BotRunnerAdapter
from mahjong.adapters.canned import CannedAdapter
from mahjong.bots.botzone_serializer import BotzoneCsmSerializer
from mahjong.bots.manifest import BotManifest, parse_manifest
from mahjong.engine.rulesets import MANIFEST
from mahjong.records.botzone_export import export_to_botzone
from mahjong.records.reader import read_record
from mahjong.table import manager as mgr

pytestmark = pytest.mark.asyncio

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_BOT = PROJECT_ROOT / "bots" / "python-reference" / "bot.py"


def _reference_bot_manifest(seat: int) -> BotManifest:
    return parse_manifest(
        {
            "bot_id": f"py_ref_seat{seat}",
            "version": "0.1.0",
            "display_name": f"Python reference (seat {seat})",
            "directory": str(REFERENCE_BOT.parent),
            "command": [sys.executable, "-u", str(REFERENCE_BOT)],
            "env": {"PYTHONUNBUFFERED": "1", "PYTHONPATH": str(PROJECT_ROOT)},
            "budget_ms_per_turn": 3000,
            "handshake_deadline_ms": 2000,
            "teardown_grace_ms": 1000,
            "limits": {
                "memory_mb": 256,
                "cpu_seconds": 60,
                "max_fds": 64,
                "max_processes": 4,
                "network": "deny",
            },
            "ruleset_supported": ["mcr-2006"],
            "format_supported": ["botzone-csm"],
        }
    )


def _make_bot(seat: int) -> BotRunnerAdapter:
    return BotRunnerAdapter(
        _reference_bot_manifest(seat),
        history_serializer=BotzoneCsmSerializer(seat=seat),
    )


# Serializer unit tests live in tests/bots/test_botzone_serializer.py.

# --- Gate: four-bot hand via the table manager ---------------------------


async def test_four_python_reference_bots_play_a_hand(tmp_path: Path) -> None:
    """S1 in-tree gate: four BotRunnerAdapter + BotzoneCsmSerializer pairs
    play a hand. Hand reaches TERMINAL; record export to Botzone log shape
    succeeds; every exported message has a non-empty token list."""
    assert REFERENCE_BOT.exists(), f"reference bot missing at {REFERENCE_BOT}"

    bots = [_make_bot(seat) for seat in range(4)]
    record_path = tmp_path / "hand.jsonl"
    final = await mgr.run_hand(
        adapters=list(bots),
        ruleset={"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]},
        seed=12345,
        hand_id="layer5-e2e",
        record_path=record_path,
        server_info={"name": "test", "version": "0.0.0"},
        decide_timeout_seconds=30.0,
        observe_timeout_seconds=5.0,
        seated_timeout_seconds=10.0,
    )

    assert final["phase"] == "TERMINAL"

    events = read_record(record_path)
    assert events[0]["event"] == "HEADER"
    assert events[-1]["event"] == "FOOTER"

    # Structural Botzone-log check: export succeeds, every message carries
    # a non-empty `tokens` list (sample.cpp's first-token parse would not be
    # fooled by an empty line).
    log = export_to_botzone(events)
    assert log, "Botzone log should not be empty"
    for msg in log:
        assert isinstance(msg["tokens"], list)
        assert msg["tokens"], f"empty tokens in {msg!r}"


async def test_botrunner_works_with_canned_seats(tmp_path: Path) -> None:
    """Sanity: 1 BotRunnerAdapter + 3 CannedAdapters with the Botzone
    serializer also completes a hand. Confirms the serializer doesn't break
    the simpler mixed-adapter case from Step 5.2."""
    bot = _make_bot(0)
    cans = [
        CannedAdapter(identity={"kind": "canned", "script": f"pass_{i}"}, actions=[])
        for i in range(3)
    ]
    record_path = tmp_path / "mixed.jsonl"
    final = await mgr.run_hand(
        adapters=[bot, *cans],
        ruleset={"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]},
        seed=54321,
        hand_id="layer5-mixed",
        record_path=record_path,
        server_info={"name": "test", "version": "0.0.0"},
        decide_timeout_seconds=30.0,
        observe_timeout_seconds=5.0,
        seated_timeout_seconds=10.0,
    )
    assert final["phase"] == "TERMINAL"
