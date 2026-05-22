"""CLI assembly smoke for `python -m mahjong selfplay`.

Spec: docs/specs/selfplay-harness.md § Entry point.

We call `mahjong.cli.selfplay.main` directly (in-process) rather than
spawning a fresh `python -m mahjong` subprocess; the latter, combined
with each hand spawning four bot subprocesses, hits macOS process-table
limits when the full test suite runs in one session. The Layer 5 e2e
test already covers the four-subprocess path with real bots, so this
smoke focuses on argument parsing + registry wiring + one-hand
end-to-end via the real BotRunnerAdapter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mahjong.cli.selfplay import main as selfplay_main


@pytest.mark.integration
def test_selfplay_cli_runs_one_hand(tmp_path: Path) -> None:
    out = tmp_path / "run"
    rc = selfplay_main(
        [
            "--master-seed",
            "0x1",
            "--hands",
            "1",
            "--bots",
            "b_random,b_random,b_random,b_random",
            "--output-dir",
            str(out),
        ]
    )
    assert rc == 0
    records = list(out.glob("*.jsonl"))
    assert len(records) == 1
    with records[0].open() as fh:
        lines = fh.readlines()
    header = json.loads(lines[0])
    footer = json.loads(lines[-1])
    assert header["event"] == "HEADER"
    assert footer["event"] == "FOOTER"
    assert header["meta"]["hand_index"] == 0
    assert header["meta"]["master_seed"] == "0x1"


def test_selfplay_cli_rejects_unknown_bot(tmp_path: Path) -> None:
    out = tmp_path / "run"
    rc = selfplay_main(
        [
            "--master-seed",
            "0x1",
            "--hands",
            "1",
            "--bots",
            "b_random,b_random,b_random,not_a_real_bot",
            "--output-dir",
            str(out),
        ]
    )
    assert rc == 2
