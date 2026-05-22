"""`python -m mahjong selfplay` — serial self-play CLI.

Spec: docs/specs/selfplay-harness.md § Entry point.

Step 6.1a wires the serial runner (`mahjong.selfplay.runner.SelfPlayRunner`)
to a default in-tree bot registry containing `py_reference_v1` and
`b_random`. Parallel-hands (--parallel-hands) and --eval-summary belong to
6.1b/6.1c and are deliberately omitted here.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.bot_runner import BotRunnerAdapter
from mahjong.bots.botzone_serializer import BotzoneCsmSerializer
from mahjong.bots.manifest import BotManifest, parse_manifest
from mahjong.bots.registry import BotRegistry
from mahjong.selfplay.runner import SelfPlayRunner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = PROJECT_ROOT / "bots" / "python-reference"


def _python_bot_manifest(bot_id: str, script: str, version: str) -> BotManifest:
    return parse_manifest(
        {
            "bot_id": bot_id,
            "version": version,
            "display_name": bot_id,
            "directory": str(REFERENCE_DIR),
            "command": [sys.executable, "-u", str(REFERENCE_DIR / script)],
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


def default_registry() -> BotRegistry:
    """Registry pre-populated with the in-tree Python bots."""
    reg = BotRegistry()
    reg.register(_python_bot_manifest("py_reference_v1", "bot.py", "0.1.0"))
    reg.register(_python_bot_manifest("b_random", "random_bot.py", "0.1.0"))
    return reg


def _parse_master_seed(s: str) -> int:
    return int(s, 0)  # accepts decimal and 0x-prefixed hex


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mahjong selfplay",
        description="Headless self-play between bots; writes one JSONL record per hand.",
    )
    parser.add_argument("--master-seed", type=_parse_master_seed, required=True)
    parser.add_argument("--hands", type=int, required=True)
    parser.add_argument(
        "--bots",
        required=True,
        help="Comma-separated list of four bot_ids (seat 0..3).",
    )
    parser.add_argument("--ruleset", default="mcr-2006")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--bot-rotation",
        choices=["none", "round-robin"],
        default="none",
    )
    return parser


def _make_adapter_factory(registry: BotRegistry) -> Callable[[str, int], SeatAdapter]:
    def _factory(bot_id: str, seat: int) -> SeatAdapter:
        manifest = registry.lookup(bot_id)
        adapter = BotRunnerAdapter(
            manifest,
            history_serializer=BotzoneCsmSerializer(seat=seat),
        )
        return cast(SeatAdapter, adapter)

    return _factory


async def _arun(args: argparse.Namespace) -> int:
    bots = [b.strip() for b in args.bots.split(",")]
    if len(bots) != 4:
        print(f"--bots needs exactly 4 entries, got {len(bots)}", file=sys.stderr)
        return 2
    registry = default_registry()
    for bot_id in bots:
        if bot_id not in registry:
            print(
                f"unknown bot_id {bot_id!r}; registered: {sorted(registry.list_ids())}",
                file=sys.stderr,
            )
            return 2

    runner = SelfPlayRunner(
        master_seed=args.master_seed,
        bots=bots,
        hands=args.hands,
        output_dir=args.output_dir,
        adapter_factory=_make_adapter_factory(registry),
        ruleset_id=args.ruleset,
        rotation=args.bot_rotation,
        resume=args.resume,
        run_hand_kwargs={
            "decide_timeout_seconds": 30.0,
            "observe_timeout_seconds": 5.0,
            "seated_timeout_seconds": 10.0,
        },
    )
    written = await runner.run()
    print(f"selfplay: wrote {len(written)} record(s) to {args.output_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return asyncio.run(_arun(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
