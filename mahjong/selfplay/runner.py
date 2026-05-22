"""Serial self-play runner.

Spec: docs/specs/selfplay-harness.md § Run lifecycle, § Record output,
      § `SelfPlayDriverAdapter` (god-view path is *not* implemented here
      yet — default mode only, per Step 6.1a scope).

The runner glues together: hand seeds (`seeds.hand_seed`), seat rotation
(`seeds.rotate_bots`), per-hand adapter construction (via a caller-supplied
factory), and the table manager's `run_hand`. Parallelism (6.1b) and the
eval-summary aggregator (6.1c) build on top of this; they are not wired
in here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from mahjong.adapters.base import SeatAdapter
from mahjong.engine.rulesets import MANIFEST
from mahjong.engine.types import RuleSetRef
from mahjong.selfplay.seeds import hand_seed, rotate_bots
from mahjong.table.manager import run_hand

AdapterFactory = Callable[[str, int], SeatAdapter]
HandIdFn = Callable[[int], str]

Rotation = Literal["none", "round-robin"]


class RunnerError(Exception):
    """Self-play runner refused to start (non-empty output dir without resume,
    bad arguments at run time, etc.)."""


def _default_hand_id(hand_index: int) -> str:
    # UUIDv7 would be nicer; for 6.1a a deterministic prefix is fine and
    # keeps the determinism fixture stable without an explicit override.
    return f"selfplay-{hand_index:08d}"


class SelfPlayRunner:
    """Drives a self-play run: hands [start, hands) into `output_dir`.

    Construct, then `await runner.run()`. `output_dir` is created if missing.
    """

    def __init__(
        self,
        *,
        master_seed: int,
        bots: list[str],
        hands: int,
        output_dir: Path,
        adapter_factory: AdapterFactory,
        ruleset_id: str = "mcr-2006",
        rotation: Rotation = "none",
        resume: bool = False,
        hand_id_fn: HandIdFn | None = None,
        server_info: dict[str, Any] | None = None,
        run_hand_kwargs: dict[str, Any] | None = None,
        worker_id: int = 0,
        worker_count: int = 1,
    ) -> None:
        if len(bots) != 4:
            raise ValueError(f"bots must have exactly 4 entries, got {len(bots)}")
        if hands <= 0:
            raise ValueError(f"hands must be positive, got {hands}")
        if ruleset_id not in MANIFEST:
            raise ValueError(f"unknown ruleset: {ruleset_id!r}")
        if worker_count < 1:
            raise ValueError(f"worker_count must be >= 1, got {worker_count}")
        if not 0 <= worker_id < worker_count:
            raise ValueError(f"worker_id must be in [0, {worker_count}), got {worker_id}")
        self.worker_id = worker_id
        self.worker_count = worker_count
        self.master_seed = master_seed
        self.bots = list(bots)
        self.hands = hands
        self.output_dir = output_dir
        self.adapter_factory = adapter_factory
        self.ruleset_id = ruleset_id
        self.rotation = rotation
        self.resume = resume
        self.hand_id_fn = hand_id_fn or _default_hand_id
        self.server_info = server_info or {
            "version": "selfplay",
            "git_sha": "dev",
            "host": "local",
        }
        self.run_hand_kwargs = run_hand_kwargs or {}

    async def run(self) -> list[Path]:
        """Play hands [start, self.hands). Returns the record paths written
        by *this* invocation (resume-skipped hands are excluded)."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        start = self._compute_start_index()
        ruleset: RuleSetRef = cast(
            RuleSetRef,
            {"id": self.ruleset_id, "version": 1, "config_hash": MANIFEST[self.ruleset_id]},
        )

        written: list[Path] = []
        for hand_index in range(start, self.hands):
            if hand_index % self.worker_count != self.worker_id:
                continue
            seat_bots = self._seat_assignment(hand_index)
            adapters = [self.adapter_factory(bot_id, seat) for seat, bot_id in enumerate(seat_bots)]
            seed = hand_seed(self.master_seed, hand_index)
            hand_id = self.hand_id_fn(hand_index)
            record_path = self.output_dir / f"{hand_id}.jsonl"
            meta = {
                "master_seed": hex(self.master_seed),
                "hand_index": hand_index,
                "source": "selfplay",
            }
            await run_hand(
                adapters=adapters,
                ruleset=ruleset,
                seed=seed,
                hand_id=hand_id,
                record_path=record_path,
                server_info=self.server_info,
                meta=meta,
                **self.run_hand_kwargs,
            )
            written.append(record_path)
        return written

    # --- Helpers -------------------------------------------------------

    def _seat_assignment(self, hand_index: int) -> list[str]:
        if self.rotation == "none":
            return list(self.bots)
        if self.rotation == "round-robin":
            return rotate_bots(self.bots, hand_index)
        raise ValueError(f"unknown rotation: {self.rotation!r}")

    def _compute_start_index(self) -> int:
        existing = sorted(self.output_dir.glob("*.jsonl"))
        if not existing:
            return 0
        # In multi-worker mode the parent owns the non-empty-dir gate; each
        # worker scans only its own slice and trusts that sibling-worker
        # records belong there.
        is_parallel = self.worker_count > 1
        if not self.resume and not is_parallel:
            raise RunnerError(
                f"output dir {self.output_dir} is non-empty; pass resume=True to continue"
            )
        max_index = -1
        for path in existing:
            try:
                header = self._read_header(path)
            except (OSError, json.JSONDecodeError):
                continue
            if header.get("event") != "HEADER":
                continue
            meta = header.get("meta") or {}
            idx = meta.get("hand_index")
            if not isinstance(idx, int):
                continue
            if idx % self.worker_count != self.worker_id:
                continue
            if not self._has_footer(path):
                # Partial record — delete and replay this hand_index.
                path.unlink()
                continue
            if idx > max_index:
                max_index = idx
        return max_index + 1

    @staticmethod
    def _read_header(path: Path) -> dict[str, Any]:
        with path.open() as fh:
            line = fh.readline()
        return cast(dict[str, Any], json.loads(line))

    @staticmethod
    def _has_footer(path: Path) -> bool:
        last = b""
        with path.open("rb") as fh:
            for raw in fh:
                line = raw.rstrip(b"\r\n")
                if line:
                    last = line
        if not last:
            return False
        try:
            obj = json.loads(last)
        except json.JSONDecodeError:
            return False
        return isinstance(obj, dict) and obj.get("event") == "FOOTER"


__all__ = ["RunnerError", "SelfPlayRunner"]
