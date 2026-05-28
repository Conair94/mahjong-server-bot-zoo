"""Tests for `mahjong.selfplay.runner.SelfPlayRunner`.

Spec: docs/specs/selfplay-harness.md § Run lifecycle, § Seed management.

These tests parameterize the runner's adapter factory to use
`CannedAdapter`s so multi-hand orchestration can be tested without
spawning subprocesses on every hand. The real-subprocess path is
exercised by `test_cli.py::test_selfplay_subprocess_smoke`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.adapters.base import (
    LeaveReason,
    Prompt,
    SeatAdapter,
    SeatContext,
    SeatIdentity,
)
from mahjong.adapters.canned import CannedAdapter
from mahjong.engine.types import Action, SeatView
from mahjong.selfplay.runner import RunnerError, SelfPlayRunner
from mahjong.selfplay.seeds import hand_seed

pytestmark = pytest.mark.asyncio

MASTER = 0xDEADBEEF12345678


# --- A spy adapter that records what `seated()` and `decide()` see ---


class _SpyAdapter:
    """Wraps a CannedAdapter and records the SeatView it was passed."""

    kind = "bot"

    def __init__(self, bot_id: str, seat: int) -> None:
        self.identity: SeatIdentity = cast(
            SeatIdentity,
            {"kind": "bot", "bot_id": bot_id, "version": "0.0.0", "runtime": "in_process"},
        )
        self._inner = CannedAdapter(identity=self.identity, actions=[])
        self.seat = seat
        self.seated_view: SeatView | None = None
        self.seated_ctx: SeatContext | None = None

    async def seated(self, ctx: SeatContext) -> None:
        self.seated_ctx = ctx
        self.seated_view = ctx["initial_view"]
        await self._inner.seated(ctx)

    async def observe(self, event: dict[str, Any], view: SeatView) -> None:
        await self._inner.observe(event, view)

    async def decide(self, prompt: Prompt) -> Action:
        return await self._inner.decide(prompt)

    async def left(self, reason: LeaveReason) -> None:
        await self._inner.left(reason)


def _spy_factory(captured: dict[int, _SpyAdapter]):
    def _make(bot_id: str, seat: int) -> SeatAdapter:
        spy = _SpyAdapter(bot_id, seat)
        captured[seat] = spy
        return cast(SeatAdapter, spy)

    return _make


def _canned_factory(bot_id: str, seat: int) -> SeatAdapter:
    identity: SeatIdentity = cast(
        SeatIdentity,
        {"kind": "bot", "bot_id": bot_id, "version": "0.0.0", "runtime": "in_process"},
    )
    return cast(SeatAdapter, CannedAdapter(identity=identity, actions=[]))


def _read_header(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return cast(dict[str, Any], json.loads(fh.readline()))


# --- Basic run: HEADER carries meta + seed = hand_seed ---


async def test_serial_run_writes_one_record_per_hand(tmp_path: Path) -> None:
    runner = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random", "b_random", "b_random", "b_random"],
        hands=3,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
    )
    paths = await runner.run()
    assert len(paths) == 3
    for idx, p in enumerate(paths):
        header = _read_header(p)
        assert header["event"] == "HEADER"
        assert header["meta"]["master_seed"] == hex(MASTER)
        assert header["meta"]["hand_index"] == idx
        assert header["meta"]["source"] == "selfplay"
        assert header["seed"] == str(hand_seed(MASTER, idx))


async def test_run_determinism_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs from the same master_seed into clean output dirs produce
    byte-identical records (the load-bearing determinism claim).

    Wall-clock `ts` is pinned via monkeypatch so the determinism is over
    seed-driven engine state, not over time-of-day.
    """
    from mahjong.table import manager as mgr

    monkeypatch.setattr(mgr, "_now_ts", lambda: "2026-05-21T00:00:00.000Z")

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"

    def make_runner(out: Path) -> SelfPlayRunner:
        return SelfPlayRunner(
            master_seed=MASTER,
            bots=["b_random"] * 4,
            hands=2,
            output_dir=out,
            adapter_factory=_canned_factory,
            hand_id_fn=lambda idx: f"hand-{idx:04d}",
        )

    paths_a = await make_runner(out_a).run()
    paths_b = await make_runner(out_b).run()
    assert len(paths_a) == len(paths_b) == 2
    for pa, pb in zip(paths_a, paths_b, strict=True):
        assert pa.read_bytes() == pb.read_bytes()


# --- Resume semantics ---


async def test_run_refuses_non_empty_dir_without_resume(tmp_path: Path) -> None:
    (tmp_path / "existing.jsonl").write_text("{}\n")
    runner = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=1,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
    )
    with pytest.raises(RunnerError):
        await runner.run()


async def test_resume_continues_from_max_hand_index(tmp_path: Path) -> None:
    first = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=2,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
    )
    paths1 = await first.run()
    assert len(paths1) == 2

    second = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=4,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
        resume=True,
    )
    paths2 = await second.run()
    # Resume only plays 2 new hands; returns just those.
    assert len(paths2) == 2

    all_records = sorted(tmp_path.glob("*.jsonl"))
    assert len(all_records) == 4
    indices = sorted(_read_header(p)["meta"]["hand_index"] for p in all_records)
    assert indices == [0, 1, 2, 3]


async def test_resume_deletes_partial_records(tmp_path: Path) -> None:
    """A file with a HEADER but no FOOTER is treated as a crash; resume
    deletes it and replays that `hand_index`."""
    # Manufacture a partial record at hand_index=0.
    partial = tmp_path / "partial.jsonl"
    partial.write_text(
        json.dumps(
            {
                "event": "HEADER",
                "seq": 0,
                "turn_index": 0,
                "phase": "DEAL",
                "ts": "2026-05-21T00:00:00.000Z",
                "meta": {"master_seed": hex(MASTER), "hand_index": 0, "source": "selfplay"},
            }
        )
        + "\n"
    )

    runner = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=1,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
        resume=True,
    )
    paths = await runner.run()
    assert not partial.exists(), "partial record should have been cleaned up"
    assert len(paths) == 1
    header = _read_header(paths[0])
    assert header["meta"]["hand_index"] == 0


# --- Privacy: no canonical state leaks to adapters in default mode ---


async def test_default_mode_only_passes_seat_views(tmp_path: Path) -> None:
    captured: dict[int, _SpyAdapter] = {}
    runner = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=1,
        output_dir=tmp_path,
        adapter_factory=_spy_factory(captured),
    )
    await runner.run()
    assert set(captured) == {0, 1, 2, 3}
    for seat, spy in captured.items():
        view = spy.seated_view
        assert view is not None
        # Foreign concealed must be a count-only dict (SeatViewOpponent),
        # never a list of concrete tile tokens (SeatViewSelf).
        for other_seat in range(4):
            if other_seat == seat:
                continue
            foreign = view["seats"][other_seat]["concealed"]
            assert isinstance(foreign, dict), (
                f"foreign concealed leaked to seat {seat} for seat {other_seat}: "
                f"got {type(foreign).__name__}, expected count-only dict"
            )
        # `allow_god_view` must be unset / falsy.
        assert not spy.seated_ctx.get("allow_god_view", False)  # type: ignore[union-attr]


# --- Rotation: HEADER seats reflect rotated bot_ids ---


async def test_rotation_round_robin_changes_seats(tmp_path: Path) -> None:
    runner = SelfPlayRunner(
        master_seed=MASTER,
        bots=["a", "b", "c", "d"],
        hands=4,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
        rotation="round-robin",
    )
    paths = await runner.run()
    assert len(paths) == 4
    expected = [
        ["a", "b", "c", "d"],
        ["d", "a", "b", "c"],
        ["c", "d", "a", "b"],
        ["b", "c", "d", "a"],
    ]
    for hand_idx, p in enumerate(paths):
        header = _read_header(p)
        seat_ids = [s["identity"]["bot_id"] for s in header["seats"]]
        assert seat_ids == expected[hand_idx], f"hand {hand_idx}: {seat_ids}"


async def test_rotation_none_keeps_seats(tmp_path: Path) -> None:
    runner = SelfPlayRunner(
        master_seed=MASTER,
        bots=["a", "b", "c", "d"],
        hands=2,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
        rotation="none",
    )
    paths = await runner.run()
    for p in paths:
        header = _read_header(p)
        assert [s["identity"]["bot_id"] for s in header["seats"]] == ["a", "b", "c", "d"]


# --- Argument validation ---


async def test_wrong_bot_count_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SelfPlayRunner(
            master_seed=MASTER,
            bots=["a", "b", "c"],
            hands=1,
            output_dir=tmp_path,
            adapter_factory=_canned_factory,
        )


# --- 6.1b: worker partitioning (`--parallel-hands` foundation) ---


async def test_worker_partition_plays_only_its_slice(tmp_path: Path) -> None:
    """worker_count=2 splits hand_index by parity; each worker plays only its slice."""
    out = tmp_path / "shared"
    worker0 = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=4,
        output_dir=out,
        adapter_factory=_canned_factory,
        worker_id=0,
        worker_count=2,
    )
    paths0 = await worker0.run()
    indices0 = sorted(_read_header(p)["meta"]["hand_index"] for p in paths0)
    assert indices0 == [0, 2]

    worker1 = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=4,
        output_dir=out,
        adapter_factory=_canned_factory,
        worker_id=1,
        worker_count=2,
    )
    paths1 = await worker1.run()
    indices1 = sorted(_read_header(p)["meta"]["hand_index"] for p in paths1)
    assert indices1 == [1, 3]

    all_records = sorted(out.glob("*.jsonl"))
    all_indices = sorted(_read_header(p)["meta"]["hand_index"] for p in all_records)
    assert all_indices == [0, 1, 2, 3]


async def test_parallel_equivalence_per_hand_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec fixture 3: serial and partitioned-parallel runs from the same
    master_seed produce per-hand byte-identical records (keyed by hand_index).
    """
    from mahjong.table import manager as mgr

    monkeypatch.setattr(mgr, "_now_ts", lambda: "2026-05-21T00:00:00.000Z")

    serial_dir = tmp_path / "serial"
    parallel_dir = tmp_path / "parallel"

    serial = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=4,
        output_dir=serial_dir,
        adapter_factory=_canned_factory,
        hand_id_fn=lambda idx: f"hand-{idx:04d}",
    )
    await serial.run()

    for worker_id in (0, 1):
        await SelfPlayRunner(
            master_seed=MASTER,
            bots=["b_random"] * 4,
            hands=4,
            output_dir=parallel_dir,
            adapter_factory=_canned_factory,
            hand_id_fn=lambda idx: f"hand-{idx:04d}",
            worker_id=worker_id,
            worker_count=2,
        ).run()

    serial_records = {
        _read_header(p)["meta"]["hand_index"]: p.read_bytes() for p in serial_dir.glob("*.jsonl")
    }
    parallel_records = {
        _read_header(p)["meta"]["hand_index"]: p.read_bytes() for p in parallel_dir.glob("*.jsonl")
    }
    assert set(serial_records) == set(parallel_records) == {0, 1, 2, 3}
    for idx in serial_records:
        assert serial_records[idx] == parallel_records[idx], f"hand {idx} differs"


async def test_worker_does_not_refuse_non_empty_dir(tmp_path: Path) -> None:
    """In multi-worker mode the parent owns the empty-dir check; the worker
    must tolerate other workers' records in the shared dir without raising."""
    # Pre-seed a record belonging to another worker's slice.
    (tmp_path / "selfplay-00000001.jsonl").write_text(
        json.dumps(
            {
                "event": "HEADER",
                "seq": 0,
                "turn_index": 0,
                "phase": "DEAL",
                "ts": "2026-05-21T00:00:00.000Z",
                "meta": {"master_seed": hex(MASTER), "hand_index": 1, "source": "selfplay"},
            }
        )
        + "\n"
        + json.dumps({"event": "FOOTER", "seq": 1})
        + "\n"
    )
    worker0 = SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=2,
        output_dir=tmp_path,
        adapter_factory=_canned_factory,
        worker_id=0,
        worker_count=2,
    )
    paths = await worker0.run()
    indices = sorted(_read_header(p)["meta"]["hand_index"] for p in paths)
    assert indices == [0]


async def test_worker_resume_filters_by_slice(tmp_path: Path) -> None:
    """Worker resume scan only considers files in its own slice; running again
    on a populated dir skips the hands it already played."""
    out = tmp_path / "shared"
    # First pass: worker 0 plays hands 0 and 2 (worker_count=2).
    await SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=4,
        output_dir=out,
        adapter_factory=_canned_factory,
        worker_id=0,
        worker_count=2,
    ).run()
    # Second pass: worker 0 again, hands extended to 6 — should only play 4.
    paths = await SelfPlayRunner(
        master_seed=MASTER,
        bots=["b_random"] * 4,
        hands=6,
        output_dir=out,
        adapter_factory=_canned_factory,
        worker_id=0,
        worker_count=2,
    ).run()
    indices = sorted(_read_header(p)["meta"]["hand_index"] for p in paths)
    assert indices == [4]


async def test_invalid_worker_args_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SelfPlayRunner(
            master_seed=MASTER,
            bots=["a"] * 4,
            hands=1,
            output_dir=tmp_path,
            adapter_factory=_canned_factory,
            worker_count=0,
        )
    with pytest.raises(ValueError):
        SelfPlayRunner(
            master_seed=MASTER,
            bots=["a"] * 4,
            hands=1,
            output_dir=tmp_path,
            adapter_factory=_canned_factory,
            worker_id=2,
            worker_count=2,
        )
    with pytest.raises(ValueError):
        SelfPlayRunner(
            master_seed=MASTER,
            bots=["a"] * 4,
            hands=1,
            output_dir=tmp_path,
            adapter_factory=_canned_factory,
            worker_id=-1,
            worker_count=2,
        )
