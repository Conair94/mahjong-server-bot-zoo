"""SQLite connection factory.

Opens (or creates) a SQLite DB file and configures it with the PRAGMAs
required by docs/specs/sqlite-schema.md § Database file and connection:

- WAL journal mode (concurrent readers + single writer).
- foreign_keys = ON (must be set per-connection in SQLite).
- busy_timeout = 5000 ms (retry briefly before raising on contention).
- synchronous = NORMAL (default for WAL; safe for our durability model).

Call ``apply_migrations()`` after ``open_db()`` to ensure the schema is current.
"""

from __future__ import annotations

import os
import sqlite3


def open_db(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open (or create) the SQLite DB at *path* with all required PRAGMAs set.

    The caller is responsible for calling ``apply_migrations(conn)`` and for
    closing the connection when done (or using it as a context manager).
    """
    # check_same_thread=False: the async server calls argon2-heavy auth flows
    # via run_in_executor, which crosses thread boundaries. With WAL + a single
    # process and python's GIL serialising stmt execution, this is safe at our
    # scale. Higher-throughput multi-table workloads would want a per-thread
    # connection pool; documented as a deferral in server-lifecycle.md.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # named-column access for free
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn
