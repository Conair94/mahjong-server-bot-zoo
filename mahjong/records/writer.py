"""Append-only JSONL record writer.

Spec: docs/specs/record-format.md § File layout, § Top-level shape, § FOOTER.

Design notes:
- Canonical serialization (sorted keys, compact separators, LF) so two writers
  given the same inputs produce byte-identical files on any platform. This is
  the contract behind verification fixture 1 (round-trip identity).
- Incremental sha256 over each written line; the footer's `checksum` is the
  digest of every line *except* the footer itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, BinaryIO

_REQUIRED_EVENT_FIELDS = ("event", "turn_index", "phase", "ts")


def canonical_jsonl_line(payload: dict[str, Any]) -> bytes:
    """Serialize `payload` to one canonical JSONL line (sorted keys, LF terminator)."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8") + b"\n"


class RecordWriter:
    """Open-on-construct, write events in order, close with footer."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: BinaryIO = path.open("wb")
        self._seq = 0
        self._hash = hashlib.sha256()
        self._closed = False

    def write_event(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("writer is closed; cannot write further events")
        for field in _REQUIRED_EVENT_FIELDS:
            if field not in payload:
                raise ValueError(f"event payload missing required field: {field!r}")
        if "seq" in payload:
            raise ValueError("seq is assigned by the writer; remove it from payload")

        full = {**payload, "seq": self._seq}
        line = canonical_jsonl_line(full)
        self._fh.write(line)
        self._hash.update(line)
        self._seq += 1

    def close_with_footer(
        self,
        *,
        turn_index: int,
        phase: str,
        ts: str,
        rng_cursor_final: int,
        state_hash_final: str,
        corrects: str | None,
    ) -> None:
        if self._closed:
            raise RuntimeError("writer already closed")

        checksum = "sha256:" + self._hash.hexdigest()
        footer = {
            "event": "FOOTER",
            "seq": self._seq,
            "turn_index": turn_index,
            "phase": phase,
            "ts": ts,
            "event_count": self._seq + 1,
            "rng_cursor_final": rng_cursor_final,
            "state_hash_final": state_hash_final,
            "checksum": checksum,
            "corrects": corrects,
        }
        self._fh.write(canonical_jsonl_line(footer))
        self._fh.close()
        self._closed = True

    @property
    def path(self) -> Path:
        return self._path

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def closed(self) -> bool:
        return self._closed


__all__ = ["RecordWriter", "canonical_jsonl_line"]
