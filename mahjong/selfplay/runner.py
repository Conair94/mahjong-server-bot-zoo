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
from mahjong.records.reader import RecordCorruptError, read_record
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
        completed = self._completed_indices()
        ruleset: RuleSetRef = cast(
            RuleSetRef,
            {"id": self.ruleset_id, "version": 1, "config_hash": MANIFEST[self.ruleset_id]},
        )

        written: list[Path] = []
        for hand_index in range(self.hands):
            if hand_index in completed:
                continue
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

    def prepare_output(self) -> None:
        """Validate and recover the full corpus before starting any workers.

        The parent owns this phase. Once workers start, each opens only its
        own deterministic filenames, so it cannot inspect a peer's partial
        HEADER or race a peer removing an interrupted hand.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._completed_indices(prepare_workers=True)

    def _seat_assignment(self, hand_index: int) -> list[str]:
        if self.rotation == "none":
            return list(self.bots)
        if self.rotation == "round-robin":
            return rotate_bots(self.bots, hand_index)
        raise ValueError(f"unknown rotation: {self.rotation!r}")

    def _completed_indices(self, *, prepare_workers: bool = False) -> set[int]:
        existing = sorted(self.output_dir.glob("*.jsonl"))
        if not existing:
            return set()
        # In multi-worker mode the parent owns the non-empty-dir gate; each
        # worker scans only its own slice and trusts that sibling-worker
        # records belong there.
        is_parallel = self.worker_count > 1 and not prepare_workers
        if not self.resume and not is_parallel:
            raise RunnerError(
                f"output dir {self.output_dir} is non-empty; pass resume=True to continue"
            )
        if is_parallel:
            # Establish ownership from the filename before opening anything.
            # Peers may be writing a HEADER or removing an interrupted record.
            owned_names = {
                f"{self.hand_id_fn(idx)}.jsonl"
                for idx in range(self.worker_id, self.hands, self.worker_count)
            }
            existing = [path for path in existing if path.name in owned_names]
        completed: set[int] = set()
        partial: list[Path] = []
        for path in existing:
            try:
                header = self._read_header(path)
            except (OSError, ValueError) as exc:
                raise RunnerError(f"cannot read self-play header: {path}") from exc
            if not isinstance(header, dict) or header.get("event") != "HEADER":
                raise RunnerError(f"not a self-play record: {path}")
            meta = header.get("meta") or {}
            idx = meta.get("hand_index")
            if type(idx) is not int or idx < 0:
                raise RunnerError(f"invalid hand index: {path}")
            if path.name != f"{self.hand_id_fn(idx)}.jsonl":
                raise RunnerError(f"self-play filename does not match hand index {idx}: {path}")
            if is_parallel and idx % self.worker_count != self.worker_id:
                continue
            self._validate_configuration(header, idx, path)
            if not self._has_footer(path):
                partial.append(path)
                continue
            try:
                read_record(path)
            except (OSError, RecordCorruptError) as exc:
                raise RunnerError(f"corrupt completed self-play record: {path}") from exc
            if idx in completed:
                raise RunnerError(f"duplicate self-play hand index {idx}: {path}")
            completed.add(idx)
        # Validate the entire slice before deleting anything. A mismatched
        # resume must leave the existing corpus available for investigation.
        for path in partial:
            path.unlink()
        return completed

    def _validate_configuration(self, header: dict[str, Any], idx: int, path: Path) -> None:
        meta = header.get("meta") or {}
        ruleset = header.get("ruleset") or {}
        seats = sorted(header.get("seats") or [], key=lambda seat: seat["seat"])
        bots = [seat.get("identity", {}).get("bot_id") for seat in seats]
        if (
            meta.get("source") != "selfplay"
            or meta.get("master_seed") != hex(self.master_seed)
            or header.get("seed") != str(hand_seed(self.master_seed, idx))
            or ruleset.get("id") != self.ruleset_id
            or ruleset.get("config_hash") != MANIFEST[self.ruleset_id]
            or bots != self._seat_assignment(idx)
        ):
            raise RunnerError(f"self-play configuration mismatch: {path}")

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
