"""Record reader: parse a JSONL record, validate sequence integrity + checksum.

Spec: docs/specs/record-format.md § Top-level shape, § FOOTER, § Verification fixtures.

A reader that returns events the writer can re-emit byte-identically (round-trip
identity, verification fixture 1). Refuses to load any record whose footer
checksum, event_count, or seq sequence fails to validate (fixture 5).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_SUPPORTED_FORMAT_VERSIONS = frozenset({1})


class RecordCorruptError(Exception):
    """Raised when a record fails any structural integrity check."""


def read_record(path: Path) -> list[dict[str, Any]]:
    """Parse `path` into a list of event dicts, validating integrity.

    Raises `RecordCorruptError` on: malformed JSON, missing/extra HEADER or
    FOOTER, non-monotonic `seq`, `event_count` mismatch, checksum mismatch,
    unsupported `format_version`.
    """
    raw = path.read_bytes()
    if not raw:
        raise RecordCorruptError("empty record")

    lines = raw.splitlines(keepends=True)
    events: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordCorruptError(f"line {idx}: malformed JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise RecordCorruptError(f"line {idx}: event is not an object")
        events.append(event)

    if events[0].get("event") != "HEADER":
        raise RecordCorruptError("first event must be HEADER")
    if events[-1].get("event") != "FOOTER":
        raise RecordCorruptError("last event must be FOOTER")

    header = events[0]
    fmt = header.get("format_version")
    if fmt not in _SUPPORTED_FORMAT_VERSIONS:
        raise RecordCorruptError(f"unsupported format_version: {fmt!r}")

    for i, event in enumerate(events):
        if event.get("seq") != i:
            raise RecordCorruptError(
                f"seq mismatch at index {i}: got {event.get('seq')!r}, expected {i}"
            )

    footer = events[-1]
    if footer.get("event_count") != len(events):
        raise RecordCorruptError(
            f"event_count mismatch: footer says {footer.get('event_count')!r}, "
            f"file has {len(events)} lines"
        )

    h = hashlib.sha256()
    for line in lines[:-1]:
        h.update(line)
    expected_checksum = "sha256:" + h.hexdigest()
    if footer.get("checksum") != expected_checksum:
        raise RecordCorruptError(
            f"checksum mismatch: footer says {footer.get('checksum')!r}, "
            f"recomputed {expected_checksum!r}"
        )

    return events


__all__ = ["RecordCorruptError", "read_record"]
