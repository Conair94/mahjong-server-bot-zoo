"""BotRunnerAdapter: lifecycle, framing, time-budget enforcement.

Spec: docs/specs/bot-runner-protocol.md.
Fixtures: 2 (HELLO success), 3 (HELLO skip), 4 (spawn fail),
          5 (per-turn timeout), 8 (framing violations), 9 (illegal action surfacing).

The trivial-bot-vs-three-CannedAdapter gate is exercised by
`test_bot_runner_plays_a_hand_against_canned`. Step 5.2's gate per
CHECKLIST.md.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest

from mahjong.adapters.base import (
    Prompt,
    SeatContext,
    SeatError,
    SeatTimeout,
)
from mahjong.adapters.bot_runner import BotRunnerAdapter
from mahjong.bots.manifest import BotManifest, parse_manifest

pytestmark = pytest.mark.asyncio

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PASS_ACTION: dict[str, Any] = {"type": "PASS"}


# --- Helpers ---------------------------------------------------------------


def _write_bot_script(tmp_path: Path, body: str, name: str = "bot.py") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _manifest_for(
    tmp_path: Path,
    *,
    command: list[str] | None = None,
    budget_ms: int = 1000,
    handshake_ms: int = 500,
    teardown_grace_ms: int = 500,
) -> BotManifest:
    return parse_manifest(
        {
            "bot_id": "b_test",
            "version": "0.0.1",
            "display_name": "Test bot",
            "directory": str(tmp_path),
            "command": command or [sys.executable, "-u", "bot.py"],
            # Bots that `import mahjong.bots.sdk` need to find the package
            # inside the project venv. The sandbox env whitelist strips
            # PYTHONPATH; declare it via manifest.env instead.
            "env": {"PYTHONUNBUFFERED": "1", "PYTHONPATH": str(PROJECT_ROOT)},
            "budget_ms_per_turn": budget_ms,
            "handshake_deadline_ms": handshake_ms,
            "teardown_grace_ms": teardown_grace_ms,
            "limits": {
                "memory_mb": 256,
                "cpu_seconds": 30,
                "max_fds": 64,
                "max_processes": 4,
                "network": "deny",
            },
            "ruleset_supported": ["mcr-2006"],
            "format_supported": ["botzone-csm"],
        }
    )


def _make_ctx(seat: int = 0) -> SeatContext:
    return cast(
        SeatContext,
        {
            "seat": seat,
            "hand_id": "test-hand",
            "ruleset": {"id": "mcr-2006", "version": 1, "config_hash": "sha256:test"},
            "seat_deadline_ms": 1000,
            "initial_view": {},
        },
    )


def _prompt(
    kind: str = "DISCARD",
    legal: list[dict[str, Any]] | None = None,
    default: dict[str, Any] | None = None,
    deadline_s: float = 5.0,
) -> Prompt:
    default = default if default is not None else PASS_ACTION
    return cast(
        Prompt,
        {
            "kind": kind,
            "view": {},
            "legal_actions": legal if legal is not None else [default],
            "default_action": default,
            "deadline": asyncio.get_event_loop().time() + deadline_s,
            "issued_at": asyncio.get_event_loop().time(),
            "context": {},
        },
    )


# Grammar/parser tests live in test_bot_runner_parser.py (no asyncio).

# --- Subprocess-based scenarios -------------------------------------------


_SDK_BOT_TEMPLATE = """
import sys
from mahjong.bots.sdk import run_bot

def decide(req):
    default = req.get("default_action") or {"type": "PASS"}
    t = default.get("type")
    if t == "PASS":
        return "PASS"
    if t == "PLAY":
        return f"PLAY {default['tile']}"
    return "PASS"

if __name__ == "__main__":
    run_bot(decide, bot_id="b_test", version="0.0.1")
"""


async def test_hello_handshake_success(tmp_path: Path) -> None:
    """Fixture 2: SDK-based bot completes the handshake and decides."""
    _write_bot_script(tmp_path, _SDK_BOT_TEMPLATE)
    manifest = _manifest_for(tmp_path)
    adapter = BotRunnerAdapter(manifest)
    try:
        await adapter.seated(_make_ctx())
        result = await adapter.decide(_prompt())
        assert result == {"type": "PASS"}
        assert adapter._mode == "long_running"
        assert adapter._handshake_skipped is False
    finally:
        await adapter.left("HAND_ENDED")


_NO_HELLO_BOT_TEMPLATE = """
import sys

# Vanilla Botzone bot — reads from stdin but doesn't speak HELLO. Discards the
# first line (which our runner sends as HELLO) and then plays normally.
sys.stdin.readline()  # consume HELLO line silently

while True:
    line = sys.stdin.readline()
    if not line:
        break
    if line.strip() == ">>>BOTZONE_REQUEST_END<<<":
        sys.stdout.write("PASS\\n")
        sys.stdout.write(">>>BOTZONE_RESPONSE_END<<<\\n")
        sys.stdout.flush()
"""


async def test_hello_handshake_skip_vanilla_bot(tmp_path: Path) -> None:
    """Fixture 3: vanilla bot that ignores HELLO still completes a decide."""
    _write_bot_script(tmp_path, _NO_HELLO_BOT_TEMPLATE)
    manifest = _manifest_for(tmp_path, handshake_ms=200)
    adapter = BotRunnerAdapter(manifest)
    try:
        await adapter.seated(_make_ctx())
        assert adapter._handshake_skipped is True
        result = await adapter.decide(_prompt())
        assert result == {"type": "PASS"}
    finally:
        await adapter.left("HAND_ENDED")


async def test_spawn_failure_raises_process_exit(tmp_path: Path) -> None:
    """Fixture 4: a manifest with a non-existent command surfaces as
    SeatError(bot_error='process_exit') at seated() time."""
    manifest = _manifest_for(
        tmp_path,
        command=["/this/path/does/not/exist/bot"],
    )
    adapter = BotRunnerAdapter(manifest)
    with pytest.raises(SeatError) as exc:
        await adapter.seated(_make_ctx())
    assert getattr(exc.value, "bot_error", None) == "process_exit"


_SLEEPY_BOT_TEMPLATE = """
import sys
import time
sys.stdin.readline()  # HELLO
sys.stdout.write('{"kind":"HELLO","bot_id":"b","version":"0","ack_mode":"long_running"}\\n')
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    if line.strip() == ">>>BOTZONE_REQUEST_END<<<":
        time.sleep(3.0)  # exceed any reasonable budget
"""


async def test_per_turn_timeout_kills_subprocess(tmp_path: Path) -> None:
    """Fixture 5: a bot that exceeds budget triggers SeatTimeout and SIGTERM."""
    _write_bot_script(tmp_path, _SLEEPY_BOT_TEMPLATE)
    manifest = _manifest_for(tmp_path, budget_ms=300)
    adapter = BotRunnerAdapter(manifest)
    try:
        await adapter.seated(_make_ctx())
        with pytest.raises(SeatTimeout) as exc:
            await adapter.decide(_prompt())
        assert getattr(exc.value, "bot_error", None) == "read_timeout"
        # Subprocess should be reaped after the kill.
        assert adapter._proc is not None
        assert adapter._proc.returncode is not None
    finally:
        await adapter.left("HAND_ENDED")


_NO_SENTINEL_BOT_TEMPLATE = """
import sys
sys.stdin.readline()
sys.stdout.write('{"kind":"HELLO","bot_id":"b","version":"0","ack_mode":"long_running"}\\n')
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    if line.strip() == ">>>BOTZONE_REQUEST_END<<<":
        sys.stdout.write("PASS\\n")
        sys.stdout.flush()
        # No sentinel — runner should eventually hit EOF when we close stdout.
        sys.stdout.close()
        break
"""


async def test_missing_sentinel_surfaces_framing_error(tmp_path: Path) -> None:
    """Fixture 8: a response with no RESPONSE_END sentinel is a framing error."""
    _write_bot_script(tmp_path, _NO_SENTINEL_BOT_TEMPLATE)
    manifest = _manifest_for(tmp_path, budget_ms=2000)
    adapter = BotRunnerAdapter(manifest)
    try:
        await adapter.seated(_make_ctx())
        with pytest.raises(SeatError) as exc:
            await adapter.decide(_prompt())
        assert getattr(exc.value, "bot_error", None) == "framing_error"
    finally:
        await adapter.left("HAND_ENDED")


_CRLF_BOT_TEMPLATE = """
import sys
sys.stdin.readline()
sys.stdout.write('{"kind":"HELLO","bot_id":"b","version":"0","ack_mode":"long_running"}\\r\\n')
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    if line.strip() == ">>>BOTZONE_REQUEST_END<<<":
        sys.stdout.write("PASS\\r\\n")
        sys.stdout.write(">>>BOTZONE_RESPONSE_END<<<\\r\\n")
        sys.stdout.flush()
"""


async def test_crlf_responses_tolerated(tmp_path: Path) -> None:
    """Fixture 8: CRLF responses are stripped and parsed normally."""
    _write_bot_script(tmp_path, _CRLF_BOT_TEMPLATE)
    manifest = _manifest_for(tmp_path)
    adapter = BotRunnerAdapter(manifest)
    try:
        await adapter.seated(_make_ctx())
        result = await adapter.decide(_prompt())
        assert result == {"type": "PASS"}
    finally:
        await adapter.left("HAND_ENDED")


_GARBAGE_BOT_TEMPLATE = """
import sys
sys.stdin.readline()
sys.stdout.write('{"kind":"HELLO","bot_id":"b","version":"0","ack_mode":"long_running"}\\n')
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    if line.strip() == ">>>BOTZONE_REQUEST_END<<<":
        sys.stdout.write("WAT IS THIS\\n")
        sys.stdout.write(">>>BOTZONE_RESPONSE_END<<<\\n")
        sys.stdout.flush()
"""


async def test_parse_error_surfaces_with_raw_response(tmp_path: Path) -> None:
    """An unparseable response becomes SeatError(bot_error='parse_error') with
    raw_response attached and truncated to 1024 bytes."""
    _write_bot_script(tmp_path, _GARBAGE_BOT_TEMPLATE)
    manifest = _manifest_for(tmp_path)
    adapter = BotRunnerAdapter(manifest)
    try:
        await adapter.seated(_make_ctx())
        with pytest.raises(SeatError) as exc:
            await adapter.decide(_prompt())
        assert getattr(exc.value, "bot_error", None) == "parse_error"
        assert getattr(exc.value, "raw_response", "") == "WAT IS THIS"
    finally:
        await adapter.left("HAND_ENDED")


_ILLEGAL_BOT_TEMPLATE = """
import sys
sys.stdin.readline()
sys.stdout.write('{"kind":"HELLO","bot_id":"b","version":"0","ack_mode":"long_running"}\\n')
sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    if line.strip() == ">>>BOTZONE_REQUEST_END<<<":
        # Syntactically valid PENG, but in a DISCARD prompt the engine will reject it.
        sys.stdout.write("PENG B5\\n")
        sys.stdout.write(">>>BOTZONE_RESPONSE_END<<<\\n")
        sys.stdout.flush()
"""


async def test_illegal_action_parses_normally(tmp_path: Path) -> None:
    """Fixture 9: a parseable-but-illegal action is returned normally; the
    table manager / engine surfaces `illegal: true` on the record event."""
    _write_bot_script(tmp_path, _ILLEGAL_BOT_TEMPLATE)
    manifest = _manifest_for(tmp_path)
    adapter = BotRunnerAdapter(manifest)
    try:
        await adapter.seated(_make_ctx())
        # Prompt is DISCARD, legal_actions doesn't include PENG. Adapter still
        # returns the parsed action; engine-side rejection is out of scope here.
        result = await adapter.decide(_prompt(legal=[PASS_ACTION], default=PASS_ACTION))
        assert result == {"type": "PENG", "tile": "B5"}
    finally:
        await adapter.left("HAND_ENDED")


# --- Teardown sanity ------------------------------------------------------


async def test_left_reaps_subprocess(tmp_path: Path) -> None:
    _write_bot_script(tmp_path, _SDK_BOT_TEMPLATE)
    adapter = BotRunnerAdapter(_manifest_for(tmp_path))
    await adapter.seated(_make_ctx())
    await adapter.left("HAND_ENDED")
    assert adapter._proc is not None
    assert adapter._proc.returncode is not None


async def test_left_is_safe_to_call_without_seated(tmp_path: Path) -> None:
    adapter = BotRunnerAdapter(_manifest_for(tmp_path))
    # No subprocess ever started — should not raise.
    await adapter.left("HAND_ENDED")


# --- End-to-end gate ------------------------------------------------------


async def test_bot_runner_plays_a_hand_against_canned(tmp_path: Path) -> None:
    """Gate: one BotRunnerAdapter + three CannedAdapters complete a hand via
    `mahjong.table.manager.run_hand`. Asserts terminal state is reached and a
    record is written; byte-stability is owned by the S0 fixture, not here."""
    from mahjong.adapters.canned import CannedAdapter
    from mahjong.engine.rulesets import MANIFEST
    from mahjong.records.reader import read_record
    from mahjong.table import manager as mgr

    _write_bot_script(tmp_path, _SDK_BOT_TEMPLATE)
    bot = BotRunnerAdapter(_manifest_for(tmp_path, budget_ms=5000, handshake_ms=2000))
    cans = [
        CannedAdapter(identity={"kind": "canned", "script": f"pass_{i}"}, actions=[])
        for i in range(3)
    ]

    record_path = tmp_path / "hand.jsonl"
    final = await mgr.run_hand(
        adapters=[bot, *cans],
        ruleset={"id": "mcr-2006", "version": 1, "config_hash": MANIFEST["mcr-2006"]},
        seed=12345,
        hand_id="test-bot-runner-e2e",
        record_path=record_path,
        server_info={"name": "test", "version": "0.0.0"},
        decide_timeout_seconds=30.0,
        observe_timeout_seconds=2.0,
        seated_timeout_seconds=5.0,
    )

    assert final["phase"] == "TERMINAL"
    events = read_record(record_path)
    assert events[-1]["event"] == "FOOTER"
