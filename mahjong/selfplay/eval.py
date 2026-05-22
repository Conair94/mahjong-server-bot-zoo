"""Eval-summary aggregator for self-play record sets.

Spec: docs/specs/selfplay-harness.md § Eval-summary output.

Step 6.1c: reads a collection of self-play JSONL records and computes per-seat
and per-bot statistics (win rate, avg score/hand, deal-in rate, avg fan when
won). Consumed by the --eval-summary CLI flag and future training pipelines.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HandOutcome:
    """Parsed outcome extracted from one record file."""

    bot_ids: list[str]        # bot_id per seat, ordered [0, 1, 2, 3]
    kind: str                 # "HU" | "DRAW"
    winners: list[int]        # winning seat indices; empty for DRAW
    deal_in_seat: int | None  # seat that dealt into the winning hand
    fan_total: int            # total fan; 0 for DRAW
    score_delta: list[int]    # score change per seat, len 4


@dataclass
class SeatSummary:
    """Accumulated statistics for one seat (or one bot across all its seats)."""

    hands: int = 0
    wins: int = 0
    deal_ins: int = 0
    score_total: int = 0
    fan_total_when_won: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.hands if self.hands else 0.0

    @property
    def avg_score(self) -> float:
        return self.score_total / self.hands if self.hands else 0.0

    @property
    def deal_in_rate(self) -> float:
        return self.deal_ins / self.hands if self.hands else 0.0

    @property
    def avg_fan_when_won(self) -> float:
        return self.fan_total_when_won / self.wins if self.wins else 0.0


@dataclass
class EvalSummary:
    """Aggregated statistics over a collection of records."""

    total_hands: int
    master_seed: str | None           # from first record meta, if present
    ruleset: str | None               # ruleset id from first record
    bot_ids_config: list[str] | None  # bot assignment from first record
    per_seat: list[SeatSummary] = field(
        default_factory=lambda: [SeatSummary() for _ in range(4)]
    )
    per_bot: dict[str, SeatSummary] = field(default_factory=dict)


def parse_record(path: Path) -> HandOutcome | None:
    """Parse a single JSONL record. Returns None if HAND_END is absent."""
    header: dict[str, Any] | None = None
    hand_end: dict[str, Any] | None = None

    try:
        with path.open() as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                evt = obj.get("event")
                if evt == "HEADER" and header is None:
                    header = obj
                elif evt == "HAND_END":
                    hand_end = obj
    except OSError:
        return None

    if header is None or hand_end is None:
        return None

    seats_raw = sorted(header.get("seats") or [], key=lambda s: s.get("seat", 0))
    bot_ids: list[str] = []
    for s in seats_raw:
        identity = s.get("identity") or {}
        bot_ids.append(identity.get("bot_id") or identity.get("user_id") or "unknown")

    return HandOutcome(
        bot_ids=bot_ids,
        kind=hand_end.get("kind", "DRAW"),
        winners=hand_end.get("winner") or [],
        deal_in_seat=hand_end.get("deal_in_seat"),
        fan_total=hand_end.get("fan_total") or 0,
        score_delta=hand_end.get("score_delta") or [0, 0, 0, 0],
    )


def _read_header_fields(paths: list[Path]) -> tuple[str | None, str | None]:
    """Return (master_seed, ruleset_id) from the first parseable HEADER."""
    for path in paths:
        try:
            with path.open() as fh:
                line = fh.readline().strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("event") != "HEADER":
                continue
            meta = obj.get("meta") or {}
            ruleset_obj = obj.get("ruleset") or {}
            return meta.get("master_seed"), ruleset_obj.get("id")
        except (OSError, json.JSONDecodeError):
            continue
    return None, None


def aggregate(paths: Iterable[Path]) -> EvalSummary:
    """Aggregate eval stats from an iterable of record paths.

    Malformed or incomplete records are silently skipped.
    """
    path_list = list(paths)

    per_seat: list[SeatSummary] = [SeatSummary() for _ in range(4)]
    per_bot: dict[str, SeatSummary] = {}
    total_hands = 0
    bot_ids_config: list[str] | None = None

    for path in path_list:
        outcome = parse_record(path)
        if outcome is None:
            continue

        if bot_ids_config is None:
            bot_ids_config = outcome.bot_ids

        total_hands += 1

        for seat in range(4):
            bot_id = outcome.bot_ids[seat] if seat < len(outcome.bot_ids) else "unknown"
            seat_stat = per_seat[seat]
            bot_stat = per_bot.setdefault(bot_id, SeatSummary())

            delta = outcome.score_delta[seat] if seat < len(outcome.score_delta) else 0

            seat_stat.hands += 1
            seat_stat.score_total += delta
            bot_stat.hands += 1
            bot_stat.score_total += delta

            if seat in outcome.winners:
                seat_stat.wins += 1
                seat_stat.fan_total_when_won += outcome.fan_total
                bot_stat.wins += 1
                bot_stat.fan_total_when_won += outcome.fan_total

            if outcome.deal_in_seat == seat:
                seat_stat.deal_ins += 1
                bot_stat.deal_ins += 1

    master_seed, ruleset = _read_header_fields(path_list)

    return EvalSummary(
        total_hands=total_hands,
        master_seed=master_seed,
        ruleset=ruleset,
        bot_ids_config=bot_ids_config,
        per_seat=per_seat,
        per_bot=per_bot,
    )


def format_summary(summary: EvalSummary) -> str:
    """Format an EvalSummary as a human-readable table string (spec layout)."""
    lines: list[str] = []

    seed_part = f", master_seed={summary.master_seed}" if summary.master_seed else ""
    lines.append(f"Self-play run: {summary.total_hands} hands{seed_part}")

    if summary.bot_ids_config:
        lines.append(f"Bots (seat 0..3): {', '.join(summary.bot_ids_config)}")
    if summary.ruleset:
        lines.append(f"Ruleset: {summary.ruleset}")

    if summary.total_hands == 0:
        lines.append("(no records)")
        return "\n".join(lines)

    lines.append("")

    col_w = 10

    def _row(label: str, values: list[str]) -> str:
        return f"{label:<20s}" + "".join(f"{v:>{col_w}}" for v in values)

    header_row = f"{'':20s}" + "".join(f"{'seat ' + str(i):>{col_w}}" for i in range(4))
    lines.append(header_row)
    lines.append(
        _row("Win rate", [f"{summary.per_seat[s].win_rate:.3f}" for s in range(4)])
    )
    lines.append(
        _row("Avg score/hand", [f"{summary.per_seat[s].avg_score:+.2f}" for s in range(4)])
    )
    lines.append(
        _row("Deal-in rate", [f"{summary.per_seat[s].deal_in_rate:.3f}" for s in range(4)])
    )
    lines.append(
        _row(
            "Avg fan when won",
            [
                f"{summary.per_seat[s].avg_fan_when_won:.1f}" if summary.per_seat[s].wins else "--"
                for s in range(4)
            ],
        )
    )

    if summary.per_bot:
        lines.append("")
        lines.append("Bot-aggregated (regardless of seat):")
        bot_names = sorted(summary.per_bot)
        bot_col_w = max(col_w, *(len(n) + 2 for n in bot_names))

        def _bot_row(label: str, vals: list[str]) -> str:
            return f"{label:<20s}" + "".join(f"{v:>{bot_col_w}}" for v in vals)

        lines.append(
            f"{'':20s}" + "".join(f"{n:>{bot_col_w}}" for n in bot_names)
        )
        lines.append(
            _bot_row("Win rate", [f"{summary.per_bot[n].win_rate:.3f}" for n in bot_names])
        )
        lines.append(
            _bot_row(
                "Avg score/hand",
                [f"{summary.per_bot[n].avg_score:+.2f}" for n in bot_names],
            )
        )
        lines.append(
            _bot_row(
                "Deal-in rate",
                [f"{summary.per_bot[n].deal_in_rate:.3f}" for n in bot_names],
            )
        )

    return "\n".join(lines)


__all__ = [
    "HandOutcome",
    "SeatSummary",
    "EvalSummary",
    "parse_record",
    "aggregate",
    "format_summary",
]
