#!/usr/bin/env python3
"""Paired (common-random-numbers) comparison of two self-play record sets.

Spec: docs/specs/v1-rule-bot.md § Eval protocol.

Both runs must use the same master seed(s) so hand i is dealt the same wall.
The "focal" seat of hand i is wherever `--focal-bot` sat in run A (read from
the record header); the same seat in run B is its counterpart. Per-hand paired
deltas cancel the shared-wall variance, which is the whole point: an unpaired
2000-hand win-rate comparison has a ~1pp standard error, while the paired
discordant-hands test resolves the same effect with a fraction of the noise
(this is *common random numbers*, the variance-reduction the AI plan requires
before any "X beats Y" claim).

Example:
    python scripts/paired_compare.py --focal-bot v1 \
        --a /tmp/eval-big-101 /tmp/eval-big-102 \
        --b /tmp/eval-base-101 /tmp/eval-base-102
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def _parse(path: Path) -> dict | None:
    header = hand_end = None
    try:
        with path.open() as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                obj = json.loads(line)
                evt = obj.get("event")
                if evt == "HEADER" and header is None:
                    header = obj
                elif evt == "HAND_END":
                    hand_end = obj
    except (OSError, json.JSONDecodeError):
        return None
    if header is None or hand_end is None:
        return None
    seats = sorted(header.get("seats") or [], key=lambda s: s.get("seat", 0))
    return {
        "hand_index": (header.get("meta") or {}).get("hand_index"),
        "master_seed": (header.get("meta") or {}).get("master_seed"),
        "bots": [(s.get("identity") or {}).get("bot_id", "?") for s in seats],
        "winners": hand_end.get("winner") or [],
        "score_delta": hand_end.get("score_delta") or [0, 0, 0, 0],
    }


def _load(dirs: list[Path]) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for d in dirs:
        for path in sorted(d.glob("*.jsonl")):
            rec = _parse(path)
            if rec is None or rec["hand_index"] is None:
                continue
            out[(rec["master_seed"], rec["hand_index"])] = rec
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--focal-bot", default="v1")
    ap.add_argument("--a", nargs="+", type=Path, required=True, help="Run A dirs (with focal bot).")
    ap.add_argument("--b", nargs="+", type=Path, required=True, help="Run B dirs (baseline).")
    args = ap.parse_args()

    a, b = _load(args.a), _load(args.b)
    common = sorted(set(a) & set(b))
    if not common:
        print("no common (master_seed, hand_index) pairs", file=sys.stderr)
        return 2

    dwins: list[int] = []
    dscores: list[float] = []
    n10 = n01 = 0  # discordant: focal won only in A / only in B
    skipped = 0
    for key in common:
        ra, rb = a[key], b[key]
        try:
            seat = ra["bots"].index(args.focal_bot)
        except ValueError:
            skipped += 1
            continue
        win_a = 1 if seat in ra["winners"] else 0
        win_b = 1 if seat in rb["winners"] else 0
        dwins.append(win_a - win_b)
        dscores.append(ra["score_delta"][seat] - rb["score_delta"][seat])
        if win_a and not win_b:
            n10 += 1
        elif win_b and not win_a:
            n01 += 1

    n = len(dwins)
    mean_dwin = sum(dwins) / n
    mean_dscore = sum(dscores) / n
    se_dwin = math.sqrt(sum((d - mean_dwin) ** 2 for d in dwins) / (n - 1)) / math.sqrt(n)
    se_dscore = math.sqrt(sum((d - mean_dscore) ** 2 for d in dscores) / (n - 1)) / math.sqrt(n)
    z_mcnemar = (n10 - n01) / math.sqrt(n10 + n01) if (n10 + n01) else 0.0

    print(f"paired hands: {n} (skipped {skipped}); focal bot: {args.focal_bot}")
    print(
        f"win-rate delta:  {mean_dwin:+.4f} per hand  (se {se_dwin:.4f}, z {mean_dwin / se_dwin:+.2f})"
    )
    print(
        f"score delta:     {mean_dscore:+.3f} per hand (se {se_dscore:.3f}, z {mean_dscore / se_dscore:+.2f})"
    )
    print(
        f"discordant hands: focal-won-only {n10} vs counterpart-won-only {n01} (McNemar z {z_mcnemar:+.2f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
