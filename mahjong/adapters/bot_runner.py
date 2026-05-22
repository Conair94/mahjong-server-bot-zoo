"""BotRunnerAdapter: subprocess-backed seat adapter.

Spec: docs/specs/bot-runner-protocol.md § Process lifecycle, § Wire framing,
      § Time-budget enforcement, § Error surfacing into records.

Lifecycle (long-running, the default):

    seated  -> spawn subprocess -> HELLO handshake (skipable for vanilla bots)
    observe -> append event to internal history buffer
    decide  -> serialize history -> write request + sentinel ->
               read response under deadline -> parse action -> return
    left    -> SIGTERM -> grace -> SIGKILL -> reap

Failure modes produce SeatTimeout / SeatError with payload attributes
(`bot_error`, `exit_code`, `raw_response`, `bytes_read`) per the spec's
error-surfacing table. The table manager reads them via getattr and stamps
them onto the record event; no new exception subclass is needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Literal, Protocol, cast

from mahjong.adapters.base import (
    BotIdentity,
    LeaveReason,
    Prompt,
    SeatContext,
    SeatError,
    SeatTimeout,
)
from mahjong.bots.manifest import BotManifest
from mahjong.bots.sandbox import build_env, build_rlimits
from mahjong.bots.sdk import REQUEST_END_SENTINEL, RESPONSE_END_SENTINEL
from mahjong.engine.types import Action, SeatView

logger = logging.getLogger(__name__)

# --- Constants -------------------------------------------------------------

_RAW_RESPONSE_CAP = 1024  # bot-runner-protocol.md § Error surfacing.
_ACK_MODE_VALUES = ("long_running", "short_running")

AckMode = Literal["long_running", "short_running"]


class HistorySerializer(Protocol):
    """Per-seat stateful serializer used by `BotRunnerAdapter`.

    Implementations accumulate observe events between decides and emit a
    wire payload for the bot on each decide. After the bot replies, the
    adapter calls `record_response` with the parsed action so the next
    payload can reflect it.

    Two implementations ship with the project:
      - `JsonHistorySerializer` (Step 5.2 default) — opaque JSON-per-decide.
      - `mahjong.bots.botzone_serializer.BotzoneCsmSerializer` (Step 5.3) —
        Botzone CSM `{"requests":[...], "responses":[...]}` envelope.
    """

    def on_observe(self, event: dict[str, Any], view: dict[str, Any]) -> None: ...
    def on_decide(self, prompt: Prompt) -> str: ...
    def record_response(self, action: Action) -> None: ...


class JsonHistorySerializer:
    """Default serializer: emits the prompt's `kind` + `legal_actions` as JSON.

    Used by SDK-based test bots that pick `default_action` and reply. Does
    not maintain a per-seat history view — designed for tests, not for
    judge-faithful bots.
    """

    def __init__(self) -> None:
        self._observed = 0

    def on_observe(self, event: dict[str, Any], view: dict[str, Any]) -> None:
        self._observed += 1

    def on_decide(self, prompt: Prompt) -> str:
        payload = {
            "kind": prompt["kind"],
            "legal_actions": list(prompt["legal_actions"]),
            "default_action": prompt["default_action"],
            "history_len": self._observed,
        }
        return json.dumps(payload)

    def record_response(self, action: Action) -> None:
        return None


# --- Action-string parsing (Botzone CSM grammar) ---------------------------


_NUMBERED_SUITS = {"W", "B", "T"}


def _shift_numbered_tile(tile: str, delta: int) -> str:
    if len(tile) != 2 or tile[0] not in _NUMBERED_SUITS or not tile[1].isdigit():
        raise _ParseError(f"cannot shift non-numbered tile {tile!r}")
    n = int(tile[1]) + delta
    if not 1 <= n <= 9:
        raise _ParseError(f"shifted tile {tile}+{delta} is out of range")
    return f"{tile[0]}{n}"


class _ParseError(Exception):
    """Internal — turned into SeatError with bot_error='parse_error'."""


def parse_action_string(line: str, prompt: Prompt) -> Action:
    """Parse a Botzone action string into an `Action` dict.

    GANG kind is inferred from `prompt.kind`: a GANG declared in CLAIM is
    EXPOSED (claiming the just-discarded tile); a GANG declared in DISCARD is
    CONCEALED (from own concealed hand). BUGANG always maps to ADDED.
    """
    parts = line.strip().split()
    if not parts:
        raise _ParseError("empty action line")
    tag = parts[0]
    if tag == "PASS":
        return cast(Action, {"type": "PASS"})
    if tag == "PLAY":
        if len(parts) != 2:
            raise _ParseError(f"PLAY expects 1 tile, got {parts[1:]!r}")
        return cast(Action, {"type": "PLAY", "tile": parts[1]})
    if tag == "PENG":
        if len(parts) != 2:
            raise _ParseError(f"PENG expects 1 tile, got {parts[1:]!r}")
        return cast(Action, {"type": "PENG", "tile": parts[1]})
    if tag == "CHI":
        # Botzone CHI: <claimed tile> <middle tile>. Reconstruct the run by
        # shifting around the middle (the spec note in § Per-turn request).
        if len(parts) != 3:
            raise _ParseError(f"CHI expects 2 tokens, got {parts[1:]!r}")
        middle = parts[2]
        lo = _shift_numbered_tile(middle, -1)
        hi = _shift_numbered_tile(middle, +1)
        return cast(Action, {"type": "CHI", "tiles": [lo, middle, hi]})
    if tag == "GANG":
        if len(parts) != 2:
            raise _ParseError(f"GANG expects 1 tile, got {parts[1:]!r}")
        kind: Literal["EXPOSED", "CONCEALED"] = (
            "EXPOSED" if prompt["kind"] == "CLAIM" else "CONCEALED"
        )
        return cast(Action, {"type": "GANG", "tile": parts[1], "kind": kind})
    if tag == "BUGANG":
        if len(parts) != 2:
            raise _ParseError(f"BUGANG expects 1 tile, got {parts[1:]!r}")
        return cast(Action, {"type": "GANG", "tile": parts[1], "kind": "ADDED"})
    if tag == "HU":
        return cast(Action, {"type": "HU"})
    raise _ParseError(f"unknown action tag {tag!r}")


# --- Async stream helpers --------------------------------------------------


async def _read_line(reader: asyncio.StreamReader) -> str:
    """Read one LF-terminated line, decode UTF-8, strip CR/LF. Returns ''
    on EOF (the StreamReader's signal)."""
    raw = await reader.readline()
    if not raw:
        return ""
    return raw.decode("utf-8", errors="strict").rstrip("\r\n")


async def _read_until_sentinel(
    reader: asyncio.StreamReader,
    sentinel: str,
) -> tuple[list[str], int]:
    """Read lines until a stripped line equals `sentinel`. Returns the
    accumulated lines (sentinel excluded) and bytes consumed.

    Raises EOFError if the stream closes first. The caller wraps this in
    `asyncio.wait_for` to enforce the per-turn budget.
    """
    lines: list[str] = []
    bytes_consumed = 0
    while True:
        raw = await reader.readline()
        if not raw:
            raise EOFError("stream closed before sentinel")
        bytes_consumed += len(raw)
        line = raw.decode("utf-8", errors="strict").rstrip("\r\n")
        if line == sentinel:
            return lines, bytes_consumed
        lines.append(line)


# --- The adapter -----------------------------------------------------------


class BotRunnerAdapter:
    """Drives a bot subprocess and conforms to `SeatAdapter`.

    One instance per (seat, hand). The subprocess is torn down at hand end
    (`left`); the adapter is not reused across hands.
    """

    identity: BotIdentity

    def __init__(
        self,
        manifest: BotManifest,
        *,
        history_serializer: HistorySerializer | None = None,
        hello_timeout_override_s: float | None = None,
    ) -> None:
        self._manifest = manifest
        self._history_serializer: HistorySerializer = (
            history_serializer if history_serializer is not None else JsonHistorySerializer()
        )
        self._hello_timeout_override_s = hello_timeout_override_s

        self.identity = {
            "kind": "bot",
            "bot_id": manifest.bot_id,
            "version": manifest.version,
            "runtime": "subprocess",
        }

        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._mode: AckMode = "long_running"
        self._handshake_skipped: bool = False

    # --- Lifecycle ---

    async def seated(self, ctx: SeatContext) -> None:
        await self._spawn()
        await self._do_hello(ctx)

    async def observe(self, event: dict[str, Any], view: SeatView) -> None:
        self._history_serializer.on_observe(event, cast(dict[str, Any], view))

    async def decide(self, prompt: Prompt) -> Action:
        # In short-running mode, the subprocess exits after each response;
        # respawn before each decide.
        if self._proc is None or self._proc.returncode is not None:
            await self._spawn()
            if self._mode == "short_running":
                # Short-running re-uses the negotiated mode; no fresh HELLO.
                pass

        budget_s = self._manifest.budget_ms_per_turn / 1000.0
        loop = asyncio.get_event_loop()
        deadline_remaining = max(0.0, prompt["deadline"] - loop.time())
        effective_timeout = (
            min(budget_s, deadline_remaining) if deadline_remaining > 0 else budget_s
        )

        request_body = self._history_serializer.on_decide(prompt)
        await self._write(request_body + "\n" + REQUEST_END_SENTINEL + "\n")

        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            lines, bytes_read = await asyncio.wait_for(
                _read_until_sentinel(self._proc.stdout, RESPONSE_END_SENTINEL),
                timeout=effective_timeout,
            )
        except TimeoutError as e:
            await self._kill()
            timeout_err = SeatTimeout(f"bot {self._manifest.bot_id} timed out reading response")
            timeout_err.bot_error = "read_timeout"  # type: ignore[attr-defined]
            raise timeout_err from e
        except EOFError as e:
            exit_code = await self._reap()
            eof_err = SeatError(
                f"bot {self._manifest.bot_id} stdout closed before sentinel (exit_code={exit_code})"
            )
            eof_err.bot_error = "framing_error"  # type: ignore[attr-defined]
            eof_err.exit_code = exit_code  # type: ignore[attr-defined]
            raise eof_err from e
        except UnicodeDecodeError as e:
            await self._kill()
            decode_err = SeatError(f"bot {self._manifest.bot_id} wrote non-UTF-8 output")
            decode_err.bot_error = "framing_error"  # type: ignore[attr-defined]
            raise decode_err from e

        if self._mode == "short_running":
            await self._teardown()

        action_line = next((line for line in lines if line.strip()), "")
        try:
            action = parse_action_string(action_line, prompt)
        except _ParseError as e:
            err = SeatError(
                f"bot {self._manifest.bot_id} parse error: {e} (bytes_read={bytes_read})"
            )
            err.bot_error = "parse_error"  # type: ignore[attr-defined]
            err.raw_response = action_line[:_RAW_RESPONSE_CAP]  # type: ignore[attr-defined]
            raise err from e
        self._history_serializer.record_response(action)
        return action

    async def left(self, reason: LeaveReason) -> None:
        await self._teardown()

    # --- Subprocess management ---

    async def _spawn(self) -> None:
        rlimits = build_rlimits(self._manifest)

        def preexec() -> None:
            import resource

            for res, lim in rlimits:
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(res, lim)

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._manifest.command,
                *self._manifest.args,
                cwd=str(self._manifest.directory),
                env=build_env(self._manifest),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=preexec,
            )
        except (FileNotFoundError, PermissionError, OSError) as e:
            err = SeatError(f"bot {self._manifest.bot_id} spawn failed: {e}")
            err.bot_error = "process_exit"  # type: ignore[attr-defined]
            err.exit_code = None  # type: ignore[attr-defined]
            raise err from e

        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._proc is not None
        assert self._proc.stderr is not None
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                return
            with contextlib.suppress(UnicodeDecodeError):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                logger.debug("bot:%s:stderr %s", self._manifest.bot_id, line)

    async def _do_hello(self, ctx: SeatContext) -> None:
        hello = {
            "kind": "HELLO",
            "seat": ctx["seat"],
            "wind": f"F{ctx['seat'] + 1}",
            "ruleset": ctx["ruleset"]["id"],
            "format": "botzone-csm",
            "mode": "long_running",
        }
        await self._write(json.dumps(hello) + "\n")

        timeout_s = (
            self._hello_timeout_override_s
            if self._hello_timeout_override_s is not None
            else self._manifest.handshake_deadline_ms / 1000.0
        )
        assert self._proc is not None
        assert self._proc.stdout is not None
        try:
            line = await asyncio.wait_for(_read_line(self._proc.stdout), timeout=timeout_s)
        except TimeoutError:
            # Vanilla Botzone bot that doesn't speak HELLO. Continue with
            # long-running mode; first per-turn read will surface real failures.
            self._handshake_skipped = True
            return

        if not line:
            # Bot exited before HELLO ack — surfaces as a clear seated-time error.
            exit_code = await self._reap()
            err = SeatError(
                f"bot {self._manifest.bot_id} died during HELLO (exit_code={exit_code})"
            )
            err.bot_error = "process_exit"  # type: ignore[attr-defined]
            err.exit_code = exit_code  # type: ignore[attr-defined]
            raise err

        try:
            ack = json.loads(line)
        except json.JSONDecodeError:
            # Treat non-JSON HELLO ack as skip (vanilla bot wrote its first
            # turn line). We can't replay it cleanly; mark skipped and rely on
            # the per-turn loop. Pragmatic, not pretty.
            self._handshake_skipped = True
            return

        ack_mode = ack.get("ack_mode", "long_running")
        if ack_mode not in _ACK_MODE_VALUES:
            ack_mode = "long_running"
        self._mode = ack_mode

        if self._mode == "short_running":
            # Bot exits after first response; reap whatever's there before the
            # first decide spawns a fresh subprocess.
            await self._wait_exit(self._manifest.teardown_grace_ms / 1000.0)

    async def _write(self, text: str) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(text.encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            exit_code = await self._reap()
            err = SeatError(f"bot {self._manifest.bot_id} stdin closed (exit_code={exit_code})")
            err.bot_error = "process_exit"  # type: ignore[attr-defined]
            err.exit_code = exit_code  # type: ignore[attr-defined]
            raise err from e

    async def _kill(self) -> None:
        """SIGTERM, grace, SIGKILL, reap — used on per-turn timeout."""
        if self._proc is None or self._proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            self._proc.terminate()
        grace = self._manifest.teardown_grace_ms / 1000.0
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=grace)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
            with contextlib.suppress(Exception):
                await self._proc.wait()

    async def _teardown(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            await self._kill()
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._stderr_task

    async def _reap(self) -> int | None:
        if self._proc is None:
            return None
        with contextlib.suppress(Exception):
            await self._proc.wait()
        return self._proc.returncode

    async def _wait_exit(self, grace_s: float) -> None:
        if self._proc is None:
            return
        with contextlib.suppress(TimeoutError, Exception):
            await asyncio.wait_for(self._proc.wait(), timeout=grace_s)


__all__ = [
    "AckMode",
    "BotRunnerAdapter",
    "HistorySerializer",
    "JsonHistorySerializer",
    "parse_action_string",
]
