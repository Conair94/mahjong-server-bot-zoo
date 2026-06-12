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


def _record(
    msg: str, level: int = logging.INFO, **extra: object
) -> logging.LogRecord:
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
    line = JsonLogFormatter().format(
        _record("sessions.cleanup", deleted=3, table_id="t1")
    )
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


# --- DEF-20: _setup_logging persists to a rotating file ----------------------


def test_setup_logging_writes_json_lines_to_log_file(tmp_path) -> None:
    """DEF-20: serve must tee logs to ``cfg.log_file`` (rotating) so crash /
    stall evidence survives the terminal. stdout behavior is unchanged."""
    import logging

    from mahjong.cli.serve import _setup_logging
    from mahjong.server.config import load_config_from_env

    cfg, _ = load_config_from_env(env={"MAHJONG_DATA_DIR": str(tmp_path)})
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        _setup_logging(cfg)
        logging.getLogger("mahjong.test").info("def20_probe table=%s", "t1")
        for h in root.handlers:
            h.flush()
        text = (tmp_path / "logs" / "server.log").read_text()
        assert "def20_probe table=t1" in text
        assert '"event"' in text  # the JSON formatter, same as stdout
    finally:
        for h in root.handlers[:]:
            if h not in saved_handlers:
                h.close()
                root.removeHandler(h)
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
