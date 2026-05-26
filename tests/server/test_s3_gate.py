"""S3 exit gate — server-lifecycle.md fixture 22.

Spin up `python -m mahjong serve` in a subprocess:
1. Create an account via the account CLI (direct DB seed since the wire
   protocol does not yet expose CREATE_ACCOUNT — auth.md § Future work).
2. Connect a client; AUTH_REQUEST; CREATE_TABLE; ATTACH; play a hand.
3. SIGTERM the server; await clean exit (exit code 0).
4. Re-open the Persistence directly; assert ``find_hands_by_account`` returns
   the played hand, FOOTER-complete and finalised.

This is the integration test that proves the pragmatic-cut Layer 8 lifecycle
holds together end-to-end.  Marked slow because it spawns subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest
import websockets

from mahjong.persistence import Persistence

pytestmark = [pytest.mark.asyncio, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return cast(int, s.getsockname()[1])


def _wait_for_port(host: str, port: int, *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"server did not bind {host}:{port} within {timeout}s")


async def _play_one_hand(url: str, *, username: str, password: str) -> int:
    """Auth, create table, attach seat 0, play hand to HAND_END. Returns table_id."""
    table_id: int = -1
    async with websockets.connect(url, subprotocols=["mahjong-v1"]) as ws:
        await ws.recv()  # HELLO
        await ws.send(
            json.dumps(
                {"kind": "AUTH_REQUEST", "username": username, "password": password}
            )
        )
        auth = json.loads(cast(str, await ws.recv()))
        assert auth["ok"], auth

        await ws.send(json.dumps({"kind": "CREATE_TABLE", "ruleset": "mcr-2006"}))
        created = json.loads(cast(str, await ws.recv()))
        assert created["kind"] == "TABLE_CREATED", created
        table_id = int(created["table_id"])

        await ws.send(json.dumps({"kind": "ATTACH", "table_id": table_id, "seat": 0}))
        attached = json.loads(cast(str, await ws.recv()))
        assert attached["kind"] == "ATTACHED", attached

        deadline = asyncio.get_event_loop().time() + 90.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            assert remaining > 0, "client hung waiting for HAND_END"
            msg = json.loads(
                cast(str, await asyncio.wait_for(ws.recv(), timeout=remaining))
            )
            if msg["kind"] == "PROMPT":
                await ws.send(
                    json.dumps(
                        {
                            "kind": "ACTION",
                            "prompt_id": msg["prompt_id"],
                            "action": msg["default_action"],
                        }
                    )
                )
            elif msg["kind"] == "HAND_END":
                break

    return table_id


async def test_s3_gate_account_play_drain_query(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    port = _free_port()

    env = {
        **os.environ,
        "MAHJONG_DATA_DIR": str(data_dir),
        "MAHJONG_LISTEN_ADDR": f"127.0.0.1:{port}",
        "MAHJONG_SHUTDOWN_TIMEOUT_SECONDS": "10",
        "MAHJONG_LOG_LEVEL": "INFO",
        "PYTHONUNBUFFERED": "1",
    }

    # 1. Seed account via the account CLI subprocess.
    create_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mahjong",
            "account",
            "create",
            "--username",
            "alice",
            "--display",
            "Alice",
            "--admin",
            "--password-stdin",
        ],
        input="alicealice\n",
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert create_proc.returncode == 0, (
        f"account create failed: {create_proc.stderr}"
    )
    assert "account_id=1" in create_proc.stdout, create_proc.stdout

    # 2. Launch the server.
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "mahjong", "serve"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_port("127.0.0.1", port, timeout=20.0)

        # 3. Play one hand against the live server.
        url = f"ws://127.0.0.1:{port}"
        table_id = await _play_one_hand(
            url, username="alice", password="alicealice"
        )
        assert table_id > 0

        # Give the server a tiny moment to finalize.
        await asyncio.sleep(0.3)

        # 4. SIGTERM the server; await clean exit.
        server_proc.send_signal(signal.SIGTERM)
        try:
            _stdout, stderr = server_proc.communicate(timeout=20)
        except subprocess.TimeoutExpired as exc:
            server_proc.kill()
            _stdout, stderr = server_proc.communicate(timeout=5)
            raise AssertionError(
                f"server did not exit within 20s of SIGTERM\nstderr:\n{stderr}"
            ) from exc
        assert server_proc.returncode == 0, (
            f"server exited {server_proc.returncode}\nstderr:\n{stderr}"
        )
    finally:
        if server_proc.poll() is None:
            server_proc.kill()
            server_proc.communicate(timeout=5)

    # 5. Re-open Persistence and query.
    persistence = Persistence(data_dir / "mahjong.db", data_dir)
    try:
        hands = persistence.find_hands_by_account(1)
        assert len(hands) == 1, f"Expected 1 hand, got {hands}"
        row = hands[0]
        assert row.terminal_kind in {"HU", "EXHAUSTIVE_DRAW"}, row
        assert row.ended_at_ms is not None
        assert row.record_checksum and row.record_checksum.startswith("sha256:")
        # Record file exists on disk.
        record_path = data_dir / row.record_path
        assert record_path.is_file(), f"record file missing at {record_path}"
    finally:
        persistence.close()
