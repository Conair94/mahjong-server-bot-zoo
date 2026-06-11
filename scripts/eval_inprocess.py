#!/usr/bin/env python3
"""Head-to-head eval of in-process seat bots via the self-play harness.

Spec: docs/specs/v1-rule-bot.md § Eval protocol.

Runs N hands with the four named bots seated via the same
`seat_bots.build_bot_adapter` factory the live server uses (so the evaluated
bot is byte-identical to the website one), then prints the eval-summary table.
Round-robin rotation removes seat/dealer bias; a fixed master seed means both
bots see the same walls — common random numbers, the paired-eval design the
AI plan requires before any "X is better than Y" claim.

Example:
    python scripts/eval_inprocess.py --bots v1,v0,v0,v0 --hands 400 \
        --seed 20260611 --out var/eval/v1-vs-v0
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mahjong.selfplay.eval import aggregate, format_summary
from mahjong.selfplay.runner import SelfPlayRunner
from mahjong.server.seat_bots import SEAT_BOTS, build_bot_adapter


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bots",
        required=True,
        help=f"Comma-separated list of four seat-bot ids (known: {sorted(SEAT_BOTS)}).",
    )
    parser.add_argument("--hands", type=int, default=400)
    parser.add_argument("--seed", type=lambda s: int(s, 0), default=20260611)
    parser.add_argument("--ruleset", default="mcr-house-3fan")
    parser.add_argument("--out", type=Path, required=True, help="Record output directory.")
    parser.add_argument("--rotation", choices=["none", "round-robin"], default="round-robin")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    bots = [b.strip() for b in args.bots.split(",")]
    if len(bots) != 4:
        print(f"--bots needs exactly 4 entries, got {len(bots)}", file=sys.stderr)
        return 2
    unknown = [b for b in bots if b not in SEAT_BOTS]
    if unknown:
        print(f"unknown bot ids {unknown}; known: {sorted(SEAT_BOTS)}", file=sys.stderr)
        return 2

    runner = SelfPlayRunner(
        master_seed=args.seed,
        bots=bots,
        hands=args.hands,
        output_dir=args.out,
        adapter_factory=lambda bot_id, seat: build_bot_adapter(bot_id),
        ruleset_id=args.ruleset,
        rotation=args.rotation,
        resume=args.resume,
        server_info={"version": "eval-inprocess", "git_sha": _git_sha(), "host": "local"},
    )
    written = asyncio.run(runner.run())
    print(f"wrote {len(written)} records to {args.out} (git_sha={_git_sha()})")
    print()
    print(format_summary(aggregate(sorted(args.out.glob("*.jsonl")))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
