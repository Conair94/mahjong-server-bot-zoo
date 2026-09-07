"""CLI assembly smoke for `python -m mahjong selfplay`.

Spec: docs/specs/selfplay-harness.md § Entry point.

We call `mahjong.cli.selfplay.main` directly (in-process) rather than
spawning a fresh `python -m mahjong` subprocess; the latter, combined
with each hand spawning four bot subprocesses, hits macOS process-table
limits when the full test suite runs in one session. The Layer 5 e2e
test already covers the four-subprocess path with real bots, so this
smoke focuses on argument parsing + registry wiring + one-hand
end-to-end via the real BotRunnerAdapter.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import pytest

from mahjong.adapters.base import SeatAdapter, SeatIdentity
from mahjong.adapters.canned import CannedAdapter
from mahjong.cli.selfplay import main as selfplay_main
from mahjong.selfplay.runner import SelfPlayRunner


def _seed_corpus(output_dir: Path) -> list[Path]:
    def factory(bot_id: str, seat: int) -> SeatAdapter:
        identity = cast(
            SeatIdentity,
            {"kind": "bot", "bot_id": bot_id, "version": "0.1.0", "runtime": "in_process"},
        )
        return cast(SeatAdapter, CannedAdapter(identity=identity, actions=[]))

    return asyncio.run(
        SelfPlayRunner(
            master_seed=1,
            bots=["b_random"] * 4,
            hands=2,
            output_dir=output_dir,
            adapter_factory=factory,
        ).run()
    )


def _parallel_resume_args(output_dir: Path) -> list[str]:
    return [
        "--master-seed",
        "0x1",
        "--hands",
        "2",
        "--bots",
        "b_random,b_random,b_random,b_random",
        "--output-dir",
        str(output_dir),
        "--parallel-hands",
        "2",
        "--resume",
    ]


@pytest.mark.integration
def test_selfplay_cli_runs_one_hand(tmp_path: Path) -> None:
    out = tmp_path / "run"
    rc = selfplay_main(
        [
            "--master-seed",
            "0x1",
            "--hands",
            "1",
            "--bots",
            "b_random,b_random,b_random,b_random",
            "--output-dir",
            str(out),
        ]
    )
    assert rc == 0
    records = list(out.glob("*.jsonl"))
    assert len(records) == 1
    with records[0].open() as fh:
        lines = fh.readlines()
    header = json.loads(lines[0])
    footer = json.loads(lines[-1])
    assert header["event"] == "HEADER"
    assert footer["event"] == "FOOTER"
    assert header["meta"]["hand_index"] == 0
    assert header["meta"]["master_seed"] == "0x1"


def test_selfplay_cli_parallel_spawns_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--parallel-hands N` (parent path) spawns N subprocess workers with
    the right --worker-id/--worker-count flags and aggregates over the shared
    dir afterwards. We monkeypatch Popen to avoid actually launching them.
    """
    from mahjong.cli import selfplay as cli

    spawned: list[list[str]] = []

    class _FakeProc:
        returncode = 0

        def wait(self) -> int:
            return 0

    def _fake_popen(cmd: list[str], **_: object) -> _FakeProc:
        spawned.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(cli.subprocess, "Popen", _fake_popen)

    out = tmp_path / "run"
    rc = cli.main(
        [
            "--master-seed",
            "0x1",
            "--hands",
            "8",
            "--bots",
            "b_random,b_random,b_random,b_random",
            "--output-dir",
            str(out),
            "--parallel-hands",
            "3",
        ]
    )
    assert rc == 0
    assert len(spawned) == 3
    worker_ids = sorted(int(cmd[cmd.index("--worker-id") + 1]) for cmd in spawned)
    assert worker_ids == [0, 1, 2]
    for cmd in spawned:
        assert "--worker-count" in cmd
        assert cmd[cmd.index("--worker-count") + 1] == "3"
        assert "--resume" in cmd


def test_selfplay_cli_parallel_refuses_non_empty_without_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mahjong.cli import selfplay as cli

    out = tmp_path / "run"
    out.mkdir()
    (out / "existing.jsonl").write_text("{}\n")

    # Popen should never be called; assert by raising if it is.
    def _boom(*_: object, **__: object) -> object:
        raise AssertionError("Popen called despite refusal")

    monkeypatch.setattr(cli.subprocess, "Popen", _boom)
    rc = cli.main(
        [
            "--master-seed",
            "0x1",
            "--hands",
            "2",
            "--bots",
            "b_random,b_random,b_random,b_random",
            "--output-dir",
            str(out),
            "--parallel-hands",
            "2",
        ]
    )
    assert rc == 2


def test_parallel_resume_rejects_unreadable_corpus_before_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mahjong.cli import selfplay as cli

    interrupted = tmp_path / "selfplay-00000001.jsonl"
    interrupted.touch()

    def _must_not_spawn(*_: object, **__: object) -> object:
        raise AssertionError("workers started before validating the existing corpus")

    monkeypatch.setattr(cli.subprocess, "Popen", _must_not_spawn)
    rc = cli.main(_parallel_resume_args(tmp_path))

    assert rc == 2
    assert interrupted.read_bytes() == b""


@pytest.mark.parametrize("failure", ["checksum", "configuration", "filename"])
def test_parallel_resume_validates_every_record_before_recovery_or_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from mahjong.cli import selfplay as cli

    interrupted, complete = _seed_corpus(tmp_path)
    interrupted.write_bytes(interrupted.read_bytes().splitlines(keepends=True)[0])
    if failure == "checksum":
        complete.write_bytes(complete.read_bytes().replace(b'"git_sha":"dev"', b'"git_sha":"bad"'))
    elif failure == "configuration":
        complete.write_bytes(
            complete.read_bytes().replace(b'"master_seed":"0x1"', b'"master_seed":"0x2"')
        )
    else:
        complete = complete.rename(tmp_path / "unexpected-name.jsonl")
    before = {path: path.read_bytes() for path in (interrupted, complete)}

    def _must_not_spawn(*_: object, **__: object) -> object:
        raise AssertionError("workers started despite invalid run provenance or records")

    monkeypatch.setattr(cli.subprocess, "Popen", _must_not_spawn)
    assert cli.main(_parallel_resume_args(tmp_path)) == 2
    assert {path: path.read_bytes() for path in before} == before


def test_parallel_resume_recovers_partial_records_before_first_worker_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mahjong.cli import selfplay as cli

    interrupted, complete = _seed_corpus(tmp_path)
    interrupted.write_bytes(interrupted.read_bytes().splitlines(keepends=True)[0])
    complete_bytes = complete.read_bytes()
    spawned: list[list[str]] = []

    class FakeProcess:
        returncode = 0

        def wait(self) -> int:
            return 0

    def start_worker(cmd: list[str], **_: object) -> FakeProcess:
        assert not interrupted.exists(), "recovery must finish before any worker is launched"
        assert complete.read_bytes() == complete_bytes
        spawned.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", start_worker)
    assert cli.main(_parallel_resume_args(tmp_path)) == 0
    assert len(spawned) == 2


def test_selfplay_cli_rejects_unknown_bot(tmp_path: Path) -> None:
    out = tmp_path / "run"
    rc = selfplay_main(
        [
            "--master-seed",
            "0x1",
            "--hands",
            "1",
            "--bots",
            "b_random,b_random,b_random,not_a_real_bot",
            "--output-dir",
            str(out),
        ]
    )
    assert rc == 2
