"""Integrity check and DB rebuild from record files.

The record files under ``records/`` are the source of truth.  This module
provides two entry points:

``integrity_check(conn, data_dir)``
    Validates every hand_index row against its record file and vice-versa.

``rebuild_index_from_records(conn, data_dir, dry_run=False)``
    Reconstructs hand_index + hand_participants from scratch by walking
    records/.  Idempotent (uses INSERT OR REPLACE); safe to run on a
    populated DB.

Spec: docs/specs/persistence-api.md § integrity_check, § rebuild path.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from mahjong.persistence.models import (
    IntegrityReport,
    Participant,
    RebuildReport,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_ts_to_ms(ts: str) -> int:
    """Parse an ISO-8601 UTC timestamp string to milliseconds since epoch."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _compute_checksum(content_before_footer: str) -> str:
    """sha256 over all lines *before* the FOOTER line, with trailing newline."""
    return "sha256:" + hashlib.sha256(content_before_footer.encode("utf-8")).hexdigest()


def _recompute_checksum_from_file(path: Path) -> str | None:
    """Read *path* and recompute the checksum from all non-FOOTER lines.

    Returns None if the file cannot be read or has fewer than 2 lines.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        return None
    content_before_footer = "".join(lines[:-1])
    return _compute_checksum(content_before_footer)


def _parse_header_and_events(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """Parse a record file into (header, footer_or_None, middle_events).

    Returns empty structures on parse errors; individual lines that fail
    json.loads are silently skipped from middle_events.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, None, []

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}, None, []

    try:
        header: dict[str, Any] = json.loads(lines[0])
    except json.JSONDecodeError:
        return {}, None, []

    if len(lines) < 2:
        return header, None, []

    # Detect FOOTER on last line
    try:
        last: dict[str, Any] = json.loads(lines[-1])
        footer: dict[str, Any] | None = last if last.get("event") == "FOOTER" else None
    except json.JSONDecodeError:
        footer = None

    middle: list[dict[str, Any]] = []
    middle_lines = lines[1:-1] if footer is not None else lines[1:]
    for ln in middle_lines:
        with contextlib.suppress(json.JSONDecodeError):
            middle.append(json.loads(ln))

    return header, footer, middle


def _participants_from_header(header: dict[str, Any], hand_id: str) -> list[Participant]:
    seats = header.get("seats", [])
    parts: list[Participant] = []
    for s in seats:
        identity = s.get("identity", {})
        kind = identity.get("kind", "canned")
        # Resolve account_id from identity.user_id ("u_<N>" → int N)
        account_id: int | None = None
        user_id = identity.get("user_id")
        if user_id and isinstance(user_id, str) and user_id.startswith("u_"):
            with contextlib.suppress(ValueError):
                account_id = int(user_id[2:])
        parts.append(
            Participant(
                seat=s["seat"],
                account_id=account_id,
                seat_kind=kind if kind in ("human", "bot", "canned") else "canned",
                wind=s.get("wind", f"F{s['seat'] + 1}"),
                final_score_delta=None,
            )
        )
    return parts


def _terminal_from_hand_end(
    events: list[dict[str, Any]],
) -> tuple[str | None, int | None, int | None, dict[int, int]]:
    """Extract (terminal_kind, winner_seat, fan_total, score_deltas) from events.

    Scans *events* for the HAND_END event.  Returns Nones if not found.
    """
    for ev in events:
        if ev.get("event") == "HAND_END":
            kind = ev.get("kind")
            winners = ev.get("winner") or []
            winner_seat = winners[0] if winners and kind == "HU" else None
            fan_total = ev.get("fan_total")
            raw_deltas = ev.get("score_delta", [])
            score_deltas: dict[int, int] = {}
            if isinstance(raw_deltas, list):
                for seat, delta in enumerate(raw_deltas):
                    if delta is not None:
                        score_deltas[seat] = int(delta)
            return kind, winner_seat, fan_total, score_deltas
    return None, None, None, {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def integrity_check(conn: sqlite3.Connection, data_dir: Path) -> IntegrityReport:
    """Validate hand_index rows against record files and vice-versa.

    1. Runs ``PRAGMA integrity_check`` on the DB file.
    2. For each hand_index row: checks file existence + checksum (finalized only).
    3. Walks records/ for .jsonl files not referenced by any hand_index row.

    Returns an :class:`IntegrityReport` with counts.
    """
    # 1. SQLite PRAGMA integrity_check
    pragma_result = conn.execute("PRAGMA integrity_check").fetchone()
    pragma_ok = pragma_result is not None and pragma_result[0] == "ok"

    # 2. Walk DB rows
    rows = conn.execute(
        "SELECT hand_id, record_path, record_checksum, ended_at_ms FROM hand_index"
    ).fetchall()

    checked_db = 0
    ok_files = 0
    missing_files = 0
    checksum_mismatches = 0
    in_progress_hands = 0
    db_record_paths: set[str] = set()

    for row in rows:
        checked_db += 1
        record_path: str = row["record_path"]
        stored_checksum: str | None = row["record_checksum"]
        ended_at_ms: int | None = row["ended_at_ms"]
        db_record_paths.add(record_path)

        if ended_at_ms is None:
            in_progress_hands += 1
            # In-progress hands: skip file-existence check (file may be mid-write)
            continue

        full_path = data_dir / record_path
        if not full_path.exists():
            missing_files += 1
            continue

        if stored_checksum is not None:
            recomputed = _recompute_checksum_from_file(full_path)
            if recomputed != stored_checksum:
                checksum_mismatches += 1
            else:
                ok_files += 1
        else:
            ok_files += 1  # finalized but checksum not stored — just verify existence

    # 3. Walk records/ for orphaned files
    records_dir = data_dir / "records"
    orphaned_files = 0
    if records_dir.exists():
        for jsonl_path in records_dir.rglob("*.jsonl"):
            rel_path = str(jsonl_path.relative_to(data_dir))
            if rel_path not in db_record_paths:
                orphaned_files += 1

    return IntegrityReport(
        pragma_ok=pragma_ok,
        checked_db=checked_db,
        ok_files=ok_files,
        missing_files=missing_files,
        checksum_mismatches=checksum_mismatches,
        orphaned_files=orphaned_files,
        in_progress_hands=in_progress_hands,
    )


def rebuild_index_from_records(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    dry_run: bool = False,
) -> RebuildReport:
    """Reconstruct hand_index + hand_participants by walking records/.

    Uses ``INSERT OR REPLACE`` so it is idempotent — running on a populated DB
    is a no-op (same primary key, same content).

    ``dry_run=True`` walks and counts without writing anything.

    Returns a :class:`RebuildReport`.
    """
    records_dir = data_dir / "records"
    processed = 0
    inserted = 0
    updated = 0
    errors = 0

    if not records_dir.exists():
        return RebuildReport(processed_files=0, inserted=0, updated=0, errors=0)

    for jsonl_path in sorted(records_dir.rglob("*.jsonl")):
        processed += 1
        header, footer, middle_events = _parse_header_and_events(jsonl_path)

        if not header or header.get("event") != "HEADER":
            errors += 1
            continue

        hand_id = header.get("hand_id")
        if not hand_id:
            errors += 1
            continue

        record_path = str(jsonl_path.relative_to(data_dir))
        match_id = header.get("match_id")
        hand_index_in_match = header.get("hand_index_in_match", 0)
        ruleset = header.get("ruleset", {})
        ruleset_id = ruleset.get("id", "mcr-2006")
        ruleset_config_hash = ruleset.get("config_hash", "")
        started_at_ms = _parse_ts_to_ms(header["ts"])
        meta = header.get("meta", {})
        master_seed = str(meta.get("master_seed") or header.get("seed") or "")
        server_version = header.get("server", {}).get("version", "0.0.0")
        source = meta.get("source", "live")
        participants = _participants_from_header(header, hand_id)

        # Determine terminal info
        terminal_kind: str | None = None
        winner_seat: int | None = None
        fan_total: int | None = None
        ended_at_ms: int | None = None
        record_checksum: str | None = None
        score_deltas: dict[int, int] = {}

        if footer is not None:
            ended_at_ms = _parse_ts_to_ms(footer["ts"])
            record_checksum = footer.get("checksum")
            t_kind, t_winner, t_fan, score_deltas = _terminal_from_hand_end(middle_events)
            terminal_kind = t_kind if t_kind is not None else "ABORTED"
            winner_seat = t_winner
            fan_total = t_fan
        else:
            # Crash-truncated: mark ABORTED
            terminal_kind = "ABORTED"

        # Check whether the row already exists in the DB
        existing = conn.execute(
            "SELECT hand_id FROM hand_index WHERE hand_id = ?", (hand_id,)
        ).fetchone()
        is_update = existing is not None

        if not dry_run:
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO hand_index
                            (hand_id, match_id, hand_index_in_match, ruleset_id,
                             ruleset_config_hash, started_at_ms, ended_at_ms,
                             terminal_kind, winner_seat, fan_total, master_seed,
                             record_path, record_checksum, server_version, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            hand_id,
                            match_id,
                            hand_index_in_match,
                            ruleset_id,
                            ruleset_config_hash,
                            started_at_ms,
                            ended_at_ms,
                            terminal_kind,
                            winner_seat,
                            fan_total,
                            master_seed,
                            record_path,
                            record_checksum,
                            server_version,
                            source,
                        ),
                    )
                    # INSERT OR REPLACE on hand_index cascades (deletes old
                    # hand_participants rows due to the FK + REPLACE semantics),
                    # so we always re-insert participants.
                    for p in participants:
                        delta = score_deltas.get(p.seat)
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO hand_participants
                                (hand_id, seat, account_id, seat_kind, wind, final_score_delta)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (hand_id, p.seat, p.account_id, p.seat_kind, p.wind, delta),
                        )
            except Exception:
                errors += 1
                continue

        if is_update:
            updated += 1
        else:
            inserted += 1

    return RebuildReport(
        processed_files=processed,
        inserted=inserted,
        updated=updated,
        errors=errors,
    )


__all__ = ["integrity_check", "rebuild_index_from_records"]
