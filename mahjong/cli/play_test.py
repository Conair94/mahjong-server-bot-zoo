"""`python -m mahjong play-test` — drive one hand with four CannedAdapters.

S0 walking-skeleton exit artifact (CHECKLIST Step 4.2): four canned seats
play a complete hand from a seed, the record is written to disk, and the
record can be replayed to a matching final state.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any, cast

from mahjong.adapters.base import SeatAdapter
from mahjong.adapters.canned import CannedAdapter
from mahjong.engine.rulesets import MANIFEST
from mahjong.table.manager import run_hand


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mahjong play-test",
        description="Play one hand with four canned seats; write the record.",
    )
    parser.add_argument("--seed", type=int, default=12345, help="RNG seed for the deal")
    parser.add_argument(
        "--ruleset",
        default="mcr-2006",
        help="Ruleset id (must appear in MANIFEST.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("hand.jsonl"),
        help="Where to write the JSONL record",
    )
    parser.add_argument(
        "--hand-id",
        default="01970e8a-9d3e-7c4a-9b1f-0a1b2c3d4e5f",
        help="UUIDv7 stamped onto the record HEADER",
    )
    return parser


async def _arun(args: argparse.Namespace) -> Path:
    ruleset = {
        "id": args.ruleset,
        "version": 1,
        "config_hash": MANIFEST[args.ruleset],
    }
    adapters: list[SeatAdapter] = [
        cast(
            SeatAdapter,
            CannedAdapter(identity={"kind": "canned", "script": "pass"}, actions=[]),
        )
        for _ in range(4)
    ]
    final = await run_hand(
        adapters=adapters,
        ruleset=cast(Any, ruleset),
        seed=args.seed,
        hand_id=args.hand_id,
        record_path=args.output,
        server_info={"version": "play-test", "git_sha": "dev", "host": "local"},
    )
    print(f"hand complete: phase={final['phase']} → {args.output}")
    return Path(args.output)


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    asyncio.run(_arun(args))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
