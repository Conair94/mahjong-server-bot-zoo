"""Step 8.8.e — structured JSON logging formatter.

server-lifecycle.md § Logging + fixture 21: with ``MAHJONG_LOG_FORMAT=json``
every emitted line is a single JSON object carrying at least ``ts`` (ISO-8601
UTC with ms), ``level``, and ``event``; ``console`` switches to a plain
human-readable line for dev.
"""

from __future__ import annotations

import json
import logging
import re

import pytest

from mahjong.server.logconfig import (
    ConsoleLogFormatter,
    JsonLogFormatter,
    make_formatter,
)


def _record(msg: str, level: int = logging.INFO, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord(
        name="mahjong.serve",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_json_formatter_emits_required_keys() -> None:
    line = JsonLogFormatter().format(_record("server.ready"))
    payload = json.loads(line)  # must be valid JSON
    assert payload["event"] == "server.ready"
    assert payload["level"] == "INFO"
    # ts is ISO-8601 UTC with millisecond precision and a trailing Z.
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", payload["ts"])


def test_json_formatter_merges_structured_extra() -> None:
    line = JsonLogFormatter().format(_record("sessions.cleanup", deleted=3, table_id="t1"))
    payload = json.loads(line)
    assert payload["deleted"] == 3
    assert payload["table_id"] == "t1"
    # Standard LogRecord bookkeeping attrs must not leak into the object.
    assert "args" not in payload
    assert "pathname" not in payload
    assert "msg" not in payload


def test_json_formatter_renders_percent_args() -> None:
    rec = logging.LogRecord(
        name="mahjong.serve",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="startup.in_progress_aborted count=%d",
        args=(2,),
        exc_info=None,
    )
    payload = json.loads(JsonLogFormatter().format(rec))
    assert payload["event"] == "startup.in_progress_aborted count=2"
    assert payload["level"] == "WARNING"


def test_json_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        rec = _record("db.error", level=logging.ERROR)
        import sys

        rec.exc_info = sys.exc_info()
    payload = json.loads(JsonLogFormatter().format(rec))
    assert "ValueError: boom" in payload["exc"]


def test_json_formatter_output_is_single_line() -> None:
    # journald / log shippers split on newline; one record = one line.
    line = JsonLogFormatter().format(_record("server.ready", note="a\nb"))
    assert "\n" not in line
    assert json.loads(line)["note"] == "a\nb"


def test_make_formatter_selects_by_name() -> None:
    assert isinstance(make_formatter("json"), JsonLogFormatter)
    assert isinstance(make_formatter("console"), ConsoleLogFormatter)
    # Unknown formats fall back to JSON (production-safe default).
    assert isinstance(make_formatter("nonsense"), JsonLogFormatter)


def test_console_formatter_is_plain_text_not_json() -> None:
    line = ConsoleLogFormatter().format(_record("server.ready"))
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)
    assert "server.ready" in line
    assert "INFO" in line
