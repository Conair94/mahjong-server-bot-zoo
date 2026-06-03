"""LogRingBuffer — a bounded, line-numbered tail of the server's output.

Spec: docs/specs/admin-console.md § 2 (log ring buffer).

The supervised ``serve`` child can produce unbounded output; the console only
needs a recent window plus a cursor so the live-tail WS can resume from where a
client left off.  A ``deque(maxlen=N)`` gives O(1) append + automatic eviction;
monotonic line numbers survive eviction so cursors stay meaningful.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Iterator


@dataclasses.dataclass(frozen=True)
class LogLine:
    line: int  # 1-based, monotonic, never reused (survives eviction)
    text: str
    stream: str  # "stdout" | "stderr"

    def to_wire(self) -> dict[str, object]:
        return {"line": self.line, "text": self.text, "stream": self.stream}


class LogRingBuffer:
    def __init__(self, maxlen: int = 2000) -> None:
        self._buf: deque[LogLine] = deque(maxlen=maxlen)
        self._next_line = 1

    def append(self, text: str, stream: str) -> LogLine:
        entry = LogLine(line=self._next_line, text=text, stream=stream)
        self._next_line += 1
        self._buf.append(entry)
        return entry

    def recent(self, *, limit: int | None = None) -> list[LogLine]:
        """The retained lines in order; the last *limit* if given."""
        if limit is None or limit >= len(self._buf):
            return list(self._buf)
        return list(self._buf)[-limit:]

    def since(self, cursor: int) -> list[LogLine]:
        """Retained lines with ``line > cursor`` (what a subscriber hasn't seen)."""
        return [ln for ln in self._buf if ln.line > cursor]

    @property
    def last_line(self) -> int:
        """The highest line number assigned so far (0 if nothing logged)."""
        return self._next_line - 1

    def __len__(self) -> int:
        return len(self._buf)

    def __iter__(self) -> Iterator[LogLine]:
        return iter(self._buf)


__all__ = ["LogLine", "LogRingBuffer"]
