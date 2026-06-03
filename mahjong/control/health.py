"""HealthMonitor — DB integrity + storage headroom for the console.

Spec: docs/specs/admin-console.md § 2 (health & storage), step 11.

``disk_free`` and the WAL size are cheap to read every STATUS tick.  The SQLite
``PRAGMA integrity_check`` scans the database, so it is cached (default 60s TTL)
rather than run on every 2-second status push.  All reads happen in the executor
(the persistence connection is configured for cross-thread use).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any, Protocol


class _PersistenceLike(Protocol):
    def integrity_check(self) -> Any: ...


class HealthMonitor:
    def __init__(
        self,
        *,
        persistence: _PersistenceLike,
        db_path: str | Path,
        integrity_ttl_s: float = 60.0,
    ) -> None:
        self._p = persistence
        self._db_path = Path(db_path)
        self._ttl = integrity_ttl_s
        self._integrity_cache: tuple[float, bool] | None = None

    def _wal_bytes(self) -> int:
        wal = Path(str(self._db_path) + "-wal")
        return wal.stat().st_size if wal.exists() else 0

    def _disk_free(self) -> int:
        target = self._db_path.parent if self._db_path.parent.exists() else Path(".")
        return int(shutil.disk_usage(target).free)

    def _integrity_ok(self) -> bool:
        now = time.monotonic()
        if self._integrity_cache is not None and now - self._integrity_cache[0] < self._ttl:
            return self._integrity_cache[1]
        ok = bool(self._p.integrity_check().pragma_ok)
        self._integrity_cache = (now, ok)
        return ok

    def _snapshot_blocking(self) -> dict[str, Any]:
        return {
            "db_integrity_ok": self._integrity_ok(),
            "disk_free_bytes": self._disk_free(),
            "wal_bytes": self._wal_bytes(),
        }

    async def snapshot(self) -> dict[str, Any]:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._snapshot_blocking
        )


__all__ = ["HealthMonitor"]
