"""Cancellation cannot bypass the bot-runner SIGKILL/reap contract."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import pytest

from mahjong.adapters.base import SeatContext
from mahjong.adapters.bot_runner import BotRunnerAdapter
from mahjong.bots.manifest import parse_manifest
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.state import initial_state, project
from mahjong.engine.types import RuleSetRef


@pytest.mark.asyncio
async def test_cancelled_bot_teardown_still_kills_and_reaps_child(tmp_path: Path) -> None:
    """Cancel while a real child ignores SIGTERM, before its grace expires."""
    script = tmp_path / "stubborn_bot.py"
    script.write_text(
        "import os, signal\n"
        "from pathlib import Path\n"
        "from mahjong.bots.sdk import run_bot\n"
        "Path('pid').write_text(str(os.getpid()))\n"
        "signal.signal(signal.SIGTERM, lambda *_: Path('term').touch())\n"
        "run_bot(lambda _: 'PASS', bot_id='stubborn', version='1')\n"
    )
    adapter = BotRunnerAdapter(
        parse_manifest(
            {
                "bot_id": "stubborn",
                "version": "1",
                "display_name": "Stubborn teardown fixture",
                "directory": str(tmp_path),
                "command": [sys.executable, "-u", str(script)],
                "env": {"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
                "budget_ms_per_turn": 1000,
                "handshake_deadline_ms": 2000,
                "teardown_grace_ms": 2000,
                "limits": {
                    "memory_mb": 256,
                    "cpu_seconds": 30,
                    "max_fds": 64,
                    "max_processes": 4,
                    "network": "deny",
                },
                "ruleset_supported": ["mcr-2006"],
                "format_supported": ["botzone-csm"],
            }
        )
    )
    ruleset: RuleSetRef = {"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]}
    ctx: SeatContext = {
        "seat": 0,
        "hand_id": "teardown-cancellation",
        "ruleset": ruleset,
        "seat_deadline_ms": 2000,
        "initial_view": project(initial_state(ruleset, seed=1), 0),
    }
    pid: int | None = None
    try:
        await adapter.seated(ctx)
        pid = int((tmp_path / "pid").read_text())
        teardown = asyncio.create_task(adapter.left("REPLACED"))
        async with asyncio.timeout(1):
            while not (tmp_path / "term").exists():
                await asyncio.sleep(0.005)
        teardown.cancel()
        with pytest.raises(asyncio.CancelledError):
            await teardown
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        await adapter.left("TABLE_CLOSED")
