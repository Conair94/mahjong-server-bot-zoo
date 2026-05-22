"""`python -m mahjong selfplay` — self-play CLI.

Spec: docs/specs/selfplay-harness.md § Entry point, § Concurrency.

Step 6.1a wired the serial runner; 6.1b adds `--parallel-hands N`, which
spawns N worker subprocesses that share an output directory and partition
the `hand_index` space by parity (worker `k` handles indices where
`hand_index % N == k`). 6.1c added `--eval-summary` aggregation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.bot_runner import BotRunnerAdapter
from mahjong.bots.botzone_serializer import BotzoneCsmSerializer
from mahjong.bots.manifest import BotManifest, parse_manifest
from mahjong.bots.registry import BotRegistry
from mahjong.selfplay.eval import aggregate, format_summary
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
    parser.add_argument(
        "--eval-summary",
        action="store_true",
        help="Print per-seat and per-bot stats after the run.",
    )
    parser.add_argument(
        "--parallel-hands",
        type=int,
        default=1,
        help=(
            "Spawn N worker subprocesses; each plays the slice of hands where "
            "hand_index %% N == worker_id. Default: 1 (serial in-process)."
        ),
    )
    # Hidden flags used by the parent to invoke its own workers.
    parser.add_argument(
        "--worker-id", dest="worker_id", type=int, default=0, help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--worker-count",
        dest="worker_count",
        type=int,
        default=1,
        help=argparse.SUPPRESS,
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
        worker_id=args.worker_id,
        worker_count=args.worker_count,
        run_hand_kwargs={
            "decide_timeout_seconds": 30.0,
            "observe_timeout_seconds": 5.0,
            "seated_timeout_seconds": 10.0,
        },
    )
    written = await runner.run()
    print(f"selfplay: wrote {len(written)} record(s) to {args.output_dir}")
    # Eval-summary is the parent's job in parallel mode (workers shouldn't
    # print partial summaries). worker_count==1 here means either serial or
    # a single spawned worker; only the latter sets it via _run_parent.
    if args.eval_summary and args.worker_count == 1 and written:
        summary = aggregate(iter(written))
        print()
        print(format_summary(summary))
    return 0


def _spawn_workers(args: argparse.Namespace) -> int:
    """Parent path for `--parallel-hands N>1`: pre-flight the output dir,
    spawn N worker subprocesses, wait for all to finish, aggregate the
    eval-summary across the shared dir.
    """
    n = args.parallel_hands
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.resume:
        print(
            f"output dir {args.output_dir} is non-empty; pass --resume to continue",
            file=sys.stderr,
        )
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)

    base_argv = [
        sys.executable,
        "-m",
        "mahjong",
        "selfplay",
        "--master-seed",
        hex(args.master_seed),
        "--hands",
        str(args.hands),
        "--bots",
        args.bots,
        "--ruleset",
        args.ruleset,
        "--output-dir",
        str(args.output_dir),
        "--bot-rotation",
        args.bot_rotation,
        # Workers always resume — they may see siblings' records mid-run.
        "--resume",
        "--worker-count",
        str(n),
    ]
    procs: list[subprocess.Popen[bytes]] = []
    for worker_id in range(n):
        cmd = [*base_argv, "--worker-id", str(worker_id)]
        procs.append(subprocess.Popen(cmd, env=os.environ.copy()))

    rc = 0
    for proc in procs:
        if proc.wait() != 0:
            rc = proc.returncode or 1
    if rc != 0:
        return rc

    if args.eval_summary:
        records = sorted(args.output_dir.glob("*.jsonl"))
        if records:
            summary = aggregate(iter(records))
            print()
            print(format_summary(summary))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    if args.parallel_hands < 1:
        print(f"--parallel-hands must be >= 1, got {args.parallel_hands}", file=sys.stderr)
        return 2
    if args.parallel_hands > 1 and args.worker_count == 1:
        # Top-level parent invocation in parallel mode.
        return _spawn_workers(args)
    return asyncio.run(_arun(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
