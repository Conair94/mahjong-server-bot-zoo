"""Migration runner for the mahjong-server SQLite schema.

Spec: docs/specs/sqlite-schema.md § Migrations.

Each migration is a Python module exporting:
  ``up(conn: sqlite3.Connection) -> None``   — apply the migration
  ``down(conn: sqlite3.Connection) -> None`` — roll back the migration

The runner reads ``schema_version.version`` to determine the current version
(0 if the table does not exist yet), then applies each migration in order up
to ``target`` (default: latest).  Every migration + its schema_version update
is wrapped in a single transaction — both happen or neither.
"""

from __future__ import annotations

import sqlite3
import time
from types import ModuleType

from mahjong.persistence.migrations import _0001_initial

# Ordered list of migration modules.  Append new migrations here.
_MIGRATIONS: list[ModuleType] = [_0001_initial]

_APPLIED_BY = "mahjong-server-0.0.0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Return the DB's current schema version (0 if schema_version missing)."""
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        return int(row[0]) if row is not None else 0
    except sqlite3.OperationalError:
        return 0  # schema_version table does not exist yet


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_migrations(conn: sqlite3.Connection, target: int | None = None) -> None:
    """Advance the schema from its current version to *target*.

    ``target=None`` (default) means "apply all known migrations" (latest).
    ``target=N`` applies exactly up to version N; useful for rollback tests.

    Each migration is wrapped in a transaction together with its
    ``schema_version`` update — both commit or neither does.

    Calling ``apply_migrations`` on an already-current DB is a no-op.
    """
    current = _get_current_version(conn)
    resolved_target = len(_MIGRATIONS) if target is None else target

    for i in range(current, resolved_target):
        migration = _MIGRATIONS[i]
        version_to = i + 1
        with conn:
            migration.up(conn)
            # Delete-then-insert guarantees exactly one row at all times.
            conn.execute("DELETE FROM schema_version")
            conn.execute(
                "INSERT INTO schema_version (version, applied_at_ms, applied_by) VALUES (?, ?, ?)",
                (version_to, _now_ms(), _APPLIED_BY),
            )


def rollback_migrations(conn: sqlite3.Connection, target: int = 0) -> None:
    """Roll back from the current version down to *target* (default: 0 = empty).

    Best-effort: ``down()`` may not be complete for every migration.
    Intended for testing; not called in production (restore from backup instead).
    """
    current = _get_current_version(conn)

    for i in range(current - 1, target - 1, -1):
        migration = _MIGRATIONS[i]
        version_to = i  # rolling back to version i (0 = empty)
        with conn:
            migration.down(conn)
            conn.execute("DELETE FROM schema_version")
            if version_to > 0:
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at_ms, applied_by) VALUES (?, ?, ?)",
                    (version_to, _now_ms(), _APPLIED_BY),
                )


__all__ = ["apply_migrations", "rollback_migrations"]
