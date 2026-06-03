"""FeedbackInbox — reads the server's on-disk feedback reports.

Spec: docs/specs/admin-console.md § "Feedback inbox".

The game server's ``_handle_feedback`` writes one ``*.txt`` per report under
``data_dir/reports/`` with a small ``key: value`` header terminated by a ``---``
line, then the (sanitised) body.  This service is the read side: it parses those
files back into JSON-ready rows for the console's Feedback pane.

Disk reads are synchronous, so ``list_reports`` runs them in the default executor
— the same sync-IO-off-the-event-loop convention the rest of the control plane
uses (cf. AdminDataService).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

_HEADER_SEP = "\n---\n"
_HEADER_KEYS = ("type", "submitted", "submitter")


def _parse_report(text: str) -> dict[str, str]:
    """Split a report file into header fields + body.

    Tolerant: a file missing the ``---`` separator yields empty header fields and
    the whole content as ``text`` (the pane still shows it; nothing raises)."""
    header_block, sep, body = text.partition(_HEADER_SEP)
    fields = {k: "" for k in _HEADER_KEYS}
    if sep:  # a real header was present
        for line in header_block.splitlines():
            key, colon, value = line.partition(":")
            if colon and key.strip() in fields:
                fields[key.strip()] = value.strip()
    else:  # no separator → treat everything as the body
        body = text
    fields["text"] = body
    return fields


class FeedbackInbox:
    def __init__(self, reports_dir: Path) -> None:
        self._dir = Path(reports_dir)

    async def list_reports(self, limit: int = 200) -> list[dict[str, Any]]:
        return await asyncio.get_running_loop().run_in_executor(None, self._read, limit)

    def _read(self, limit: int) -> list[dict[str, Any]]:
        if not self._dir.is_dir():
            return []
        # Filenames are timestamp-prefixed (YYYYMMDD_HHMMSS_…), so reverse-sorting
        # the names gives newest-first without parsing dates.
        paths = sorted(self._dir.glob("*.txt"), key=lambda p: p.name, reverse=True)
        rows: list[dict[str, Any]] = []
        for path in paths[:limit]:
            try:
                parsed = _parse_report(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            parsed["filename"] = path.name
            rows.append(parsed)
        return rows


__all__ = ["FeedbackInbox"]
