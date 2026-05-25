"""Persistence layer for the mahjong server.

Public API:
  - ``open_db(path)`` — open (or create) the SQLite DB with all PRAGMAs set.
  - ``apply_migrations(conn, target=None)`` — advance the schema to the
    latest (or a specific) migration version.

The record file is the source of truth for the *contents* of a hand; SQLite
is the source of truth for *finding* a hand and *who played it*.
See docs/specs/sqlite-schema.md for the full spec.
"""

from mahjong.persistence.db import open_db
from mahjong.persistence.migrations import apply_migrations

__all__ = ["apply_migrations", "open_db"]
