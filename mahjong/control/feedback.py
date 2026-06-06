"""FeedbackInbox + FeedbackStatusStore — read and triage the server's feedback reports.

Spec: docs/specs/admin-console.md § "Feedback inbox" and
docs/specs/feedback-tracking.md (Spec 30).

The game server's ``_handle_feedback`` writes one ``*.txt`` per report under
``data_dir/reports/`` with a small ``key: value`` header terminated by a ``---``
line, then the (sanitised) body.  Those files are an immutable audit record of the
player's words.

``FeedbackStatusStore`` adds a *triage status* overlay in a sidecar JSON file
(``data_dir/reports/status.json``) keyed by report filename — so we can mark a report
``triaged`` / ``implemented`` / ``wontfix`` etc. without touching the report itself.
``FeedbackInbox.list_reports`` merges that status into each row, and ``update_status``
is the write side the admin console calls.

Disk reads/writes are synchronous, so the async methods run them in the default executor
— the same sync-IO-off-the-event-loop convention the rest of the control plane uses.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
from pathlib import Path
from typing import Any

_HEADER_SEP = "\n---\n"
_HEADER_KEYS = ("type", "submitted", "submitter")

# Spec 30 § 30.1 — the fixed status vocabulary (shared with feedback-backlog.md).
VALID_STATUSES = frozenset(
    {"open", "triaged", "in-progress", "implemented", "verified", "wontfix", "duplicate"}
)
_BACKLOG_ID_RE = re.compile(r"^FB-\d{2,}$")
# Status notes reuse the report sanitisation boundary (Spec 23 § 23.1) minus the
# min-length rule — a status note may legitimately be blank.
_NOTE_DISALLOWED = re.compile(r"[^A-Za-z0-9 .,!?'\-]")
_NOTE_MAX = 200


def _sanitise_note(text: str) -> str:
    cleaned = _NOTE_DISALLOWED.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:_NOTE_MAX]


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


class FeedbackStatusStore:
    """Triage-status overlay (``status.json``) keyed by report filename.

    Pure synchronous disk IO; the async layer (FeedbackInbox) wraps it in an executor.
    Validates against ``VALID_STATUSES`` so the store can never drift to free-text
    statuses, and writes atomically (temp + ``os.replace``) so a crash mid-write can't
    corrupt the map.
    """

    VALID_STATUSES = VALID_STATUSES

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, dict[str, Any]]:
        """Whole map, ``{filename: entry}``.  Missing or corrupt file → ``{}``."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def set(
        self,
        filename: str,
        status: str,
        *,
        backlog_id: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Validate and persist one report's status; return the stored entry.

        Raises ``ValueError`` (before any write) on an out-of-vocabulary status or a
        malformed ``backlog_id`` — so a rejected update leaves the sidecar untouched."""
        if status not in self.VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        if backlog_id is not None and not _BACKLOG_ID_RE.match(backlog_id):
            raise ValueError(f"invalid backlog_id {backlog_id!r}")

        entry: dict[str, Any] = {
            "status": status,
            "backlog_id": backlog_id or "",
            "note": _sanitise_note(note) if note else "",
            "updated": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        }
        data = self.load()
        data[filename] = entry
        self._atomic_write(data)
        return entry

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)


class FeedbackInbox:
    def __init__(self, reports_dir: Path) -> None:
        self._dir = Path(reports_dir)
        self._status = FeedbackStatusStore(self._dir / "status.json")

    async def list_reports(self, limit: int = 200) -> list[dict[str, Any]]:
        return await asyncio.get_running_loop().run_in_executor(None, self._read, limit)

    async def update_status(
        self,
        filename: str,
        status: str,
        *,
        backlog_id: str | None = None,
        note: str | None = None,
    ) -> list[dict[str, Any]]:
        """Set a report's triage status; return the refreshed listing.

        Raises ``KeyError`` if ``filename`` is not a known report (also the
        path-injection guard) and ``ValueError`` on bad status/backlog_id."""
        return await asyncio.get_running_loop().run_in_executor(
            None, self._update_status, filename, status, backlog_id, note
        )

    def _update_status(
        self, filename: str, status: str, backlog_id: str | None, note: str | None
    ) -> list[dict[str, Any]]:
        self._require_known_report(filename)
        self._status.set(filename, status, backlog_id=backlog_id, note=note)
        return self._read(200)

    def _require_known_report(self, filename: str) -> None:
        # `filename` is a join key, never used to build a path directly — but guard
        # path separators and confirm the report exists so status can't be attached
        # to arbitrary keys.
        if "/" in filename or "\\" in filename or not filename.endswith(".txt"):
            raise KeyError(filename)
        if not (self._dir / filename).is_file():
            raise KeyError(filename)

    def _read(self, limit: int) -> list[dict[str, Any]]:
        if not self._dir.is_dir():
            return []
        status_map = self._status.load()
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
            st = status_map.get(path.name)
            parsed["status"] = st.get("status", "open") if st else "open"
            parsed["backlog_id"] = st.get("backlog_id", "") if st else ""
            parsed["note"] = st.get("note", "") if st else ""
            parsed["updated"] = st.get("updated", "") if st else ""
            rows.append(parsed)
        return rows


__all__ = ["VALID_STATUSES", "FeedbackInbox", "FeedbackStatusStore"]
