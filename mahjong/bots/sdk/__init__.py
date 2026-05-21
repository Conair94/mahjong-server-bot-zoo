"""Bundled Python SDK for writing bots that target our runner.

Spec: docs/specs/bot-runner-protocol.md § Wire framing, § Startup handshake.

A bot author writes:

    from mahjong.bots.sdk import run_bot

    def decide(request: dict) -> str:
        # request is the parsed history payload from the runner.
        return "PASS"

    if __name__ == "__main__":
        run_bot(decide, bot_id="my_bot", version="0.1.0")

`run_bot` handles HELLO + sentinel framing so the bot only thinks in terms of
"parsed request in, action string out."

The default serializer used by `BotRunnerAdapter` in Step 5.2 emits JSON
request bodies that `parse_request` here understands. Step 5.3 swaps in the
Botzone CSM typed-line format; bots that want to target Botzone directly
(without our SDK) read raw stdin lines instead.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

REQUEST_END_SENTINEL = ">>>BOTZONE_REQUEST_END<<<"
RESPONSE_END_SENTINEL = ">>>BOTZONE_RESPONSE_END<<<"


def _read_until_sentinel(stream: TextIO, sentinel: str) -> list[str]:
    """Read lines from stream until a line equal to `sentinel`.

    Returns the lines before the sentinel (sentinel line itself excluded).
    Raises EOFError if the stream closes before the sentinel arrives.
    """
    lines: list[str] = []
    while True:
        line = stream.readline()
        if line == "":
            raise EOFError(f"stream closed before {sentinel!r}")
        line = line.rstrip("\r\n")
        if line == sentinel:
            return lines
        lines.append(line)


def parse_request(lines: list[str]) -> dict[str, Any]:
    """Parse the default JSON request body produced by BotRunnerAdapter.

    The body is a single JSON object on one line, followed by zero or more
    blank lines and the REQUEST_END sentinel. Bots that target the Botzone
    typed-line format (Step 5.3) should bypass this helper.
    """
    payload = next((line for line in lines if line.strip()), "")
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload}
    if not isinstance(parsed, dict):
        return {"raw": payload}
    return parsed


def run_bot(
    decide: Callable[[dict[str, Any]], str],
    *,
    bot_id: str,
    version: str,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Main loop for SDK-based bots.

    Reads the HELLO line, sends a HELLO ack, then loops: read request until
    REQUEST_END, parse JSON, call `decide(request)`, write response + sentinel.

    `stdin`/`stdout` overrides are for testing; bots normally rely on the
    process defaults.
    """
    sin = stdin or sys.stdin
    sout = stdout or sys.stdout

    # --- HELLO handshake (bot-runner-protocol.md § Startup handshake) ---
    hello_line = sin.readline()
    if hello_line:
        try:
            hello = json.loads(hello_line)
        except json.JSONDecodeError:
            hello = {}
        ack = {
            "kind": "HELLO",
            "bot_id": bot_id,
            "version": version,
            "ack_mode": hello.get("mode", "long_running"),
        }
        sout.write(json.dumps(ack) + "\n")
        sout.flush()

    # --- Per-turn loop ---
    while True:
        try:
            lines = _read_until_sentinel(sin, REQUEST_END_SENTINEL)
        except EOFError:
            return
        request = parse_request(lines)
        try:
            action = decide(request)
        except Exception as e:  # pragma: no cover - bot author's bug surfaces
            sys.stderr.write(f"bot decide raised: {e!r}\n")
            sys.stderr.flush()
            return
        sout.write(action.rstrip("\r\n") + "\n")
        sout.write(RESPONSE_END_SENTINEL + "\n")
        sout.flush()


__all__ = [
    "REQUEST_END_SENTINEL",
    "RESPONSE_END_SENTINEL",
    "parse_request",
    "run_bot",
]
