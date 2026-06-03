"""MetricsSampler — CPU% and resident memory of the supervised process.

Spec: docs/specs/admin-console.md § 2 (metrics sampler).

``psutil.Process.cpu_percent(interval=None)`` is *stateful*: it reports usage
since the previous call on the same ``Process`` object, so the first call always
returns 0.0 and the object must be reused across samples.  The sampler caches the
``Process`` per PID and rebuilds it when the PID changes (e.g. after a restart).
A background task refreshes ``latest`` on a timer; the control plane reads
``latest`` when assembling each ``STATUS`` snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import Callable

import psutil

PidProvider = Callable[[], "int | None"]


@dataclasses.dataclass(frozen=True)
class Metrics:
    cpu_pct: float
    mem_rss_bytes: int

    def to_wire(self) -> dict[str, float | int]:
        return {"cpu_pct": self.cpu_pct, "mem_rss_bytes": self.mem_rss_bytes}


class MetricsSampler:
    def __init__(self, *, pid_provider: PidProvider, interval_s: float = 2.0) -> None:
        self._pid_provider = pid_provider
        self._interval_s = interval_s
        self._cached: tuple[int, psutil.Process] | None = None
        self._latest: Metrics | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def latest(self) -> Metrics | None:
        return self._latest

    def _process_for(self, pid: int) -> psutil.Process:
        if self._cached is not None and self._cached[0] == pid:
            return self._cached[1]
        proc = psutil.Process(pid)
        proc.cpu_percent(interval=None)  # prime the per-object baseline
        self._cached = (pid, proc)
        return proc

    def sample_once(self) -> Metrics | None:
        """Take one reading; updates and returns ``latest`` (None if no live PID)."""
        pid = self._pid_provider()
        if pid is None:
            self._latest = None
            self._cached = None
            return None
        try:
            proc = self._process_for(pid)
            cpu = proc.cpu_percent(interval=None)
            rss = proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            self._latest = None
            self._cached = None
            return None
        self._latest = Metrics(cpu_pct=round(float(cpu), 1), mem_rss_bytes=int(rss))
        return self._latest

    async def _loop(self) -> None:
        while True:
            self.sample_once()
            await asyncio.sleep(self._interval_s)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


__all__ = ["Metrics", "MetricsSampler", "PidProvider"]
