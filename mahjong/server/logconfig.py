"""Logging formatters for the server.  Spec: server-lifecycle.md § Logging.

Two formats, selected by ``MAHJONG_LOG_FORMAT``:

- ``json`` (default / production): one JSON object per line on stdout, with
  fixed top-level keys ``ts`` (ISO-8601 UTC, ms), ``level``, ``event``.  Any
  structured ``extra=`` kwargs on the log call are merged in as additional
  keys.  Since DEF-20 the same JSON lines are also teed to a rotating file
  (``MAHJONG_LOG_FILE``; see ``cli/serve._setup_logging``) so post-mortems
  survive the terminal.
- ``console`` (dev): a plain ``%(asctime)s %(levelname)s %(name)s %(message)s``
  line — easier to read in a terminal.

Secrets never reach a log call (no password hashes, no session tokens), so the
formatter does no redaction of its own; it just serialises whatever the call
site passed.  Fixture 21 asserts the no-secrets invariant against real output.
"""

from __future__ import annotations

import datetime
import json
import logging

# LogRecord attributes that are framework bookkeeping, not caller-supplied
# structured fields.  Everything else in ``record.__dict__`` came from an
# ``extra=`` kwarg and belongs in the JSON object.
_STANDARD_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)

# Top-level keys we set ourselves; a clashing extra= must not overwrite them.
_RESERVED_KEYS: frozenset[str] = frozenset({"ts", "level", "event"})


def _iso_utc_ms(created: float) -> str:
    """ISO-8601 UTC timestamp with millisecond precision and a trailing Z."""
    dt = datetime.datetime.fromtimestamp(created, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class JsonLogFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _iso_utc_ms(record.created),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key in _RESERVED_KEYS:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # default=str keeps non-JSON-serialisable extras (e.g. Path) from
        # crashing a log call; ensure_ascii=False keeps unicode readable.
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleLogFormatter(logging.Formatter):
    """Plain human-readable line for local dev sessions."""

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)s %(name)s %(message)s")


def make_formatter(log_format: str) -> logging.Formatter:
    """Pick a formatter by name; unknown names fall back to JSON (prod-safe)."""
    if log_format.lower() == "console":
        return ConsoleLogFormatter()
    return JsonLogFormatter()


__all__ = ["ConsoleLogFormatter", "JsonLogFormatter", "make_formatter"]
